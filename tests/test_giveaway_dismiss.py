"""«✖ Убрать из очереди» in the bot: a tap closes the row, the page stays, undo brings it back."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import database  # noqa: E402
from _fakes import FakeEvent  # noqa: E402
from pulse_desk.app_ctx import state  # noqa: E402
from pulse_desk.bot.router import Click  # noqa: E402
from pulse_desk.bot.sections import giveaways  # noqa: E402
from pulse_desk.bot.undo import undo  # noqa: E402
from pulse_desk.bot_permissions import full_permissions  # noqa: E402


def press(data: str, role: str = "admin") -> FakeEvent:
    event = FakeEvent(data=data.encode())
    click = Click(event, data, role, full_permissions(), lambda _perms, _feature: True)
    asyncio.run(giveaways.handle(click))
    return event


def callbacks(event: FakeEvent) -> list[bytes]:
    rows = event.edits[-1]["kwargs"]["buttons"]
    return [b.data for row in rows for b in row]


class GiveawayDismissTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pulse_dismiss_")
        self._old = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "dismiss.db"
        asyncio.run(database.init_db())
        self._undo = dict(state.bot_undo)
        state.bot_undo.clear()
        self.ids = [
            asyncio.run(database.save_ping({
                "chat": f"Chat {n}", "chat_id": -100 - n, "message_id": n, "mentions": ["acc"],
                "text": "you won", "chat_type": "channel", "is_win": True,
            }))
            for n in (1, 2)
        ]

    def tearDown(self):
        state.bot_undo.clear()
        state.bot_undo.update(self._undo)
        database.DB_PATH = self._old
        self._tmp.cleanup()

    def queue_ids(self) -> list[int]:
        board = asyncio.run(database.get_giveaway_board(limit=50, include_outbox=False))
        return sorted(int(r["id"]) for r in board["buckets"]["need_action"])

    def test_feed_offers_tidy_mode_to_the_owner_only(self):
        self.assertIn(b"gw:x:d:0:-1:1", callbacks(press("gw:f:d:0:-1:1")))
        self.assertNotIn(b"gw:x:d:0:-1:1", callbacks(press("gw:f:d:0:-1:1", role="viewer")))

    def test_guest_cannot_remove(self):
        event = press(f"gw:rm:{self.ids[0]}:d:0:-1:1", role="viewer")
        self.assertEqual(event.answer_texts, ["Только владелец"])
        self.assertEqual(self.queue_ids(), sorted(self.ids))

    def test_tap_closes_the_row_and_redraws_tidy_mode_with_undo(self):
        target = self.ids[0]
        event = press(f"gw:rm:{target}:d:0:-1:1")
        self.assertEqual(self.queue_ids(), [self.ids[1]])
        ping = asyncio.run(database.get_ping_by_id(target))
        self.assertEqual((ping["giveaway_status"], ping["action_status"]), ("closed", "closed"))
        datas = callbacks(event)
        self.assertTrue(datas[0].startswith(b"ud:"))
        self.assertIn(f"gw:rm:{self.ids[1]}:d:0:-1:1".encode(), datas)
        self.assertNotIn(f"gw:rm:{target}:d:0:-1:1".encode(), datas)

        restored, back = asyncio.run(undo(event.sender_id, datas[0].decode().split(":")[1]))
        self.assertEqual((restored, back), (1, "gw:x:d:0:-1:1"))
        self.assertEqual(self.queue_ids(), sorted(self.ids))

    def test_unknown_row_is_reported(self):
        event = press("gw:rm:999999:d:0:-1:1")
        self.assertEqual(event.answer_texts, ["Розыгрыш не найден"])


if __name__ == "__main__":
    unittest.main()
