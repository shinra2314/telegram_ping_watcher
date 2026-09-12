"""Outbox failure handling: what a delayed copy costs when a send goes wrong.

The queue used to treat every non-delivery the same way — three strikes and the
row was cancelled for good. That meant a minute of bot downtime silently threw
away every due notification, and one throttled recipient stalled all the others.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from _fakes import FakeBot  # noqa: E402

from pulse_desk.bot_permissions import dump_permissions, full_permissions  # noqa: E402

try:
    import database
except ModuleNotFoundError as exc:
    if exc.name != "aiosqlite":
        raise
    database = None

if database is not None:
    from pulse_desk import bot_notify
    from pulse_desk.app_ctx import state


def _past(minutes: int = 5) -> str:
    return (datetime.now() - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


@unittest.skipIf(database is None, "aiosqlite is not installed")
class DeliverPendingSendTests(unittest.IsolatedAsyncioTestCase):
    """`deliver_pending_send` must say *why* it could not deliver."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        self.old_client = state.bot_client
        database.DB_PATH = Path(self.tmp.name) / "outbox.db"
        await database.init_db()
        self.bot = FakeBot()
        state.bot_client = self.bot
        bot_notify.reset_unreachable_peers()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        state.bot_client = self.old_client
        bot_notify.reset_unreachable_peers()
        self.tmp.cleanup()

    async def test_delivers_and_returns_the_message_id(self):
        row = {"id": 1, "tg_id": 111, "message": "готово", "link": "", "file_path": ""}
        self.assertIsNotNone(await bot_notify.deliver_pending_send(row))
        self.assertEqual(len(self.bot.sent), 1)

    async def test_offline_bot_raises_BotOffline(self):
        self.bot.set_connected(False)
        state.bot_client = None  # ensure_bot_connected() is False
        row = {"id": 1, "tg_id": 111, "message": "готово", "link": "", "file_path": ""}
        with self.assertRaises(bot_notify.BotOffline):
            await bot_notify.deliver_pending_send(row)

    async def test_blocked_recipient_raises_RecipientUnreachable(self):
        self.bot.send_error = ValueError("USER_IS_BLOCKED: the user blocked the bot")
        row = {"id": 1, "tg_id": 111, "message": "готово", "link": "", "file_path": ""}
        with self.assertRaises(bot_notify.RecipientUnreachable):
            await bot_notify.deliver_pending_send(row)
        # ...and the peer is muted, so the next row for them costs no round trip.
        self.assertTrue(bot_notify.peer_is_unreachable(111))

    async def test_muted_peer_is_skipped_without_a_send(self):
        bot_notify.mark_peer_unreachable(111)
        row = {"id": 1, "tg_id": 111, "message": "готово", "link": "", "file_path": ""}
        with self.assertRaises(bot_notify.RecipientUnreachable):
            await bot_notify.deliver_pending_send(row)
        self.assertEqual(self.bot.sent, [])

    async def test_other_errors_still_propagate(self):
        self.bot.send_error = RuntimeError("server exploded")
        row = {"id": 1, "tg_id": 111, "message": "готово", "link": "", "file_path": ""}
        with self.assertRaises(RuntimeError):
            await bot_notify.deliver_pending_send(row)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class PendingSendLoopTests(unittest.IsolatedAsyncioTestCase):
    """One pass of the drain loop, driven through the real queue."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        self.old_client = state.bot_client
        database.DB_PATH = Path(self.tmp.name) / "loop.db"
        await database.init_db()
        self.bot = FakeBot()
        state.bot_client = self.bot
        bot_notify.reset_unreachable_peers()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        state.bot_client = self.old_client
        bot_notify.reset_unreachable_peers()
        self.tmp.cleanup()

    async def _drain_once(self) -> float:
        """One real batch — the same code path the `pending-sends` job runs."""
        from pulse_desk.loops import drain_pending_sends

        return await drain_pending_sends()

    async def _attempts(self, row_id: int):
        rows = await database.get_due_pending_sends(limit=25)
        for row in rows:
            if int(row["id"]) == row_id:
                return int(row.get("attempts") or 0)
        return None  # no longer due: delivered or cancelled

    async def test_bot_offline_does_not_burn_attempts(self):
        # The regression this whole change exists for: ~60 s of downtime used to
        # spend all three attempts and drop the notification for good.
        row_id = await database.queue_pending_send(111, _past(), "приз")
        state.bot_client = None
        for _ in range(5):
            await self._drain_once()
        state.bot_client = self.bot
        self.assertEqual(await self._attempts(row_id), 0)

        # Once the bot is back, the same row delivers.
        await self._drain_once()
        self.assertIsNone(await self._attempts(row_id))
        self.assertEqual(len(self.bot.sent), 1)

    async def test_blocked_recipient_is_dropped_immediately(self):
        row_id = await database.queue_pending_send(111, _past(), "приз")
        self.bot.send_error = ValueError("USER_IS_BLOCKED: the user blocked the bot")
        await self._drain_once()
        self.assertIsNone(await self._attempts(row_id))  # cancelled, not retried

    async def test_generic_failure_still_retries_then_gives_up(self):
        row_id = await database.queue_pending_send(111, _past(), "приз")
        for _ in range(3):
            self.bot.send_error = RuntimeError("flaky")
            await self._drain_once()
        self.assertEqual(await self._attempts(row_id), 3)
        await self._drain_once()  # attempts exhausted -> cancelled
        self.assertIsNone(await self._attempts(row_id))

    async def test_flood_wait_costs_no_attempt_and_idles_the_loop(self):
        from telethon.errors import FloodWaitError

        row_id = await database.queue_pending_send(111, _past(), "приз")
        exc = FloodWaitError(request=None)
        exc.seconds = 30
        self.bot.send_error = exc
        idle_until = await self._drain_once()
        # The row keeps its budget, and the loop is told to back off rather than
        # sleeping inside the batch and stalling everyone else's copies.
        self.assertEqual(await self._attempts(row_id), 0)
        self.assertGreater(idle_until, 0.0)

    async def test_offline_stops_the_batch_without_touching_later_rows(self):
        a = await database.queue_pending_send(111, _past(), "a")
        b = await database.queue_pending_send(222, _past(), "b")
        state.bot_client = None
        await self._drain_once()
        state.bot_client = self.bot
        self.assertEqual(await self._attempts(a), 0)
        self.assertEqual(await self._attempts(b), 0)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BroadcastInterruptionTests(unittest.IsolatedAsyncioTestCase):
    """A fanout cut short must not silently drop the members it never reached."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        self.old_client = state.bot_client
        self.old_admin = bot_notify.ADMIN_ID
        database.DB_PATH = Path(self.tmp.name) / "fanout.db"
        await database.init_db()
        self.bot = FakeBot()
        state.bot_client = self.bot
        bot_notify.ADMIN_ID = 999
        bot_notify.reset_unreachable_peers()
        key = await database.create_bot_key("friends", "secret", role="viewer")
        perms = dump_permissions(full_permissions())
        for tg_id in (111, 222, 333):
            await database.upsert_bot_member(tg_id, f"u{tg_id}", f"User {tg_id}", key["id"], "viewer", perms)

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        state.bot_client = self.old_client
        bot_notify.ADMIN_ID = self.old_admin
        bot_notify.reset_unreachable_peers()
        self.tmp.cleanup()

    async def test_all_members_receive_a_healthy_broadcast(self):
        delivered = await bot_notify.broadcast_member_notification("приз", token="tok")
        self.assertEqual(len(delivered), 3)
        self.assertEqual(await database.pending_sends_backlog(), 0)

    async def test_offline_midway_queues_the_rest_instead_of_dropping_them(self):
        # Returning early here used to lose every member after the failure point,
        # with nothing recorded anywhere.
        original = bot_notify.ensure_bot_connected
        calls = {"n": 0}

        async def flaky_connected() -> bool:
            calls["n"] += 1
            return calls["n"] <= 1  # first member sends, then the bot goes down

        bot_notify.ensure_bot_connected = flaky_connected
        try:
            delivered = await bot_notify.broadcast_member_notification("приз", token="tok")
        finally:
            bot_notify.ensure_bot_connected = original

        self.assertEqual(len(delivered), 1)
        # The two we never reached are now the outbox's problem, not lost.
        self.assertEqual(await database.pending_sends_backlog(), 2)


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BroadcastClaimTests(unittest.IsolatedAsyncioTestCase):
    """A claim consumed before a failed send has to be returnable."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "claims.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_release_makes_a_claimed_row_claimable_again(self):
        pb_id = await database.create_pending_broadcast(None, "mention", "приз")
        self.assertIsNotNone(await database.claim_pending_broadcast(pb_id, "approved"))
        # Second claim fails: this is what made a mid-send error unrecoverable.
        self.assertIsNone(await database.claim_pending_broadcast(pb_id, "approved"))

        self.assertTrue(await database.release_pending_broadcast(pb_id))
        self.assertIsNotNone(await database.claim_pending_broadcast(pb_id, "approved"))

    async def test_released_row_is_pending_again(self):
        pb_id = await database.create_pending_broadcast(None, "mention", "приз")
        await database.claim_pending_broadcast(pb_id, "auto_sent", decided_by=42)
        await database.release_pending_broadcast(pb_id)
        row = await database.get_pending_broadcast(pb_id)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["decided_by"])
