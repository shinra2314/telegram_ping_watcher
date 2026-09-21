"""The Mini App's statistics: counted over a key's accounts, and the prefs patch.

`build_panel_report` runs real SQL over a temp database — the whole point of it
is the account cut, and a mocked query would prove nothing about that.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.analytics import clean_names, fill_days, invalidate_analytics_cache  # noqa: E402
from pulse_desk.bot_prefs import DEFAULT_MEMBER_PREFS, apply_prefs_patch  # noqa: E402

try:
    import database
except ModuleNotFoundError as exc:
    if exc.name != "aiosqlite":
        raise
    database = None


class PrefsPatchTests(unittest.TestCase):
    ALLOWED = ["mentions", "wins"]

    def test_none_means_leave_as_is(self):
        prefs = dict(DEFAULT_MEMBER_PREFS, wins=False)
        self.assertEqual(apply_prefs_patch(prefs, {"wins": None, "mentions": None}, self.ALLOWED), prefs)

    def test_granted_types_and_mute_switch(self):
        got = apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"wins": False, "muted": True}, self.ALLOWED)
        self.assertEqual((got["wins"], got["muted"]), (False, True))

    def test_mute_is_always_the_members_own(self):
        self.assertTrue(apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"muted": True}, [])["muted"])

    def test_a_type_outside_the_grant_is_refused(self):
        with self.assertRaises(PermissionError):
            apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"digest": True}, self.ALLOWED)

    def test_min_score_is_clamped(self):
        self.assertEqual(apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"min_score": 250}, [])["min_score"], 100)
        self.assertEqual(apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"min_score": -3}, [])["min_score"], 0)

    def test_autoclean_takes_only_the_offered_choices(self):
        self.assertEqual(apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"autoclean_hours": 47}, [])["autoclean_hours"], 47)
        with self.assertRaises(ValueError):
            apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"autoclean_hours": 5}, [])

    def test_unknown_keys_are_ignored(self):
        self.assertNotIn("admin", apply_prefs_patch(DEFAULT_MEMBER_PREFS, {"admin": True}, []))


class ReportHelperTests(unittest.TestCase):
    def test_names_are_stored_form(self):
        self.assertEqual(clean_names(["@MuverGT", "muvergt", " ", "B"]), ["b", "muvergt"])

    def test_fill_days_keeps_quiet_days(self):
        rows = [{"day": "2026-09-15", "total": 4, "wins": 1, "giveaways": 2}]
        got = fill_days(rows, date(2026, 9, 17), days=4)
        self.assertEqual([d["day"] for d in got], ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"])
        self.assertEqual([d["total"] for d in got], [0, 4, 0, 0])


@unittest.skipIf(database is None, "aiosqlite is not installed")
class PanelReportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from pulse_desk.analytics import build_panel_report

        self.build = build_panel_report
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "panel.db"
        await database.init_db()
        invalidate_analytics_cache()
        now = datetime.now().replace(microsecond=0)

        async def ping(i: int, mentions: list[str], chat: str, is_win: bool, hours_ago: float):
            moment = (now - timedelta(hours=hours_ago)).isoformat()
            await database.save_ping({
                "date": moment, "chat": chat, "chat_id": i, "sender": f"author{i % 2}", "message_id": i,
                "mentions": mentions, "text": f"post {i}", "chat_type": "channel",
                "detected_at": moment, "is_giveaway": 1, "is_win": is_win,
            })

        await ping(1, ["mine"], "Mine chat", True, 2)
        await ping(2, ["mine"], "Mine chat", False, 30)
        await ping(3, ["other"], "Other chat", True, 3)
        await ping(4, ["other"], "Other chat", True, 24 * 40)

    async def asyncTearDown(self):
        invalidate_analytics_cache()
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_a_scoped_key_never_counts_other_accounts(self):
        report = await self.build(["@Mine"], ["mine"])
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["wins"], 1)
        self.assertEqual(report["summary"]["last_24h"], 1)
        self.assertEqual([c["chat"] for c in report["chats"]], ["Mine chat"])
        self.assertEqual(report["accounts"], [{"name": "mine", "mentions": 2, "wins": 1}])
        self.assertEqual(sum(d["total"] for d in report["daily"]), 2)
        self.assertEqual(sum(report["hours"]), 2)

    async def test_an_unscoped_key_counts_everything(self):
        report = await self.build([], ["mine", "other"])
        self.assertEqual(report["summary"]["total"], 4)
        self.assertEqual(report["summary"]["wins"], 3)
        # The 40-day-old win is in the totals but outside the 30-day breakdowns.
        self.assertEqual(sum(c["count"] for c in report["chats"]), 3)
        self.assertEqual([a["name"] for a in report["accounts"]], ["other", "mine"])
        self.assertEqual(len(report["daily"]), 14)

    async def test_the_period_widens_the_breakdowns_only(self):
        wide = await self.build([], ["mine", "other"], 90)
        self.assertEqual((wide["window_days"], sum(c["count"] for c in wide["chats"])), (90, 4))
        self.assertEqual(wide["summary"]["total"], 4)
        # An unlisted period falls back to the default, not to "everything".
        self.assertEqual((await self.build([], [], 1000))["window_days"], 30)


if __name__ == "__main__":
    unittest.main()
