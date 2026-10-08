"""Supervised agentic investigation of status changes, with Claude Code as the agent runtime.

Boundaries:
- Context: the agent sees only the anomalies of one run plus their history,
  not the whole portfolio.
- Tools: two read-only CLI commands (probe, history). No web access, no file writes,
  and user-level Claude Code settings/MCP servers are not loaded.
- Output: a JSON schema enforced by Claude Code, then validated again here,
  because the rest of the system must not depend on model output being well-formed.
- Autonomy: this module only runs after a human approves it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from .history import History, Run, app_key

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_FILE = REPO_ROOT / ".claude" / "skills" / "investigate-store-anomaly" / "SKILL.md"

CLASSIFICATIONS = ["REMOVED_GLOBALLY", "REGIONAL_UNAVAILABILITY", "RECOVERED",
                   "TRANSIENT_SIGNAL", "PERSISTENT_CONFLICT", "INCONCLUSIVE"]
CONFIDENCE = ["low", "medium", "high"]
EVIDENCE_SOURCES = ["case", "probe", "history"]

CONCLUSION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "investigations"],
    "properties": {
        "summary": {"type": "string"},
        "investigations": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["app_id", "country", "label", "classification", "evidence",
                         "likely_explanation", "confidence", "remaining_uncertainty",
                         "recommended_human_action"],
            "properties": {
                "app_id": {"type": "string"},
                "country": {"type": "string"},
                "label": {"type": "string"},
                "classification": {"type": "string", "enum": CLASSIFICATIONS},
                "evidence": {"type": "array", "minItems": 1, "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "observation"],
                    "properties": {"source": {"type": "string", "enum": EVIDENCE_SOURCES},
                                   "observation": {"type": "string"}}}},
                "likely_explanation": {"type": "string"},
                "confidence": {"type": "string", "enum": CONFIDENCE},
                "remaining_uncertainty": {"type": "string"},
                "recommended_human_action": {"type": "string"},
            }}},
    },
}


# --- case: the agent's context -------------------------------------------------

def tool_commands(state_dir: Path) -> dict[str, str]:
    sd = "" if state_dir.resolve() == (REPO_ROOT / ".monitor") else f" --state-dir {state_dir.resolve()}"
    return {"probe": "python3 -m store_monitor probe <app_id> --countries <cc,cc,...>",
            "history": f"python3 -m store_monitor history <app_id> --json{sd}"}


def anomalies_of(run: Run) -> list[dict]:
    return [c for c in run.changes if c.get("is_anomaly")]


def build_case(run: Run, history: History, state_dir: Path) -> dict:
    anomalies = []
    for c in anomalies_of(run):
        timeline = [{k: e.get(k) for k in ("run_id", "checked_at", "status", "detail", "signals")}
                    for e in history.timeline(c["app_id"], c["country"])]
        anomalies.append({k: c[k] for k in ("label", "app_id", "country", "previous", "current",
                                             "previous_run_id", "current_detail",
                                             "current_signals", "previous_signals")}
                         | {"timeline": timeline})
    return {"run_id": run.run_id, "checked_at": run.checked_at, "input": run.input,
            "anomalies": anomalies, "tools": tool_commands(state_dir)}


def case_dir(state_dir: Path, run_id: str) -> Path:
    return Path(state_dir) / "investigations" / run_id


def write_case(case: dict, state_dir: Path) -> Path:
    d = case_dir(state_dir, case["run_id"])
    d.mkdir(parents=True, exist_ok=True)
    path = d / "case.json"
    path.write_text(json.dumps(case, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# --- agent run ----------------------------------------------------------------------

def skill_instructions() -> str:
    """SKILL.md body without frontmatter: the same instructions serve the interactive
    skill and the headless run, so they cannot drift apart."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    return text.strip()


