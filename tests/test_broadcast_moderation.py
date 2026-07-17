"""Broadcast moderation: pending queue, card builder, moderated enqueue, approval loop."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
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

if database is not None:
    from pulse_desk import bot_notify, loops
    from pulse_desk.app_ctx import state
    from pulse_desk.bot_notify import build_ping_card


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


class FakeSentMessage:
    def __init__(self, message_id: int = 42):
        self.id = message_id


class FakeBotClient:
    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    def is_connected(self) -> bool:
        return True

    async def send_message(self, target, message, buttons=None, link_preview=False, file=None):
        self.sent.append({"target": target, "message": message, "buttons": buttons, "file": file})
        return FakeSentMessage(100 + len(self.sent))

    async def edit_message(self, target, message_id, text, buttons=None, link_preview=False):
        self.edited.append({"target": target, "message_id": message_id, "text": text, "buttons": buttons})


def _record(**overrides) -> dict:
    base = {
        "chat": "test_channel",
        "chat_id": -100123,
        "chat_type": "channel",
        "sender": "sender",
        "mentions": ["MCshinra"],
        "link": "https://t.me/test_channel/5",
        "text": "Розыгрыш! Условия простые.",
        "is_win": False,
        "is_giveaway": True,
        "is_check": False,
    }
    base.update(overrides)
    return base


@unittest.skipIf(database is None, "aiosqlite is not installed")
class PendingBroadcastDbTests(unittest.TestCase):
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

    def test_create_and_get(self):
        async def body():
            expires = _iso(datetime.now() + timedelta(minutes=5))
            pb_id = await database.create_pending_broadcast(None, "giveaway", "msg", link="https://t.me/x/1", expires_at=expires)
            row = await database.get_pending_broadcast(pb_id)
            self.assertEqual(row["status"], "pending")
            self.assertEqual(row["notif_type"], "giveaway")
            self.assertEqual(row["link"], "https://t.me/x/1")
            self.assertEqual(row["expires_at"], expires)
            self.assertIsNone(row["admin_message_id"])
            await database.set_pending_broadcast_admin_message(pb_id, 555)
            row = await database.get_pending_broadcast(pb_id)
            self.assertEqual(row["admin_message_id"], 555)

        self._run(body)

    def test_due_selection_only_expired_pending(self):
        async def body():
            past = _iso(datetime.now() - timedelta(minutes=1))
            future = _iso(datetime.now() + timedelta(minutes=5))
            due_id = await database.create_pending_broadcast(None, "mention", "old", expires_at=past)
            await database.create_pending_broadcast(None, "mention", "fresh", expires_at=future)
            decided_id = await database.create_pending_broadcast(None, "mention", "decided", expires_at=past)
            await database.claim_pending_broadcast(decided_id, "rejected", 1)
            due = await database.get_due_pending_broadcasts()
            self.assertEqual([row["id"] for row in due], [due_id])

        self._run(body)

    def test_claim_is_single_shot(self):
        async def body():
            expires = _iso(datetime.now() + timedelta(minutes=5))
            pb_id = await database.create_pending_broadcast(None, "win", "msg", expires_at=expires)
            first = await database.claim_pending_broadcast(pb_id, "approved", 777)
            self.assertIsNotNone(first)
            self.assertEqual(first["status"], "approved")
            self.assertEqual(first["decided_by"], 777)
            self.assertTrue(first["decided_at"])
            second = await database.claim_pending_broadcast(pb_id, "rejected", 888)
            self.assertIsNone(second, "second claim must lose the race")

        self._run(body)

    def test_prune_keeps_pending_and_recent(self):
        async def body():
            old = _iso(datetime.now() - timedelta(days=10))
            expires = _iso(datetime.now() - timedelta(days=9))
            old_decided = await database.create_pending_broadcast(None, "mention", "old", expires_at=expires)
            await database.claim_pending_broadcast(old_decided, "auto_sent")
            old_pending = await database.create_pending_broadcast(None, "mention", "stuck", expires_at=expires)
            async with database._connect() as db:
                await db.execute("UPDATE pending_broadcasts SET created_at = ? WHERE id IN (?, ?)", (old, old_decided, old_pending))
                await db.commit()
            pruned = await database.prune_pending_broadcasts(days=7)
            self.assertEqual(pruned, 1)
            self.assertIsNone(await database.get_pending_broadcast(old_decided))
            self.assertIsNotNone(await database.get_pending_broadcast(old_pending), "pending rows never pruned")

        self._run(body)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BuildPingCardTests(unittest.TestCase):
    def test_titles_per_type(self):
        msg, _, _ = build_ping_card(_record(is_giveaway=True))
        self.assertIn("Найден розыгрыш", msg)
        msg, _, _ = build_ping_card(_record(is_giveaway=False, is_win=True))
        self.assertIn("победу", msg)
        msg, _, _ = build_ping_card(_record(is_giveaway=False))
        self.assertIn("Новое упоминание", msg)

    def test_link_normalisation(self):
        _, link, _ = build_ping_card(_record(link="нет ссылки"))
        self.assertIsNone(link)
        _, link, _ = build_ping_card(_record())
        self.assertEqual(link, "https://t.me/test_channel/5")

    def test_excerpt_truncated(self):
        msg, _, header = build_ping_card(_record(text="x" * 5000))
        limit = 600 if header else 800
        self.assertLessEqual(len(msg), limit + 400, "card must stay within caption budget")
        self.assertIn("x" * 100, msg)

    def test_candidate_line(self):
        msg, _, _ = build_ping_card(_record(), candidate={"score": 7, "status": "recommended", "blocked_reason": ""})
        self.assertIn("score `7`", msg)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class ModeratedEnqueueTests(unittest.TestCase):
    def _run(self, coro_factory):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                original_client = state.bot_client
                original_admin = bot_notify.ADMIN_ID
                original_broadcast = bot_notify.broadcast_member_notification
                database.DB_PATH = Path(tmp) / "test.db"
                state.notification_seen.clear()
                try:
                    await database.init_db()
                    return await coro_factory()
                finally:
                    database.DB_PATH = original_path
                    state.bot_client = original_client
                    bot_notify.ADMIN_ID = original_admin
                    bot_notify.broadcast_member_notification = original_broadcast

        return asyncio.run(scenario())

    def test_moderated_mode_holds_member_broadcast(self):
        async def body():
            await database.set_setting("notifications", {"moderation_mode": "moderated", "approval_timeout_seconds": 300})
            fake = FakeBotClient()
            state.bot_client = fake
            bot_notify.ADMIN_ID = 777
            broadcast_calls: list[dict] = []

            async def fake_broadcast(message, buttons=None, file=None, notif_type="mention", score=None, premium_only=None):
                broadcast_calls.append({"message": message, "notif_type": notif_type, "premium_only": premium_only})
                return []  # no premium members

            bot_notify.broadcast_member_notification = fake_broadcast

            await bot_notify.send_bot_notification(_record(), ping_id=None)

            self.assertTrue(all(c["premium_only"] is True for c in broadcast_calls),
                            "only the premium fast-path may fire before approval")
            self.assertEqual(len(fake.sent), 1, "admin gets the moderation card")
            self.assertEqual(fake.sent[0]["target"], 777)
            self.assertIn("на модерации", fake.sent[0]["message"])
            flat = [btn for row in (fake.sent[0]["buttons"] or []) for btn in row]
            datas = {getattr(btn, "data", b"") for btn in flat}
            due = await database.get_due_pending_broadcasts(now_iso=_iso(datetime.now() + timedelta(minutes=10)))
            self.assertEqual(len(due), 1)
            pb = due[0]
            self.assertIn(f"bc:ok:{pb['id']}".encode(), datas)
            self.assertIn(f"bc:no:{pb['id']}".encode(), datas)
            self.assertEqual(pb["admin_message_id"], 101)
            self.assertEqual(pb["notif_type"], "giveaway")

        self._run(body)

    def test_auto_mode_broadcasts_immediately(self):
        async def body():
            await database.set_setting("notifications", {"moderation_mode": "auto"})
            fake = FakeBotClient()
            state.bot_client = fake
            bot_notify.ADMIN_ID = 777
            broadcast_calls: list[dict] = []

            async def fake_broadcast(message, buttons=None, file=None, notif_type="mention", score=None, premium_only=None):
                broadcast_calls.append({"notif_type": notif_type, "premium_only": premium_only})
                return [(5, 10)]

            bot_notify.broadcast_member_notification = fake_broadcast

            await bot_notify.send_bot_notification(_record(), ping_id=None)

            self.assertEqual(len(broadcast_calls), 1, "auto mode keeps legacy behavior")
            self.assertEqual(len(fake.sent), 1, "admin copy still sent")
            due = await database.get_due_pending_broadcasts(now_iso=_iso(datetime.now() + timedelta(days=1)))
            self.assertEqual(due, [], "no pending row in auto mode")

        self._run(body)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class ApprovalLoopTests(unittest.TestCase):
    def _run(self, coro_factory):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                original_path = database.DB_PATH
                original_client = state.bot_client
                original_execute = loops.execute_pending_broadcast
                original_edit = loops.edit_pending_admin_card
                database.DB_PATH = Path(tmp) / "test.db"
                try:
                    await database.init_db()
                    return await coro_factory()
                finally:
                    database.DB_PATH = original_path
                    state.bot_client = original_client
                    loops.execute_pending_broadcast = original_execute
                    loops.edit_pending_admin_card = original_edit

        return asyncio.run(scenario())

    def test_due_row_auto_sent_once(self):
        async def body():
            past = _iso(datetime.now() - timedelta(minutes=1))
            pb_id = await database.create_pending_broadcast(None, "giveaway", "msg", expires_at=past)
            state.bot_client = FakeBotClient()
            executed: list[int] = []
            edited: list[str] = []

            async def fake_execute(row):
                executed.append(int(row["id"]))
                return 2, "tok"

            async def fake_edit(row, footer, token=None, delivered_count=0):
                edited.append(footer)

            loops.execute_pending_broadcast = fake_execute
            loops.edit_pending_admin_card = fake_edit

            handled = await loops.process_due_broadcasts()
            self.assertEqual(handled, 1)
            self.assertEqual(executed, [pb_id])
            self.assertIn("автоматически", edited[0])
            row = await database.get_pending_broadcast(pb_id)
            self.assertEqual(row["status"], "auto_sent")

            self.assertEqual(await loops.process_due_broadcasts(), 0, "second pass is a no-op")
            self.assertEqual(executed, [pb_id])

        self._run(body)

    def test_bot_down_leaves_rows_pending(self):
        async def body():
            past = _iso(datetime.now() - timedelta(minutes=1))
            pb_id = await database.create_pending_broadcast(None, "mention", "msg", expires_at=past)
            state.bot_client = None

            async def fail_execute(row):  # must never fire
                raise AssertionError("execute must not run while bot is down")

            loops.execute_pending_broadcast = fail_execute
            self.assertEqual(await loops.process_due_broadcasts(), 0)
            row = await database.get_pending_broadcast(pb_id)
            self.assertEqual(row["status"], "pending")

        self._run(body)


if __name__ == "__main__":
    unittest.main()
