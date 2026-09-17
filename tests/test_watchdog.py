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

    def test_auto_scan_window_absorbs_idle_plus_one_flood_wait(self):
        # A healthy scan can go silent for one idle interval plus a single
        # capped FloodWait stall; the window must clear that or it false-pages.
        t = default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, flood_wait_max_seconds=1800
        )
        self.assertGreaterEqual(t["auto-scan"], 900 + 1800)

    def test_auto_scan_window_widens_with_flood_wait_cap(self):
        low = default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, flood_wait_max_seconds=600
        )
        high = default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, flood_wait_max_seconds=1800
        )
        self.assertGreater(high["auto-scan"], low["auto-scan"])

    def test_critical_jobs_present(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        for name in ("auto-scan", "broadcast-approval", "source-scores", "access-scheduler", "market-fetch"):
            self.assertIn(name, t)

    def test_feature_jobs_excluded_unless_enabled(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        self.assertNotIn("obsidian-sync", t)
        self.assertNotIn("salary-sync", t)

    def test_enabled_feature_job_gets_ten_polls(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300,
                               enabled_features={"salary-sync": 120, "obsidian-sync": 30})
        self.assertEqual(t["salary-sync"], 1200)
        # Never tighter than ten minutes, however fast the poll.
        self.assertEqual(t["obsidian-sync"], 600)

    def test_unknown_feature_is_ignored(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300,
                               enabled_features={"tunnel": 10})
        self.assertNotIn("tunnel", t)

    def test_ticking_jobs_are_watched(self):
        # The digest and roulette loops tick every 30 s now instead of sleeping
        # to their target, so a silent one is a dead one.
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        for name in ("daily-digest", "roulette-reminder", "maintenance"):
            self.assertIn(name, t)

    def test_feature_job_polls_follow_configuration(self):
        from types import SimpleNamespace

        from pulse_desk.jobs import feature_job_polls

        off = feature_job_polls(SimpleNamespace(obsidian_sync_poll_seconds=30, salary_xlsx_path=""))
        self.assertEqual(set(off), {"obsidian-sync"})
        on = feature_job_polls(SimpleNamespace(obsidian_sync_poll_seconds=30,
                                               salary_xlsx_path="book.xlsx", salary_sync_poll_seconds=60))
        self.assertEqual(on["salary-sync"], 60)


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


class BotJobMonitoringTests(unittest.TestCase):
    """`bot-connection` is what keeps incoming updates flowing.

    It used to be fire-and-forget and absent from both monitoring lists, so if
    it died the bot went deaf while /api/health still reported ok.
    """

    def test_bot_connection_is_watched_when_a_bot_is_configured(self):
        t = default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, bot_configured=True
        )
        self.assertIn("bot-connection", t)

    def test_no_bot_means_no_false_outage(self):
        t = default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, bot_configured=False
        )
        self.assertNotIn("bot-connection", t)

    def test_outbox_is_watched(self):
        t = default_thresholds(scan_interval_seconds=900, market_poll_seconds=300)
        self.assertIn("pending-sends", t)

    def test_expected_jobs_and_thresholds_agree(self):
        from pulse_desk.jobs import expected_jobs

        thresholds = set(default_thresholds(
            scan_interval_seconds=900, market_poll_seconds=300, bot_configured=True
        ))
        expected = expected_jobs(bot_configured=True)
        # Every job we page about for staleness must also be one we require to
        # exist; the two lists silently disagreed before.
        self.assertTrue(thresholds <= expected, thresholds - expected)

    def test_expected_jobs_tracks_the_bot(self):
        from pulse_desk.jobs import expected_jobs

        self.assertIn("bot-connection", expected_jobs(bot_configured=True))
        self.assertNotIn("bot-connection", expected_jobs(bot_configured=False))
