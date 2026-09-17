"""One-time invites: a key with max_uses closes after its first person."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database  # noqa: E402


class OneTimeKeyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_keys_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "keys.db"
        asyncio.run(database.init_db())
        key = asyncio.run(database.create_bot_key("friend", "s3cret", "viewer", None, ""))
        self.key_id = int(key["id"])

    def tearDown(self):
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def _join(self, tg_id: int):
        asyncio.run(database.upsert_bot_member(tg_id, "", "", self.key_id, "viewer", ""))

    def test_unlimited_key_keeps_working(self):
        self._join(1)
        self._join(2)
        self.assertIsNotNone(asyncio.run(database.get_bot_key_by_secret("s3cret", 3)))

    def test_one_time_key_closes_after_first_person(self):
        asyncio.run(database.set_bot_key_max_uses(self.key_id, 1))
        self.assertIsNotNone(asyncio.run(database.get_bot_key_by_secret("s3cret", 1)))
        self._join(1)
        # A second person is refused…
        self.assertIsNone(asyncio.run(database.get_bot_key_by_secret("s3cret", 2)))
        self.assertIsNone(asyncio.run(database.get_bot_key_by_secret("s3cret")))
        # …the person who used it can still /start with the same link.
        self.assertIsNotNone(asyncio.run(database.get_bot_key_by_secret("s3cret", 1)))

    def test_badge_and_panel_show_usage(self):
        from pulse_desk.bot.cards import key_panel_card, key_state_badge
        from pulse_desk.bot.keyboards import key_panel_keyboard

        key = {"id": 3, "label": "x", "max_uses": 1, "member_count": 1}
        self.assertEqual(key_state_badge(key), "🎟 использован")
        self.assertIn("Активаций: `1/1`", key_panel_card(key, {}, []))
        datas = [b.data for row in key_panel_keyboard(key, {}, 0) for b in row]
        self.assertIn(b"key:once:3", datas)
        self.assertEqual(key_state_badge({"id": 3, "max_uses": 0, "member_count": 5}), "🟢 активен")


if __name__ == "__main__":
    unittest.main()
