from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.watchdog import (
    JobHealth,
    classify_job,
    default_thresholds,
    diff_health,
    format_age,
)


class ClassifyJobTests(unittest.TestCase):
    def test_running_and_fresh_is_ok(self):
        h = classify_job("auto-scan", running=True, age_seconds=10, threshold_seconds=100)
        self.assertTrue(h.healthy)
        self.assertEqual(h.reason, "ok")

    def test_age_at_threshold_is_still_ok(self):
        h = classify_job("auto-scan", running=True, age_seconds=100, threshold_seconds=100)
        self.assertTrue(h.healthy)

    def test_age_past_threshold_is_stale(self):
        h = classify_job("auto-scan", running=True, age_seconds=101, threshold_seconds=100)
        self.assertFalse(h.healthy)
        self.assertEqual(h.reason, "stale")

    def test_not_running_is_missing(self):
        h = classify_job("reminders", running=False, age_seconds=5, threshold_seconds=100)
        self.assertFalse(h.healthy)
        self.assertEqual(h.reason, "missing")

    def test_no_age_yet_is_starting_not_alerting(self):
        # A just-started job with no heartbeat must never page the admin.
        h = classify_job("market-fetch", running=True, age_seconds=None, threshold_seconds=100)
        self.assertTrue(h.healthy)
        self.assertEqual(h.reason, "starting")


class DiffHealthTests(unittest.TestCase):
    def _h(self, name, healthy):
        return JobHealth(name, healthy, "ok" if healthy else "stale", 0, 100)

    def test_new_failure_is_alerted_once(self):
        healths = [self._h("auto-scan", False)]
        new_alerts, recoveries = diff_health(set(), healths)
        self.assertEqual([h.name for h in new_alerts], ["auto-scan"])
        self.assertEqual(recoveries, [])

    def test_still_unhealthy_does_not_realert(self):
        healths = [self._h("auto-scan", False)]
        new_alerts, recoveries = diff_health({"auto-scan"}, healths)
        self.assertEqual(new_alerts, [])
        self.assertEqual(recoveries, [])

    def test_recovery_is_reported(self):
        healths = [self._h("auto-scan", True)]
        new_alerts, recoveries = diff_health({"auto-scan"}, healths)
        self.assertEqual(new_alerts, [])
        self.assertEqual([h.name for h in recoveries], ["auto-scan"])

    def test_independent_jobs_tracked_separately(self):
        healths = [self._h("auto-scan", False), self._h("reminders", True)]
        new_alerts, recoveries = diff_health({"reminders"}, healths)
        self.assertEqual([h.name for h in new_alerts], ["auto-scan"])
        self.assertEqual([h.name for h in recoveries], ["reminders"])


class ThresholdTests(unittest.TestCase):
    def test_auto_scan_threshold_scales_with_interval(self):
        narrow = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        wide = default_thresholds(scan_interval_seconds=3600, market_poll_seconds=300)
        self.assertGreater(wide["auto-scan"], narrow["auto-scan"])

    def test_critical_jobs_present(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        for name in ("auto-scan", "reminders", "source-scores", "access-scheduler", "market-fetch"):
            self.assertIn(name, t)

    def test_optional_jobs_excluded(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        self.assertNotIn("daily-digest", t)
        self.assertNotIn("obsidian-sync", t)


class FormatAgeTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(format_age(45), "45 с")

    def test_minutes(self):
        self.assertEqual(format_age(600), "10 мин")

    def test_hours(self):
        self.assertEqual(format_age(7200), "2 ч")

    def test_none(self):
        self.assertEqual(format_age(None), "—")


if __name__ == "__main__":
    unittest.main()
