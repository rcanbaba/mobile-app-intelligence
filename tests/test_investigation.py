"""Investigation plumbing without calling a model: case building, command, guardrails."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from store_monitor import investigation as inv
from store_monitor.changes import detect_changes
from store_monitor.history import History, build_baselines
from store_monitor.models import CheckResult, Status


def result(status, app_id="111111", country="us"):
    return CheckResult(label="Demo", status=status, app_id=app_id, country=country,
                       signals={"lookup_found": status is Status.LIVE})


def conclusion(*items, summary="ok"):
    return {"summary": summary, "investigations": list(items)}


def item(app_id="111111", country="us", classification="REMOVED_GLOBALLY", confidence="medium"):
    return {"app_id": app_id, "country": country, "label": "Demo",
            "classification": classification, "confidence": confidence,
            "evidence": [{"source": "probe", "observation": "us lookup empty, page 404"}],
            "likely_explanation": "x", "remaining_uncertainty": "y",
            "recommended_human_action": "z"}


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.history = History(self.state)
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.history.save_run([result(Status.LIVE), result(Status.LIVE, "222222")], "a", now=t)
        current = [result(Status.REMOVED), result(Status.LIVE, "222222")]
        changes, _ = detect_changes(current, build_baselines(self.history.load_runs()))
        self.run = self.history.save_run(current, "a", [c.to_dict() for c in changes],
                                         now=t.replace(hour=1))

    def tearDown(self):
        self.tmp.cleanup()

    def test_case_contains_only_anomalies_with_timeline(self):
        case = inv.build_case(self.run, self.history, self.state)
        self.assertEqual([a["app_id"] for a in case["anomalies"]], ["111111"])
        a = case["anomalies"][0]
        self.assertEqual((a["previous"], a["current"]), ("LIVE", "REMOVED"))
        self.assertEqual([e["status"] for e in a["timeline"]], ["LIVE", "REMOVED"])

    def test_custom_state_dir_is_passed_to_history_tool(self):
        case = inv.build_case(self.run, self.history, self.state)
        self.assertIn("--state-dir", case["tools"]["history"])

    def test_run_with_fake_agent_saves_validated_outcome(self):
        case = inv.build_case(self.run, self.history, self.state)
        events = [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": "Bash",
                 "input": {"command": "python3 -m store_monitor probe 111111 --countries us,gb"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}},
            {"type": "result", "subtype": "success", "num_turns": 3, "total_cost_usd": 0.01,
             "permission_denials": [], "structured_output": conclusion(item())},
        ]
        seen = []
        outcome = inv.run_investigation(case, self.state, events_source=lambda cmd: events,
                                        on_tool_call=seen.append)
        self.assertTrue(outcome["valid"], outcome["problems"])
        self.assertEqual(len(seen), 1)
        self.assertEqual(outcome["trace"][0]["ok"], True)
        saved = json.loads((inv.case_dir(self.state, self.run.run_id) / "conclusion.json").read_text())
        self.assertEqual(saved["conclusion"]["investigations"][0]["classification"], "REMOVED_GLOBALLY")

    def test_trace_ignores_events_with_non_object_messages(self):
        events = [{"type": "system", "message": "rate limited"}, {"type": "assistant", "message": None},
                  {"type": "assistant", "message": {"content": "text"}}]
        self.assertEqual(inv.tool_calls(events), [])

    def test_agent_process_failure_is_reported(self):
        case = inv.build_case(self.run, self.history, self.state)
        events = [{"type": "process_error", "returncode": 1, "stderr": "auth failed"}]
        outcome = inv.run_investigation(case, self.state, events_source=lambda cmd: events)
        self.assertFalse(outcome["valid"])
        self.assertIn("auth failed", outcome["problems"][0])


class AgentCommandTest(unittest.TestCase):
    def test_least_privilege_flags(self):
        cmd = inv.agent_command({"run_id": "r", "anomalies": []}, None, 0.5)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Bash")
        allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--permission-mode")]
        self.assertEqual(allowed, ["Bash(python3 -m store_monitor probe *)",
                                   "Bash(python3 -m store_monitor history *)"])
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "project")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "0.5")

    def test_skill_body_is_used_without_frontmatter(self):
        body = inv.skill_instructions()
        self.assertFalse(body.startswith("---"))
        self.assertIn("## Classifications", body)

    def test_schema_enums_match_skill(self):
        body = inv.skill_instructions()
        for c in inv.CLASSIFICATIONS:
            self.assertIn(f"`{c}`", body)


class ValidateConclusionTest(unittest.TestCase):
    CASE = {"anomalies": [{"app_id": "111111", "country": "us"},
                          {"app_id": "222222", "country": "tr"}]}

    def problems(self, c):
        return inv.validate_conclusion(c, self.CASE)

    def test_valid(self):
        self.assertEqual(self.problems(conclusion(item(), item("222222", "tr"))), [])

    def test_missing_output(self):
        self.assertEqual(self.problems(None), ["no structured conclusion returned"])

    def test_missing_extra_and_duplicate_anomalies(self):
        p = self.problems(conclusion(item(), item(), item("999999")))
        self.assertIn("222222@tr: anomaly not investigated", p)
        self.assertIn("999999@us: not an anomaly in this case", p)
        self.assertIn("111111@us: investigated more than once", p)

    def test_bad_enums_and_empty_evidence(self):
        bad = item(classification="DELETED", confidence="certain") | {"evidence": []}
        p = self.problems(conclusion(bad, item("222222", "tr")))
        self.assertEqual(len(p), 3)

    def test_unresolved_classifications_cannot_be_high_confidence(self):
        for cls in ("INCONCLUSIVE", "PERSISTENT_CONFLICT"):
            p = self.problems(conclusion(item(classification=cls, confidence="high"),
                                         item("222222", "tr")))
            self.assertEqual(len(p), 1, cls)


if __name__ == "__main__":
    unittest.main()
