from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    import database
except ModuleNotFoundError as exc:  # pure tests run without project deps installed
    if exc.name != "aiosqlite":
        raise
    database = None


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BotMemberBlockPersistsTests(unittest.TestCase):
    def _run(self, coro_factory):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                database.DB_PATH = Path(tmp) / "test.db"
                try:
                    await database.init_db()
                    return await coro_factory()
                finally:
                    database.DB_PATH = original_path

        return asyncio.run(scenario())

    def test_new_member_defaults_unblocked(self):
        async def body():
            await database.upsert_bot_member(
                tg_id=1, tg_username="u", name="N", key_id=None, role="viewer"
            )
            member = await database.get_bot_member(1)
            self.assertFalse(member["blocked"])

        self._run(body)

    def test_re_redeem_does_not_clear_block(self):
        """A blocked member re-redeeming a key must stay blocked (persistence)."""

        async def body():
            await database.upsert_bot_member(
                tg_id=7, tg_username="u", name="N", key_id=None, role="viewer"
            )
            await database.set_bot_member_blocked(7, True)
            # Re-interaction / key re-redeem upserts the member again.
            await database.upsert_bot_member(
                tg_id=7, tg_username="u2", name="N2", key_id=None, role="admin"
            )
            member = await database.get_bot_member(7)
            self.assertTrue(member["blocked"], "block must survive re-redeem")
            # Profile fields still refresh on re-interaction.
            self.assertEqual(member["tg_username"], "u2")
            self.assertEqual(member["name"], "N2")

        self._run(body)


if __name__ == "__main__":
    unittest.main()
