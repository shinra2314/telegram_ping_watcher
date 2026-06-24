from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.giveaways import CHECK_KEYWORDS_DEFAULT, is_check_text

try:
    import database
except ModuleNotFoundError as exc:  # Allows parser tests to run without project deps.
    if exc.name != "aiosqlite":
        raise
    database = None


class CheckMatcherTests(unittest.TestCase):
    kw = CHECK_KEYWORDS_DEFAULT

    def test_matches_bare_check(self):
        self.assertTrue(is_check_text("чек на 5 TON, забирай", self.kw))

    def test_matches_case_insensitive_and_emoji(self):
        self.assertTrue(is_check_text("💸 Чек: t.me/CryptoBot?start=CQ", self.kw))

    def test_matches_multicheck(self):
        self.assertTrue(is_check_text("раздаю мультичек на 20 человек", self.kw))

    def test_matches_inflections(self):
        self.assertTrue(is_check_text("два чека для вас", self.kw))
        self.assertTrue(is_check_text("свежие чеки тут", self.kw))
        self.assertTrue(is_check_text("ещё мультичеки", self.kw))

    def test_no_false_positive_on_substring(self):
        # "человечек" ends with "чек" but must not match (word boundary).
        self.assertFalse(is_check_text("маленький человечек", self.kw))

    def test_no_match_on_verb_forms(self):
        # "чекать"/"чекни"/… are verbs ("to check it out"), not redeemable
        # checks — they must not trigger a check notification.
        for phrase in (
            "го чекать профиль",
            "чекни личку",
            "я чекаю каналы целыми днями",
            "надо чекнуть бота",
            "чекаем дальше",
        ):
            self.assertFalse(is_check_text(phrase, self.kw), msg=phrase)

    def test_still_matches_noun_cases(self):
        # Noun inflections stay matched after the verb-exclusion fix.
        for phrase in ("приз в чеке", "рад чеку", "пачка чеков"):
            self.assertTrue(is_check_text(phrase, self.kw), msg=phrase)

    def test_no_match_without_keyword(self):
        self.assertFalse(is_check_text("обычное сообщение без подарков", self.kw))

    def test_empty_inputs(self):
        self.assertFalse(is_check_text("", self.kw))
        self.assertFalse(is_check_text("чек", []))


class CheckFilterDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if database is None:
            self.skipTest("aiosqlite is not installed in this Python environment")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_check_flag_persists_and_filters(self):
        await database.save_ping({
            "chat": "Crypto Channel",
            "chat_id": 1,
            "message_id": 10,
            "link": "https://t.me/crypto/10",
            "text": "чек на 5 TON",
            "chat_type": "channel",
            "detected_at": "2026-06-23T10:00:00",
            "is_check": True,
        })
        await database.save_ping({
            "chat": "Other Channel",
            "chat_id": 2,
            "message_id": 20,
            "link": "https://t.me/other/20",
            "text": "обычный пост",
            "chat_type": "channel",
            "detected_at": "2026-06-23T10:01:00",
            "is_check": False,
        })
        rows = await database.get_pings(chat_type="check")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chat"], "Crypto Channel")
        self.assertEqual(rows[0]["is_check"], 1)


class PurgeStaleChecksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if database is None:
            self.skipTest("aiosqlite is not installed in this Python environment")
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def _save_check(self, message_id, detected_at, *, text, is_win=False):
        return await database.save_ping({
            "chat": "Crypto Channel",
            "chat_id": message_id,
            "message_id": message_id,
            "link": f"https://t.me/crypto/{message_id}",
            "text": text,
            "chat_type": "channel",
            "detected_at": detected_at,
            "is_check": True,
            "is_win": is_win,
        })

    async def test_purges_only_stale_pure_checks(self):
        now = datetime.now()
        stale = (now - timedelta(hours=2)).replace(microsecond=0).isoformat()
        fresh = (now - timedelta(minutes=5)).replace(microsecond=0).isoformat()
        await self._save_check(10, stale, text="чек на 5 TON")          # stale pure -> deleted
        await self._save_check(11, fresh, text="свежий чек на 5 TON")   # fresh -> kept
        fav_id = await self._save_check(12, stale, text="избранный чек")
        await database.update_ping_meta(fav_id, is_favorite=True)       # stale favorite -> kept
        await self._save_check(13, stale, text="победный чек", is_win=True)  # stale win -> kept

        deleted = await database.purge_stale_checks(minutes=60)
        self.assertEqual(deleted, 1)

        remaining = await database.get_pings(chat_type="check", limit=100)
        self.assertEqual({r["message_id"] for r in remaining}, {11, 12, 13})

        # FTS rows must not outlive their pings (no triggers; cleaned manually).
        from database import _core
        async with _core._connect() as db:
            fts_ids = {r[0] for r in await (await db.execute("SELECT rowid FROM pings_fts")).fetchall()}
            ping_ids = {r[0] for r in await (await db.execute("SELECT id FROM pings")).fetchall()}
        self.assertEqual(len(ping_ids), 3)
        self.assertEqual(fts_ids, ping_ids)

    async def test_purge_noop_when_all_fresh(self):
        fresh = (datetime.now() - timedelta(minutes=5)).replace(microsecond=0).isoformat()
        await self._save_check(20, fresh, text="чек на 5 TON")
        self.assertEqual(await database.purge_stale_checks(minutes=60), 0)


if __name__ == "__main__":
    unittest.main()
