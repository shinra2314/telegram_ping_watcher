"""Per-key send delay: the durable queue and the broadcast split that fills it."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.bot_permissions import dump_permissions, full_permissions, set_delay  # noqa: E402

try:
    import database
except ModuleNotFoundError as exc:  # pure tests run without project deps installed
    if exc.name != "aiosqlite":
        raise
    database = None

if database is not None:
    from pulse_desk import bot_notify
    from pulse_desk.app_ctx import state


def _past(minutes: int = 5) -> str:
    return (datetime.now() - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def _future(minutes: int = 60) -> str:
    return (datetime.now() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


@unittest.skipIf(database is None, "aiosqlite is not installed")
class PendingSendQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pending.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_only_elapsed_rows_come_due(self):
        await database.queue_pending_send(111, _past(), "готово", token="tok", link="https://t.me/x")
        await database.queue_pending_send(222, _future(), "ещё рано", token="tok")
        due = await database.get_due_pending_sends()
        self.assertEqual([r["tg_id"] for r in due], [111])
        self.assertEqual(due[0]["message"], "готово")
        self.assertEqual(due[0]["link"], "https://t.me/x")

    async def test_delivered_row_leaves_the_queue(self):
        row_id = await database.queue_pending_send(111, _past(), "msg")
        await database.mark_pending_send_result(row_id, sent=True)
        self.assertEqual(await database.get_due_pending_sends(), [])

    async def test_failed_row_stays_queued_and_counts_attempts(self):
        row_id = await database.queue_pending_send(111, _past(), "msg")
        await database.mark_pending_send_result(row_id, sent=False)
        due = await database.get_due_pending_sends()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["attempts"], 1)

    async def test_cancel_by_token_drops_only_undelivered_copies(self):
        await database.queue_pending_send(111, _past(), "a", token="tok")
        sent_id = await database.queue_pending_send(222, _past(), "b", token="tok")
        await database.mark_pending_send_result(sent_id, sent=True)
        await database.queue_pending_send(333, _past(), "c", token="other")

        self.assertEqual(await database.cancel_pending_sends("tok"), 1)
        self.assertEqual([r["tg_id"] for r in await database.get_due_pending_sends()], [333])
        self.assertEqual(await database.cancel_pending_sends("tok"), 0, "cancelling twice is a no-op")
        self.assertEqual(await database.cancel_pending_sends(""), 0)

    async def test_count_tracks_outstanding_copies(self):
        await database.queue_pending_send(111, _future(), "a", token="tok")
        await database.queue_pending_send(222, _future(), "b", token="tok")
        self.assertEqual(await database.count_pending_sends("tok"), 2)
        await database.cancel_pending_sends("tok")
        self.assertEqual(await database.count_pending_sends("tok"), 0)
        self.assertEqual(await database.count_pending_sends(""), 0)

    async def test_prune_keeps_rows_that_are_still_waiting(self):
        settled = await database.queue_pending_send(111, _past(), "old")
        await database.mark_pending_send_result(settled, sent=True)
        await database.queue_pending_send(222, _past(), "still waiting")
        import aiosqlite as _aiosqlite

        async with _aiosqlite.connect(database.DB_PATH) as db:
            await db.execute("UPDATE bot_pending_sends SET created_at = ?", ("2000-01-01T00:00:00",))
            await db.commit()
        self.assertEqual(await database.prune_pending_sends(days=7), 1)
        self.assertEqual(len(await database.get_due_pending_sends()), 1)


class _FakeBot:
    """Minimal Telethon stand-in: records sends, hands out message ids."""

    def __init__(self):
        self.sent: list[dict] = []
        self._next_id = 100

    def is_connected(self):
        return True

    async def connect(self):
        return True

    async def send_message(self, target, message, buttons=None, link_preview=False, file=None):
        self._next_id += 1
        self.sent.append({"target": target, "message": message, "buttons": buttons, "file": file})
        return type("Sent", (), {"id": self._next_id})()


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BroadcastDelaySplitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        self.old_client = state.bot_client
        self.old_admin = bot_notify.ADMIN_ID
        database.DB_PATH = Path(self.tmp.name) / "split.db"
        await database.init_db()
        self.bot = _FakeBot()
        state.bot_client = self.bot
        bot_notify.ADMIN_ID = 777

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        state.bot_client = self.old_client
        bot_notify.ADMIN_ID = self.old_admin
        self.tmp.cleanup()

    async def _member_with_delay(self, tg_id: int, minutes: int) -> None:
        secret = f"secret-for-{tg_id}-0000000"
        perms = set_delay(full_permissions(), minutes)
        key = await database.create_bot_key(f"k{tg_id}", secret, "viewer", None, dump_permissions(perms))
        await database.upsert_bot_member(tg_id, "u", "U", key["id"], "viewer", dump_permissions(perms))

    async def test_delayed_members_are_queued_not_messaged(self):
        await self._member_with_delay(111, 0)
        await self._member_with_delay(222, 15)

        delivered = await bot_notify.broadcast_member_notification("msg", None, notif_type="win", token="tok")

        self.assertEqual([tg for tg, _mid in delivered], [111], "only the no-delay member is messaged now")
        self.assertEqual([s["target"] for s in self.bot.sent], [111])
        self.assertEqual(await database.count_pending_sends("tok"), 1, "the delayed member is queued")
        self.assertEqual(await database.get_due_pending_sends(), [], "and not due yet")

    async def test_queued_copy_carries_the_link_for_later_rebuild(self):
        from telethon import Button

        await self._member_with_delay(222, 5)
        buttons = [[Button.url("Открыть в Telegram", "https://t.me/c/1/2")]]
        await bot_notify.broadcast_member_notification("msg", buttons, notif_type="win", token="tok")

        import aiosqlite as _aiosqlite

        async with _aiosqlite.connect(database.DB_PATH) as db:
            db.row_factory = _aiosqlite.Row
            row = dict(await (await db.execute("SELECT * FROM bot_pending_sends")).fetchone())
        self.assertEqual(row["link"], "https://t.me/c/1/2")
        self.assertEqual(row["notif_type"], "win")
        self.assertEqual(row["token"], "tok")

    async def test_delivering_a_queued_copy_reuses_the_hide_token(self):
        await self._member_with_delay(222, 5)
        await bot_notify.broadcast_member_notification("msg", None, notif_type="win", token="tok")
        import aiosqlite as _aiosqlite

        async with _aiosqlite.connect(database.DB_PATH) as db:
            await db.execute("UPDATE bot_pending_sends SET send_at = ?", (_past(),))
            await db.commit()

        due = await database.get_due_pending_sends()
        self.assertEqual(len(due), 1)
        message_id = await bot_notify.deliver_pending_send(due[0])
        self.assertIsNotNone(message_id)
        await database.mark_pending_send_result(int(due[0]["id"]), sent=True)
        await database.save_broadcast_messages("tok", [(222, int(message_id))])

        saved = await database.get_broadcast_messages("tok")
        self.assertEqual([(r["tg_id"], r["message_id"]) for r in saved], [(222, message_id)])

    async def test_account_scoped_key_is_not_queued_for_other_accounts(self):
        secret = "scoped-secret-0000000"
        perms = set_delay(full_permissions(), 10)
        perms["accounts"] = ["muver"]
        key = await database.create_bot_key("scoped", secret, "viewer", None, dump_permissions(perms))
        await database.upsert_bot_member(333, "u", "U", key["id"], "viewer", dump_permissions(perms))

        await bot_notify.broadcast_member_notification(
            "msg", None, notif_type="win", token="tok", mentions=json.dumps(["@someone_else"])
        )
        self.assertEqual(await database.count_pending_sends("tok"), 0)

        await bot_notify.broadcast_member_notification(
            "msg", None, notif_type="win", token="tok2", mentions=json.dumps(["@muver"])
        )
        self.assertEqual(await database.count_pending_sends("tok2"), 1)


if __name__ == "__main__":
    unittest.main()
