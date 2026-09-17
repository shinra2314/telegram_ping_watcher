"""Detection latency: post → detection, win edit → win flag, and the columns behind it."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import latency  # noqa: E402


class LatencyRuleTests(unittest.TestCase):
    def test_post_delay_mixes_offset_and_naive(self):
        from datetime import datetime

        posted = datetime(2026, 9, 10, 16, 25, 25).astimezone()
        row = {"date": posted.isoformat(), "detected_at": "2026-09-10T16:31:25"}
        self.assertAlmostEqual(latency.post_delay(row), 6.0)

    def test_win_measured_from_the_edit(self):
        row = {"is_win": 1, "date": "2026-09-10T10:00:00", "edited_at": "2026-09-10T20:00:00",
               "win_detected_at": "2026-09-10T20:07:00"}
        self.assertAlmostEqual(latency.win_delay(row), 7.0)

    def test_old_rows_without_flag_time_are_left_out(self):
        self.assertIsNone(latency.win_delay({"is_win": 1, "date": "2026-09-10T10:00:00"}))
        self.assertIsNone(latency.win_delay({"is_win": 0, "win_detected_at": "2026-09-10T10:00:00"}))

    def test_backfill_is_not_a_delay(self):
        row = {"date": "2026-01-01T10:00:00", "detected_at": "2026-09-10T10:00:00"}
        self.assertIsNone(latency.post_delay(row))

    def test_summary_and_channels(self):
        rows = [{"chat": "slow", "date": "2026-09-10T10:00:00", "detected_at": f"2026-09-10T1{h}:00:00"}
                for h in (1, 2)]
        rows += [{"chat": "fast", "date": "2026-09-10T10:00:00", "detected_at": "2026-09-10T10:01:00"}
                 for _ in range(2)]
        built = latency.build_latency(rows)
        self.assertEqual(built["posts"]["count"], 4)
        self.assertEqual(built["posts"]["slow"], 2)
        self.assertEqual(built["channels"][0]["chat"], "slow")
        self.assertEqual(latency.fmt_minutes(0.2), "<1 мин")
        self.assertEqual(latency.fmt_minutes(125), "2.1 ч")

    def test_card_renders_empty_and_full(self):
        from pulse_desk.bot.cards import latency_lines

        self.assertIn("Данных пока нет", "\n".join(latency_lines({})))
        built = latency.build_latency([{"chat": "c", "date": "2026-09-10T10:00:00",
                                        "detected_at": "2026-09-10T10:05:00"}])
        self.assertIn("5 мин", "\n".join(latency_lines(built)))


class WinFlagColumnTests(unittest.TestCase):
    """save_ping stamps win_detected_at only when a row becomes a win."""

    def setUp(self):
        import database

        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_lat_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "lat.db"
        asyncio.run(database.init_db())

    def tearDown(self):
        import database

        database.DB_PATH = self._old
        self._tmp.cleanup()

    def test_edit_into_win_stamps_once(self):
        import database

        base = {"chat": "c", "chat_id": -1001, "message_id": 5, "mentions": ["a"], "text": "giveaway",
                "chat_type": "channel", "date": "2026-09-10T10:00:00", "detected_at": "2026-09-10T10:01:00"}
        ping_id = asyncio.run(database.save_ping(base))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertIsNone(row["win_detected_at"])

        edited = dict(base, is_win=True, edited_at="2026-09-10T20:00:00", detected_at="2026-09-10T20:03:00")
        asyncio.run(database.save_ping(edited))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertEqual(row["win_detected_at"], "2026-09-10T20:03:00")
        self.assertEqual(row["edited_at"], "2026-09-10T20:00:00")

        again = dict(edited, detected_at="2026-09-11T08:00:00")
        asyncio.run(database.save_ping(again))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertEqual(row["win_detected_at"], "2026-09-10T20:03:00")

    def test_rescan_without_win_verdict_keeps_the_win(self):
        # A later pass that does not see the win (another account's search
        # index, a sweep reading only the placeholder) must not clear it —
        # otherwise the next pass is an "upgrade" again and re-sends the card.
        import database

        base = {"chat": "c", "chat_id": -1002, "message_id": 7, "mentions": ["a"], "text": "card",
                "chat_type": "channel", "date": "2026-09-10T10:00:00", "is_win": True}
        ping_id = asyncio.run(database.save_ping(dict(base, priority_score=90, priority_label="critical")))
        asyncio.run(database.save_ping(dict(base, is_win=False, priority_score=15, priority_label="normal")))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertEqual(row["is_win"], 1)
        self.assertEqual(row["giveaway_status"], "pending")
        # Its priority does not sink with the pass that missed the win…
        self.assertEqual((row["priority_score"], row["priority_label"]), (90, "critical"))
        # …but a higher score still lands.
        asyncio.run(database.save_ping(dict(base, priority_score=100, priority_label="critical")))
        self.assertEqual(asyncio.run(database.get_ping_by_id(ping_id))["priority_score"], 100)

    def test_non_win_priority_follows_the_latest_pass(self):
        import database

        base = {"chat": "c", "chat_id": -1003, "message_id": 8, "mentions": ["a"], "text": "hi",
                "chat_type": "channel", "date": "2026-09-10T10:00:00", "priority_score": 60,
                "priority_label": "high"}
        ping_id = asyncio.run(database.save_ping(base))
        asyncio.run(database.save_ping(dict(base, priority_score=15, priority_label="normal")))
        row = asyncio.run(database.get_ping_by_id(ping_id))
        self.assertEqual((row["priority_score"], row["priority_label"]), (15, "normal"))


if __name__ == "__main__":
    unittest.main()
