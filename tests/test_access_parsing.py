from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_prefs import (
    next_hhmm_datetime,
    parse_duration_to_seconds,
    parse_weekday_spec,
)


class WeekdaySpecTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(parse_weekday_spec("mon-fri"), [1, 2, 3, 4, 5])

    def test_list(self):
        self.assertEqual(parse_weekday_spec("sat,sun"), [6, 7])

    def test_single(self):
        self.assertEqual(parse_weekday_spec("wed"), [3])

    def test_wrap_range(self):
        self.assertEqual(parse_weekday_spec("fri-mon"), [1, 5, 6, 7])

    def test_mixed_and_dedup(self):
        self.assertEqual(parse_weekday_spec("mon-tue,tue,fri"), [1, 2, 5])

    def test_invalid_returns_empty(self):
        for bad in ("", "funday", "mon-funday", "1-3", "  "):
            self.assertEqual(parse_weekday_spec(bad), [], bad)


class DurationTests(unittest.TestCase):
    def test_simple_units(self):
        self.assertEqual(parse_duration_to_seconds("2h"), 7200)
        self.assertEqual(parse_duration_to_seconds("30m"), 1800)
        self.assertEqual(parse_duration_to_seconds("1d"), 86400)

    def test_combined(self):
        self.assertEqual(parse_duration_to_seconds("1h30m"), 5400)

    def test_invalid(self):
        for bad in ("", "2", "abc", "2x", "h", "2hh"):
            self.assertIsNone(parse_duration_to_seconds(bad), bad)


class NextHhmmTests(unittest.TestCase):
    def test_future_today(self):
        now = datetime(2026, 6, 15, 8, 0)
        self.assertEqual(next_hhmm_datetime(now, "18:00"), datetime(2026, 6, 15, 18, 0))

    def test_past_rolls_tomorrow(self):
        now = datetime(2026, 6, 15, 20, 0)
        self.assertEqual(next_hhmm_datetime(now, "18:00"), datetime(2026, 6, 16, 18, 0))

    def test_invalid_returns_none(self):
        self.assertIsNone(next_hhmm_datetime(datetime(2026, 6, 15, 8, 0), "bogus"))


if __name__ == "__main__":
    unittest.main()
