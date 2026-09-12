from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot.cards import roulette_card, roulette_reminder_card
from pulse_desk.roulette import (
    DEFAULT_ROULETTE_TIME,
    apply_checkin,
    checkin_moment,
    mark_sent,
    next_fire_at,
    normalize_roulette_cfg,
    roulette_due,
    seed_cfg,
    skip_today,
)


def cfg(**over) -> dict:
    base = {"enabled": True, "time": "12:35", "last_cycle": "2026-09-04", "last_click": "2026-09-04T12:35:00", "awaiting": False}
    base.update(over)
    return base


class NormalizeTests(unittest.TestCase):
    def test_missing_row_falls_back_to_the_seed_time(self):
        out = normalize_roulette_cfg(None)
        self.assertEqual(out["time"], DEFAULT_ROULETTE_TIME)
        self.assertTrue(out["enabled"])
        self.assertEqual(out["last_cycle"], "")

    def test_junk_time_falls_back_but_keeps_the_rest(self):
        out = normalize_roulette_cfg({"time": "99:99", "enabled": False, "last_cycle": "2026-09-04"})
        self.assertEqual(out["time"], DEFAULT_ROULETTE_TIME)
        self.assertFalse(out["enabled"])
        self.assertEqual(out["last_cycle"], "2026-09-04")

    def test_dotted_time_is_normalized(self):
        self.assertEqual(normalize_roulette_cfg({"time": "9.5"})["time"], "09:05")

    def test_seed_closes_the_day_of_its_own_click(self):
        seed = seed_cfg()
        self.assertEqual(seed["last_cycle"], seed["last_click"][:10])
        self.assertFalse(seed["awaiting"])


class DueTests(unittest.TestCase):
    def test_not_due_before_the_slot(self):
        self.assertFalse(roulette_due(datetime(2026, 9, 5, 12, 34), cfg()))

    def test_due_at_the_slot(self):
        self.assertTrue(roulette_due(datetime(2026, 9, 5, 12, 35), cfg()))

    def test_not_due_twice_in_one_day(self):
        now = datetime(2026, 9, 5, 12, 35)
        self.assertFalse(roulette_due(now, mark_sent(cfg(), now)))

    def test_catch_up_after_an_overnight_shutdown(self):
        # Slot at 03:00 while the PC was off; first tick after boot must fire.
        overnight = cfg(time="03:00", last_cycle="2026-09-03")
        self.assertTrue(roulette_due(datetime(2026, 9, 5, 10, 0), overnight))

    def test_catch_up_fires_only_once(self):
        now = datetime(2026, 9, 5, 10, 0)
        overnight = cfg(time="03:00", last_cycle="2026-09-03")
        self.assertFalse(roulette_due(now, mark_sent(overnight, now)))

    def test_disabled_never_fires(self):
        self.assertFalse(roulette_due(datetime(2026, 9, 5, 23, 59), cfg(enabled=False)))

    def test_checkin_closes_the_day_without_a_reminder(self):
        now = datetime(2026, 9, 5, 9, 0)
        self.assertFalse(roulette_due(datetime(2026, 9, 5, 23, 0), apply_checkin(cfg(), now)))

    def test_skip_silences_today_only(self):
        skipped = skip_today(cfg(), datetime(2026, 9, 5, 12, 40))
        self.assertFalse(roulette_due(datetime(2026, 9, 5, 20, 0), skipped))
        self.assertTrue(roulette_due(datetime(2026, 9, 6, 12, 35), skipped))


class NextFireTests(unittest.TestCase):
    def test_today_when_the_day_is_still_open(self):
        now = datetime(2026, 9, 5, 8, 0)
        self.assertEqual(next_fire_at(now, cfg()), datetime(2026, 9, 5, 12, 35))

    def test_tomorrow_once_the_day_is_closed(self):
        now = datetime(2026, 9, 5, 12, 40)
        self.assertEqual(next_fire_at(now, mark_sent(cfg(), now)), datetime(2026, 9, 6, 12, 35))

    def test_overdue_slot_stays_in_the_past(self):
        now = datetime(2026, 9, 5, 14, 0)
        self.assertLess(next_fire_at(now, cfg()), now)

    def test_disabled_has_no_next_fire(self):
        self.assertIsNone(next_fire_at(datetime(2026, 9, 5, 8, 0), cfg(enabled=False)))


class CheckinTests(unittest.TestCase):
    def test_reported_time_becomes_the_new_alarm(self):
        now = datetime(2026, 9, 5, 21, 50)
        out = apply_checkin(cfg(awaiting=True), checkin_moment(now, "21:47"))
        self.assertEqual(out["time"], "21:47")
        self.assertEqual(out["last_click"], "2026-09-05T21:47:00")
        self.assertFalse(out["awaiting"])
        self.assertEqual(next_fire_at(now, out), datetime(2026, 9, 6, 21, 47))

    def test_time_still_ahead_is_read_as_yesterday(self):
        # Reporting a 23:50 click at 00:30 must not book the alarm for 00:30.
        moment = checkin_moment(datetime(2026, 9, 6, 0, 30), "23:50")
        self.assertEqual(moment, datetime(2026, 9, 5, 23, 50))
        out = apply_checkin(cfg(), moment)
        self.assertEqual(out["last_cycle"], "2026-09-05")
        self.assertTrue(roulette_due(datetime(2026, 9, 6, 23, 50), out))

    def test_garbage_is_rejected(self):
        self.assertIsNone(checkin_moment(datetime(2026, 9, 5, 12, 0), "потом"))
        self.assertIsNone(checkin_moment(datetime(2026, 9, 5, 12, 0), "25:00"))

    def test_mark_sent_starts_waiting_for_a_time(self):
        out = mark_sent(cfg(), datetime(2026, 9, 5, 12, 35))
        self.assertTrue(out["awaiting"])
        self.assertEqual(out["last_cycle"], "2026-09-05")


class CardTests(unittest.TestCase):
    def test_panel_shows_time_last_click_and_next_run(self):
        text = roulette_card(cfg(), datetime(2026, 9, 5, 8, 0))
        self.assertIn("12:35", text)
        self.assertIn("04.09 12:35", text)
        self.assertIn("сегодня 12:35", text)

    def test_panel_says_tomorrow_once_the_day_is_closed(self):
        now = datetime(2026, 9, 5, 12, 40)
        self.assertIn("завтра 12:35", roulette_card(mark_sent(cfg(), now), now))

    def test_panel_flags_a_pending_answer(self):
        now = datetime(2026, 9, 5, 12, 40)
        self.assertIn("Жду время", roulette_card(mark_sent(cfg(), now), now))

    def test_panel_reports_a_disabled_reminder(self):
        self.assertIn("выкл", roulette_card(cfg(enabled=False), datetime(2026, 9, 5, 8, 0)))

    def test_reminder_card_mentions_the_previous_click(self):
        text = roulette_reminder_card(cfg(), datetime(2026, 9, 5, 12, 35))
        self.assertIn("04.09 12:35", text)
        self.assertIn("21:47", text)

    def test_cards_survive_an_empty_config(self):
        empty = normalize_roulette_cfg(None)
        now = datetime(2026, 9, 5, 8, 0)
        self.assertIn("—", roulette_card(empty, now))
        self.assertIn("—", roulette_reminder_card(empty, now))


if __name__ == "__main__":
    unittest.main()
