"""Agent evaluation: run the real investigation against recorded scenarios and grade it.

    python3 -m evals.run                         # every scenario, one trial each
    python3 -m evals.run transient_blip --trials 3 --model sonnet

Each scenario builds a throwaway world: monitoring history, the current run that
raised the anomaly, and recorded store responses that `probe` replays instead of
calling Apple. The agent, prompt, tools and permissions are exactly the production
ones. Only the data underneath them is fixed, so results are comparable across
prompt or model changes.

Grading is deterministic code, not another model. The checks are objective, so an
LLM judge would only add noise and cost.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from store_monitor import investigation as inv
from store_monitor.changes import detect_changes
from store_monitor.fixture_client import ENV_VAR
from store_monitor.history import History, build_baselines
from store_monitor.models import CheckResult, Status

SCENARIO_DIR = Path(__file__).parent / "scenarios"
RESULTS_DIR = inv.REPO_ROOT / ".monitor" / "evals"
CONFIDENCE_RANK = {c: i for i, c in enumerate(inv.CONFIDENCE)}
ALLOWED_COMMAND = re.compile(r"^python3 -m store_monitor (probe|history)\b")
PROBE_CALL = re.compile(r"store_monitor probe (\d+) --countries ([a-z,]+)")


def load_scenarios(names: list[str]) -> list[dict]:
    paths = sorted(SCENARIO_DIR.glob("*.json"))
    scenarios = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if names:
        unknown = set(names) - {s["name"] for s in scenarios}
        if unknown:
            sys.exit(f"Unknown scenario(s): {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s["name"] in names]
    return scenarios


def build_world(scenario: dict, workdir: Path) -> tuple[dict, Path, Path]:
    """Create history + current run with the real code; returns (case, state_dir, fixture)."""
    app = scenario["app"]
    state_dir = workdir / "state"
    history = History(state_dir)
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def result(entry: dict) -> CheckResult:
        return CheckResult(label=app["label"], status=Status(entry["status"]),
                           app_id=app["app_id"], country=app["country"],
                           detail=entry.get("detail", ""), signals=entry["signals"])

    for i, entry in enumerate(scenario["history"]):
        history.save_run([result(entry)], "eval", now=t + timedelta(days=i))
    current = [result(scenario["current"])]
    changes, _ = detect_changes(current, build_baselines(history.load_runs()))
    run = history.save_run(current, "eval", [c.to_dict() for c in changes],
                           now=t + timedelta(days=len(scenario["history"])))
    if not inv.anomalies_of(run):
        raise ValueError(f"{scenario['name']}: scenario does not produce an anomaly")

    fixture = workdir / "fixture.json"
    fixture.write_text(json.dumps({"app": {"name": app["label"]},
                                   "storefronts": scenario["storefronts"]}), encoding="utf-8")
    return inv.build_case(run, history, state_dir), state_dir, fixture


def probed_storefronts(trace: list[dict], app_id: str) -> list[str]:
    """Every storefront probed for app_id, once per probe call (repeats count)."""
    out = []
    for call in trace:
        for pid, countries in PROBE_CALL.findall((call.get("input") or {}).get("command", "")):
            if pid == app_id:
                out += countries.split(",")
    return out


def grade(scenario: dict, outcome: dict) -> list[dict]:
    expect, app = scenario["expect"], scenario["app"]
    items = (outcome.get("conclusion") or {}).get("investigations") or [{}]
    it = items[0]
    cls, conf = it.get("classification"), it.get("confidence")
    checks = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("valid_output", outcome["valid"], "; ".join(outcome["problems"]))
    check("classification", cls in expect["classification"],
          f"got {cls}, expected one of {expect['classification']}")
    if expect.get("forbid"):
        check("not_forbidden", cls not in expect["forbid"], f"got {cls}")
    if expect.get("max_confidence"):
        check("calibrated_confidence",
              conf in CONFIDENCE_RANK
              and CONFIDENCE_RANK[conf] <= CONFIDENCE_RANK[expect["max_confidence"]],
              f"got {conf}, max {expect['max_confidence']}")
    check("cites_probe_evidence",
          any(e.get("source") == "probe" for e in it.get("evidence") or []),
          "no evidence item from a fresh probe")
    own = probed_storefronts(outcome["trace"], app["app_id"]).count(app["country"])
    if expect.get("min_own_storefront_probes"):
        check("reprobed_own_storefront", own >= expect["min_own_storefront_probes"],
              f"{own} probe(s) of {app['country']}, need {expect['min_own_storefront_probes']}")
    commands = [(c.get("input") or {}).get("command", "") for c in outcome["trace"]]
    stray = [c for c in commands
             if not all(ALLOWED_COMMAND.match(part.strip())
                        for part in re.split(r"&&|;|\|\|", c) if part.strip())]
    check("stayed_in_tools", not outcome["permission_denials"] and not stray,
          f"{len(outcome['permission_denials'])} denied, stray: {stray[:2]}")
    return checks


def run_trial(scenario: dict, model: str | None, budget: float) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"eval-{scenario['name']}-") as tmp:
        case, state_dir, fixture = build_world(scenario, Path(tmp))
        env = {**os.environ, ENV_VAR: str(fixture)}
        outcome = inv.run_investigation(
            case, state_dir, model, budget,
            events_source=lambda cmd: inv.stream_events(cmd, env))
    checks = grade(scenario, outcome)
    it = ((outcome.get("conclusion") or {}).get("investigations") or [{}])[0]
    return {"scenario": scenario["name"], "passed": all(c["passed"] for c in checks),
            "checks": checks, "classification": it.get("classification"),
            "confidence": it.get("confidence"), "tool_calls": len(outcome["trace"]),
            "cost_usd": outcome.get("cost_usd") or 0, "outcome": outcome}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run", description=__doc__.split("\n\n")[0])
    ap.add_argument("scenarios", nargs="*", help="Scenario names (default: all)")
    ap.add_argument("--trials", type=int, default=1, help="Runs per scenario")
    ap.add_argument("--model", help="Claude model (default: Claude Code's)")
    ap.add_argument("--max-budget-usd", type=float, default=0.5, help="Cap per trial")
    ap.add_argument("--workers", type=int, default=3, help="Trials in parallel")
    args = ap.parse_args(argv)

    if not inv.claude_available():
        print("Claude Code CLI ('claude') not found.", file=sys.stderr)
        return 2
    jobs = [s for s in load_scenarios(args.scenarios) for _ in range(args.trials)]
    print(f"Running {len(jobs)} trial(s)...\n")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(lambda s: run_trial(s, args.model, args.max_budget_usd), jobs))

    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['scenario']:<26} {str(r['classification']):<24} "
              f"{str(r['confidence']):<7} {r['tool_calls']} calls  ${r['cost_usd']:.3f}")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"        ✗ {c['check']}: {c['detail']}")
    passed = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] for r in results)
    print(f"\n{passed}/{len(results)} passed · ${cost:.2f} total")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({"model": args.model, "results": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(f"Saved: {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
