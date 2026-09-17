"""Dead channels: found across every account, ranked by whether they ever paid."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database  # noqa: E402
from pulse_desk import giveaway_ops  # noqa: E402
from pulse_desk.app_ctx import state  # noqa: E402


class FakeClient:
    def __init__(self, name, dialogs):
        self._session_name_custom = name
        self._dialogs = dialogs

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs:
            yield dialog


class DeadChannelTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_dead_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "dead.db"
        asyncio.run(database.init_db())
        self._clients = list(state.clients)
        state.clients.clear()

    def tearDown(self):
        state.clients.clear()
        state.clients.extend(self._clients)
        state.bot_cleanup_cache = {}
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def test_marked_id(self):
        self.assertEqual(giveaway_ops.marked_channel_id(1987654321), -1001987654321)
        self.assertEqual(giveaway_ops.marked_channel_id(-1001987654321), -1001987654321)

    def test_candidates_merge_accounts_and_rank_paying_channels_last(self):
        asyncio.run(database.save_ping({
            "chat": "Paying", "chat_id": -100222, "message_id": 1, "mentions": ["a"], "text": "win",
            "chat_type": "channel", "is_win": True,
        }))
        dialogs = [SimpleNamespace(cid=111, title="Silent", days=40), SimpleNamespace(cid=222, title="Paying", days=90)]

        def fake_candidate(dialog, _days):
            return {"chat_id": dialog.cid, "title": dialog.title, "inactive_days": dialog.days}

        state.clients.extend([FakeClient("acc1", dialogs), FakeClient("acc2", dialogs[:1])])
        with mock.patch.object(giveaway_ops, "inactive_channel_candidate", fake_candidate):
            data = asyncio.run(giveaway_ops.cleanup_candidates())
        items = data["candidates"]
        self.assertEqual([i["title"] for i in items], ["Silent", "Paying"])
        self.assertEqual(sorted(items[0]["accounts"]), ["acc1", "acc2"])
        self.assertEqual(items[1]["wins"], 1)
        self.assertIn(111, state.bot_cleanup_cache["items"])

    def test_confirm_text_warns_about_wins(self):
        from pulse_desk.bot.sections.giveaways import cleanup_text, leave_confirm_text

        text = leave_confirm_text(222, {"title": "Paying", "accounts": ["acc1"], "inactive_days": 90, "wins": 3})
        self.assertIn("Побед за 90 дней: 3", text)
        self.assertIn("`acc1`", text)
        summary = cleanup_text({"candidates": [{"wins": 0}, {"wins": 2}], "accounts": 2, "inactive_days": 14})
        self.assertIn("без побед за 90 дн: `1`", summary)

    def test_keyboard_marks_paying_channels(self):
        from pulse_desk.bot.keyboards import cleanup_keyboard

        rows = cleanup_keyboard([{"chat_id": 1, "title": "A", "inactive_days": 20, "wins": 2,
                                  "accounts": ["x", "y"]}])
        self.assertTrue(rows[0][0].text.startswith("🏆2"))
        self.assertIn("×2", rows[0][0].text)


if __name__ == "__main__":
    unittest.main()
