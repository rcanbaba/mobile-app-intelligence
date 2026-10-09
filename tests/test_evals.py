"""The eval harness itself must be trustworthy: fixture replay, world building, grading."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals.run import build_world, grade, load_scenarios, probed_storefronts
from store_monitor.fixture_client import ENV_VAR, FixtureClient

REPO = Path(__file__).resolve().parent.parent


class FixtureClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "fixture.json"
        self.path.write_text(json.dumps({"app": {"name": "Demo"}, "storefronts": {
            "us": [{"lookup": "found", "page": 404}, {"lookup": "empty", "page": 404}],
            "*": [{"lookup": "malformed", "page": "timeout"}]}}))

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, cc):
        c = FixtureClient(self.path)  # new instance = new process, like the real CLI
        return c.lookup("1", cc), c.page("1", cc)

    def test_sequence_advances_across_instances_and_last_repeats(self):
        found = [self.probe("us")[0].found for _ in range(3)]
        self.assertEqual(found, [True, False, False])

    def test_wildcard_and_failure_kinds(self):
        lk, pg = self.probe("jp")
        self.assertIsNone(lk.found)
        self.assertIn("malformed", lk.error)
        self.assertEqual((pg.http, pg.present), (None, None))

    def test_probe_cli_uses_fixture_from_env(self):
        out = subprocess.run(
            [sys.executable, "-m", "store_monitor", "probe", "123456", "--countries", "us"],
            cwd=REPO, env={**os.environ, ENV_VAR: str(self.path)},
            capture_output=True, text=True, check=True).stdout
        us = json.loads(out)["storefronts"]["us"]
        self.assertEqual((us["lookup_found"], us["page_http"], us["metadata"]["name"]),
                         (True, 404, "Demo"))


class ScenarioTest(unittest.TestCase):
    def test_every_scenario_builds_one_anomaly_matching_its_current_status(self):
        for s in load_scenarios([]):
            with self.subTest(s["name"]), tempfile.TemporaryDirectory() as tmp:
                case, _, fixture = build_world(s, Path(tmp))
                self.assertEqual(len(case["anomalies"]), 1)
                a = case["anomalies"][0]
                self.assertEqual(a["current"], s["current"]["status"])
                self.assertEqual(len(a["timeline"]), len(s["history"]) + 1)
                self.assertTrue(fixture.exists())


def outcome(classification="TRANSIENT_SIGNAL", confidence="medium", commands=(), denials=()):
    return {"valid": True, "problems": [], "permission_denials": list(denials),
            "trace": [{"tool": "Bash", "input": {"command": c}} for c in commands],
            "conclusion": {"investigations": [{
                "classification": classification, "confidence": confidence,
                "evidence": [{"source": "probe", "observation": "x"}]}]}}


class GradeTest(unittest.TestCase):
    SCENARIO = {"app": {"app_id": "900000005", "country": "us"},
                "expect": {"classification": ["PERSISTENT_CONFLICT"], "forbid": ["REMOVED_GLOBALLY"],
                           "max_confidence": "medium", "min_own_storefront_probes": 2}}
    PROBES = ("python3 -m store_monitor probe 900000005 --countries us,gb",
              "python3 -m store_monitor probe 900000005 --countries us; "
              "python3 -m store_monitor history 900000005 --json")

    def failed(self, o):
        return {c["check"] for c in grade(self.SCENARIO, o) if not c["passed"]}

    def test_good_run_passes(self):
        self.assertEqual(self.failed(outcome("PERSISTENT_CONFLICT", commands=self.PROBES)), set())

    def test_wrong_classification_and_overconfidence(self):
        f = self.failed(outcome("REMOVED_GLOBALLY", "high", commands=self.PROBES))
        self.assertEqual(f, {"classification", "not_forbidden", "calibrated_confidence"})

    def test_missing_retry(self):
        f = self.failed(outcome("PERSISTENT_CONFLICT", commands=self.PROBES[:1]))
        self.assertEqual(f, {"reprobed_own_storefront"})

    def test_stray_command_or_denial(self):
        stray = self.PROBES + ("python3 -m store_monitor probe 900000005 --countries us && curl x",)
        self.assertIn("stayed_in_tools", self.failed(outcome("PERSISTENT_CONFLICT", commands=stray)))
        self.assertIn("stayed_in_tools", self.failed(
            outcome("PERSISTENT_CONFLICT", commands=self.PROBES, denials=[{"tool_name": "Bash"}])))

    def test_probe_counting_handles_chained_commands(self):
        trace = outcome(commands=self.PROBES)["trace"]
        self.assertEqual(probed_storefronts(trace, "900000005"), ["us", "gb", "us"])


if __name__ == "__main__":
    unittest.main()