def agent_command(case: dict, model: str | None, max_budget_usd: float) -> list[str]:
    prompt = ("Investigate every anomaly in this App Store Monitor case and return the "
              "structured conclusion.\n\nCase:\n```json\n"
              + json.dumps(case, indent=2, ensure_ascii=False) + "\n```")
    cmd = ["claude", "-p", prompt,
           "--append-system-prompt", skill_instructions(),
           "--output-format", "stream-json", "--verbose",
           "--json-schema", json.dumps(CONCLUSION_SCHEMA),
           # Least privilege: only Bash exists, and only the two read-only commands
           # are approved. Everything else is denied (headless runs never prompt).
           "--tools", "Bash",
           "--allowedTools", "Bash(python3 -m store_monitor probe *)",
           "Bash(python3 -m store_monitor history *)",
           "--permission-mode", "default",
           # Ignore the user's personal settings and MCP servers: their permission
           # grants must not widen what this agent can do.
           "--setting-sources", "project", "--strict-mcp-config",
           "--no-session-persistence",
           "--max-budget-usd", str(max_budget_usd)]
    if model:
        cmd += ["--model", model]
    return cmd


def claude_available() -> bool:
    return shutil.which("claude") is not None


def stream_events(cmd: list[str]) -> Iterable[dict]:
    with subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, text=True) as proc:
        for line in proc.stdout:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        err = proc.stderr.read()
    if proc.returncode:
        yield {"type": "process_error", "returncode": proc.returncode, "stderr": err[-2000:]}


def tool_calls(events: list[dict]) -> list[dict]:
    """The investigation trace: each tool call and whether it succeeded."""
    calls, by_id = [], {}
    for e in events:
        content = (e.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if e.get("type") == "assistant" and c.get("type") == "tool_use":
                call = {"tool": c.get("name"), "input": c.get("input"), "ok": None}
                by_id[c.get("id")] = call
                calls.append(call)
            elif e.get("type") == "user" and c.get("type") == "tool_result":
                if c.get("tool_use_id") in by_id:
                    by_id[c["tool_use_id"]]["ok"] = not c.get("is_error")
    return calls


def run_investigation(case: dict, state_dir: Path, model: str | None = None,
                      max_budget_usd: float = 1.0,
                      events_source: Callable[[list[str]], Iterable[dict]] = stream_events,
                      on_tool_call: Callable[[dict], None] = lambda call: None) -> dict:
    cmd = agent_command(case, model, max_budget_usd)
    events: list[dict] = []
    for e in events_source(cmd):
        events.append(e)
        for call in tool_calls([e]):
            on_tool_call(call)

    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    conclusion = result.get("structured_output")
    problems = validate_conclusion(conclusion, case)
    failure = next((e for e in events if e.get("type") == "process_error"), None)
    if failure:
        problems.insert(0, f"claude exited with code {failure['returncode']}: {failure['stderr'][-300:]}")

    outcome = {
        "run_id": case["run_id"],
        "valid": not problems,
        "problems": problems,
        "conclusion": conclusion,
        "trace": [c for c in tool_calls(events) if c["tool"] != "StructuredOutput"],
        "permission_denials": result.get("permission_denials", []),
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "stop": result.get("subtype"),
    }
    d = case_dir(state_dir, case["run_id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (d / "conclusion.json").write_text(json.dumps(outcome, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    return outcome


# --- deterministic guardrail on model output ----------------------------------------

def validate_conclusion(conclusion, case: dict) -> list[str]:
    if not isinstance(conclusion, dict):
        return ["no structured conclusion returned"]
    items = conclusion.get("investigations")
    if not isinstance(items, list):
        return ["'investigations' is missing or not a list"]

    problems = []
    expected = {app_key(a["app_id"], a["country"]) for a in case["anomalies"]}
    seen = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            problems.append(f"investigation #{i} is not an object")
            continue
        key = app_key(str(it.get("app_id")), str(it.get("country")))
        seen.append(key)
        if key not in expected:
            problems.append(f"{key}: not an anomaly in this case")
        if it.get("classification") not in CLASSIFICATIONS:
            problems.append(f"{key}: unknown classification {it.get('classification')!r}")
        if it.get("confidence") not in CONFIDENCE:
            problems.append(f"{key}: unknown confidence {it.get('confidence')!r}")
        if not it.get("evidence"):
            problems.append(f"{key}: no evidence cited")
        if it.get("classification") == "INCONCLUSIVE" and it.get("confidence") == "high":
            problems.append(f"{key}: INCONCLUSIVE with high confidence is contradictory")
    for key in sorted(expected - set(seen)):
        problems.append(f"{key}: anomaly not investigated")
    for key in sorted({k for k in seen if seen.count(k) > 1}):
        problems.append(f"{key}: investigated more than once")
    return problems
