"""History persistence and change detection, using a temporary state directory."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from store_monitor.changes import ChangeKind, describe_signals, detect_changes
from store_monitor.history import History, build_baselines
from store_monitor.models import CheckResult, Status

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def result(status, app_id="111111", country="us", label="Demo"):
    return CheckResult(label=label, status=status, app_id=app_id, country=country,
                       signals={"lookup_found": status is Status.LIVE, "page_http": 200})


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = History(self.tmp.name)
        self.clock = T0

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, *statuses_per_run):
        """Save one run per argument; each argument is a status or a list of results."""
        for item in statuses_per_run:
            results = item if isinstance(item, list) else [result(item)]
            self.history.save_run(results, "apps.txt", now=self.clock)
            self.clock += timedelta(hours=1)

    def detect(self, *current):
        return detect_changes(list(current), build_baselines(self.history.load_runs()))


class PersistenceTest(HistoryTestCase):
    def test_round_trip_keeps_results_and_order(self):
        self.record(Status.LIVE, Status.REMOVED)
        runs = self.history.load_runs()
        self.assertEqual([r.results[0].status for r in runs], [Status.LIVE, Status.REMOVED])
        self.assertEqual(runs[0].results[0].signals["page_http"], 200)

    def test_same_second_runs_get_distinct_ids(self):
        self.history.save_run([result(Status.LIVE)], "a", now=T0)
        self.history.save_run([result(Status.LIVE)], "a", now=T0)
        self.assertEqual(len(self.history.load_runs()), 2)

    def test_empty_state_dir(self):
        self.assertEqual(self.history.load_runs(), [])

    def test_timeline_filters_by_app_and_country(self):
        self.record([result(Status.LIVE), result(Status.LIVE, country="gb"),
                     result(Status.LIVE, app_id="222222")])
        self.assertEqual(len(self.history.timeline("111111")), 2)
        self.assertEqual(len(self.history.timeline("111111", "gb")), 1)


class ChangeDetectionTest(HistoryTestCase):
    def test_first_sighting_is_new_not_anomaly(self):
        changes, unchanged = self.detect(result(Status.REMOVED))
        self.assertEqual([c.kind for c in changes], [ChangeKind.NEW])
        self.assertFalse(changes[0].is_anomaly)

    def test_unchanged_is_not_reported(self):
        self.record(Status.LIVE)
        self.assertEqual(self.detect(result(Status.LIVE)), ([], 1))

    def test_live_to_removed_is_anomaly(self):
        self.record(Status.LIVE)
        (c,), _ = self.detect(result(Status.REMOVED))
        self.assertTrue(c.is_anomaly)
        self.assertEqual((c.previous, c.current), (Status.LIVE, Status.REMOVED))

    def test_recovery_is_reported(self):
        self.record(Status.UNCERTAIN)
        (c,), _ = self.detect(result(Status.LIVE))
        self.assertEqual((c.kind, c.previous), (ChangeKind.STATUS_CHANGE, Status.UNCERTAIN))

    def test_error_is_check_failure_not_anomaly(self):
        self.record(Status.LIVE)
        (c,), _ = self.detect(result(Status.ERROR))
        self.assertEqual((c.kind, c.previous, c.consecutive_errors),
                         (ChangeKind.CHECK_FAILED, Status.LIVE, 1))

    def test_consecutive_errors_are_counted(self):
        self.record(Status.LIVE, Status.ERROR, Status.ERROR)
        (c,), _ = self.detect(result(Status.ERROR))
        self.assertEqual(c.consecutive_errors, 3)

    def test_error_blip_then_same_status_is_unchanged(self):
        self.record(Status.LIVE, Status.ERROR)
        self.assertEqual(self.detect(result(Status.LIVE)), ([], 1))

    def test_change_hidden_behind_error_is_detected(self):
        self.record(Status.LIVE, Status.ERROR)
        (c,), _ = self.detect(result(Status.REMOVED))
        self.assertEqual((c.previous, c.current), (Status.LIVE, Status.REMOVED))

    def test_storefronts_are_tracked_separately(self):
        self.record([result(Status.LIVE, country="us")])
        (c,), _ = self.detect(result(Status.REMOVED, country="tr"))
        self.assertIs(c.kind, ChangeKind.NEW)

    def test_inputs_without_app_id_are_ignored(self):
        self.assertEqual(self.detect(CheckResult(label="x", status=Status.NO_LINK)), ([], 0))


class DescribeSignalsTest(unittest.TestCase):
    def test_summary(self):
        s = describe_signals({"lookup_found": False, "alt_country": "us",
                              "alt_lookup_found": None, "alt_lookup_error": "timeout",
                              "page_http": 404})
        self.assertEqual(s, "lookup empty, US lookup failed (timeout), page 404")


if __name__ == "__main__":
    unittest.main()
