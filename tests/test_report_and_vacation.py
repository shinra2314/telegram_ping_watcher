"""Weekly/monthly report numbers and schedule; vacation routing and delegation."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import report, vacation  # noqa: E402

NOW = datetime(2026, 9, 14, 11, 0)  # a Monday


class PeriodTests(unittest.TestCase):
    def test_previous_week_is_monday_to_sunday(self):
        start, end, label = report.period("week", NOW, previous=True)
        self.assertEqual((start, end), (datetime(2026, 9, 7), datetime(2026, 9, 14)))
        self.assertEqual(label, "07.09–13.09")

    def test_previous_month(self):
        start, end, label = report.period("month", datetime(2026, 10, 1, 10, 30), previous=True)
        self.assertEqual((start, end, label), (datetime(2026, 9, 1), datetime(2026, 10, 1), "сентябрь 2026"))

    def test_current_week_is_last_seven_days(self):
        start, end, _label = report.period("week", NOW)
        self.assertEqual(start, datetime(2026, 9, 8))
        self.assertEqual(end, NOW)


class BuildTests(unittest.TestCase):
    def test_counts_rates_channels_and_book(self):
        start, end, label = report.period("week", NOW, previous=True)
        pings = [
            {"is_win": 1, "action_status": "claimed", "chat": "A", "detected_at": "2026-09-07T10:00:00"},
            {"is_win": 1, "action_status": "claim_prize", "chat": "A", "detected_at": "2026-09-08T10:00:00"},
            {"is_win": 1, "action_status": "scam", "chat": "B", "detected_at": "2026-09-13T23:00:00+03:00"},
            {"is_win": 1, "action_status": "claimed", "chat": "A", "duplicate_of": 1},
            {"is_giveaway": 1}, {}, {},
        ]
        book = SimpleNamespace(
            journal=[SimpleNamespace(day=date(2026, 9, 8), value=12.5), SimpleNamespace(day=date(2026, 8, 30), value=99)],
            months=[SimpleNamespace(month="2026-09", total=40.0, paid_at=date(2026, 9, 10)),
                    SimpleNamespace(month="2026-09", total=10.0, paid_at=None)],
        )
        data = report.build_report("week", start, end, label, pings, [{"is_win": 1}],
                                   unclaimed={"usd": 9.5, "unpriced": 2}, book=book)
        self.assertEqual((data.wins, data.claimed, data.scam, data.open_wins), (3, 1, 1, 1))
        self.assertEqual((data.giveaways, data.mentions), (1, 2))
        self.assertEqual(data.top_channels[0], ("A", 2))
        self.assertEqual(data.daily_wins[0], 1)
        self.assertEqual(data.book_won_usd, 12.5)
        self.assertEqual((data.book_payout_usd, data.book_paid_usd), (50.0, 40.0))
        text = report.report_text(data)
        self.assertIn("33%", text)
        self.assertIn("+200%", text)
        self.assertIn("$9.50", text)

    def test_schedule(self):
        self.assertEqual(report.due_reports(NOW, {}), ["week"])
        self.assertEqual(report.due_reports(NOW, {"week": report.week_key(NOW)}), [])
        self.assertEqual(report.due_reports(NOW.replace(hour=9), {}), [])
        first = datetime(2026, 10, 1, 10, 20)
        self.assertEqual(report.due_reports(first, {}), ["month"])
        # Sent on Monday → nothing on Tuesday.
        tuesday = datetime(2026, 9, 15, 11, 0)
        self.assertEqual(report.due_reports(tuesday, {"week": report.week_key(tuesday)}), [])

    def test_a_day_the_pc_stayed_off_is_caught_up(self):
        # PC off for the whole Monday / the whole 1st: the report still goes
        # out when it comes up within three days of the slot, not later.
        self.assertEqual(report.due_reports(datetime(2026, 9, 15, 11, 0), {}), ["week"])
        self.assertEqual(report.due_reports(datetime(2026, 9, 17, 10, 0), {}), ["week"])
        self.assertEqual(report.due_reports(datetime(2026, 9, 17, 11, 0), {}), [])
        self.assertEqual(report.due_reports(datetime(2026, 10, 3, 18, 0), {"week": "2026-W40"}), ["month"])
        self.assertEqual(report.due_reports(datetime(2026, 10, 4, 11, 0), {"week": "2026-W40"}), [])
        # A new ISO year: Monday 28.12.2026 is week 53 of 2026, 04.01.2027 week 1.
        self.assertEqual(report.week_key(datetime(2026, 12, 30)), "2026-W53")
        self.assertEqual(report.due_reports(datetime(2027, 1, 5, 12, 0), {"week": "2026-W53"}), ["week"])

    def test_card_renders_or_degrades(self):
        from pulse_desk.bot.render.screens import build_report_card

        start, end, label = report.period("week", NOW, previous=True)
        data = report.build_report("week", start, end, label, [], [])
        with tempfile.TemporaryDirectory() as tmp:
            result = build_report_card(data, Path(tmp) / "r.png")
            if result is not None:
                self.assertTrue(Path(result).exists())


class VacationRuleTests(unittest.TestCase):
    def test_routing(self):
        cfg = vacation.start(vacation.normalize(None), NOW, 7)
        cfg["delegate"] = {"tg_id": 42, "name": "Друг", "prev_role": "viewer"}
        self.assertFalse(vacation.owner_receives("mention", cfg, NOW))
        self.assertTrue(vacation.owner_receives("win", cfg, NOW))
        self.assertTrue(vacation.owner_receives("", cfg, NOW))
        self.assertEqual(vacation.delegate_receives("win", cfg, NOW), 42)
        self.assertIsNone(vacation.delegate_receives("market", cfg, NOW))
        cfg["mute_wins"] = True
        self.assertFalse(vacation.owner_receives("win", cfg, NOW))

    def test_expiry(self):
        cfg = vacation.start(vacation.normalize(None), NOW, 3)
        later = datetime(2026, 9, 18, 12, 0)
        self.assertTrue(vacation.is_active(cfg, NOW))
        self.assertFalse(vacation.is_active(cfg, later))
        self.assertTrue(vacation.expired(cfg, later))
        self.assertTrue(vacation.owner_receives("mention", cfg, later))
        self.assertFalse(vacation.expired(vacation.stop(cfg), later))


class DelegationTests(unittest.TestCase):
    def setUp(self):
        import database

        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_vac_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "vac.db"
        asyncio.run(database.init_db())
        asyncio.run(database.upsert_bot_member(42, "friend", "Друг", None, "premium", ""))

    def tearDown(self):
        import database

        database.DB_PATH = self._old
        self._tmp.cleanup()

    def test_delegate_raised_then_restored_on_expiry(self):
        import database
        from pulse_desk.bot.sections import vacation as section

        cfg = vacation.start(vacation.normalize(None), NOW, 3)
        cfg["delegate"] = {"tg_id": 42, "name": "Друг", "prev_role": "premium"}
        asyncio.run(section.save(cfg))
        asyncio.run(section._elevate(cfg))
        self.assertEqual(asyncio.run(database.get_bot_member(42))["role"], "admin")

        async def fake_send(*_a, **_kw):
            return True

        with mock.patch("pulse_desk.bot_notify.send_admin_bot_message", fake_send), \
                mock.patch("pulse_desk.bot_notify.send_member_bot_message", fake_send):
            self.assertTrue(asyncio.run(section.check_expiry(datetime(2026, 9, 20))))
        self.assertEqual(asyncio.run(database.get_bot_member(42))["role"], "premium")
        self.assertFalse(asyncio.run(section.load())["active"])


if __name__ == "__main__":
    unittest.main()
