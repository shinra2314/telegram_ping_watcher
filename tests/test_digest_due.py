"""Digest scheduling: one send per slot, catching up a slot the PC slept through."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.digest import digest_due, digest_seed_slot, digest_slot  # noqa: E402

CFG = {"enabled": True, "time": "10:00"}


def at(hh: int, mm: int, day: int = 13) -> datetime:
    return datetime(2026, 9, day, hh, mm)


class DigestDueTests(unittest.TestCase):
    def test_fires_at_the_slot(self):
        self.assertTrue(digest_due(at(10, 0), CFG, "2026-09-12"))

    def test_not_before_the_slot(self):
        self.assertFalse(digest_due(at(9, 59), CFG, "2026-09-12"))

    def test_once_per_slot(self):
        self.assertFalse(digest_due(at(10, 1), CFG, "2026-09-13"))

    def test_pc_switched_on_late_still_gets_it(self):
        # Boot at 10:47 (seen on real mornings) — the old loop lost this day.
        self.assertTrue(digest_due(at(10, 47), CFG, "2026-09-12"))

    def test_evening_boot_does_not_send_the_morning_digest(self):
        self.assertFalse(digest_due(at(19, 0), CFG, "2026-09-12"))

    def test_disabled(self):
        self.assertFalse(digest_due(at(10, 0), {"enabled": False, "time": "10:00"}, "2026-09-12"))

    def test_slot_near_midnight_crosses_the_day(self):
        cfg = {"enabled": True, "time": "23:30"}
        self.assertEqual(digest_slot(at(1, 0, 14), "23:30"), datetime(2026, 9, 13, 23, 30))
        self.assertTrue(digest_due(at(1, 0, 14), cfg, "2026-09-12"))
        self.assertFalse(digest_due(at(1, 0, 14), cfg, "2026-09-13"))

    def test_seed_counts_the_latest_slot_as_handled(self):
        # Upgrading at 15:00, after the old loop already sent today's digest.
        seed = digest_seed_slot(at(15, 0), CFG)
        self.assertEqual(seed, "2026-09-13")
        self.assertFalse(digest_due(at(15, 0), CFG, seed))
        # Upgrading before the slot: today's digest still goes out.
        early = digest_seed_slot(at(9, 0), CFG)
        self.assertTrue(digest_due(at(10, 0), CFG, early))


class DowntimeCardTests(unittest.TestCase):
    def test_counts_and_span(self):
        from pulse_desk.bot.cards import downtime_catchup_card

        pings = [{"is_win": 1, "is_giveaway": 1}, {"is_giveaway": 1}, {}, {}]
        text = downtime_catchup_card(at(0, 31), at(9, 47), pings)
        self.assertIn("00:31–09:47", text)
        self.assertIn("9 ч 16 мин", text)
        self.assertIn("Победы: `1`", text)
        self.assertIn("Розыгрыши: `1`", text)
        self.assertIn("Упоминания: `2`", text)

    def test_nothing_found_says_so(self):
        from pulse_desk.bot.cards import downtime_catchup_card

        text = downtime_catchup_card(at(23, 50, 12), at(9, 47), [])
        self.assertIn("12.09 23:50", text)
        self.assertIn("ничего не нашлось", text)


if __name__ == "__main__":
    unittest.main()
