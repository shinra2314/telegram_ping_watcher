"""Reliability hardening, 24.09: lock storms, handlers that raise, a frozen loop.

Every test here pins a failure seen in the live logs — see
docs/superpowers/specs/2026-09-24-reliability.md.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import database
from pulse_desk import scan_engine
from pulse_desk.app_ctx import state
from pulse_desk.resilience import LoopWatch, ThrottledErrors, guard
from pulse_desk.telegram_accounts import deletion_key, on_messages_deleted


def ping(chat_id: int, message_id: int, *, chat_type: str = "channel", win: bool = False, giveaway: bool = False) -> dict:
    return {
        "date": "2026-09-24T10:00:00", "chat": "Chat", "chat_id": chat_id, "sender": "A", "sender_id": 2,
        "message_id": message_id, "mentions": ["@Alpha"], "link": f"https://t.me/c/{chat_id}/{message_id}",
        "text": f"post {message_id} @Alpha", "chat_type": chat_type, "detected_at": "2026-09-24T10:00:01",
        "is_win": win, "is_giveaway": giveaway,
    }


class DbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "pulse_test.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()


class DeletePingsTests(DbCase):
    async def test_one_event_soft_deletes_wins_and_drops_the_rest(self):
        await database.save_ping(ping(1, 3))
        await database.save_ping(ping(1, 4, giveaway=True))
        await database.save_ping(ping(1, 5, win=True))
        await database.save_ping(ping(2, 3))  # same id, another channel: untouched

        touched = await database.delete_pings(1, [3, 4, 5, 6, 7])

        self.assertEqual(touched, 3)
        self.assertIsNone(await database.get_ping_by_message_ref(1, 3))
        self.assertTrue((await database.get_ping_by_message_ref(1, 4))["deleted_at"])
        self.assertTrue((await database.get_ping_by_message_ref(1, 5))["deleted_at"])
        self.assertIsNotNone(await database.get_ping_by_message_ref(2, 3))
        # The FTS row of the dropped ping went with it.
        hits = {(row["chat_id"], row["message_id"]) for row in await database.search_pings_fts("post")}
        self.assertNotIn((1, 3), hits)
        self.assertIn((1, 4), hits)

    async def test_nothing_stored_means_no_write_even_under_someone_elses_lock(self):
        await database.save_ping(ping(1, 3))
        # Another writer holds the lock for the whole call: a delete of messages we
        # never stored must answer from a read, not queue behind it for 10 s.
        holder = sqlite3.connect(database.DB_PATH, timeout=1)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE pings SET note = 'held' WHERE message_id = 3")
        try:
            started = time.perf_counter()
            self.assertEqual(await database.delete_pings(1, list(range(100, 200))), 0)
            self.assertLess(time.perf_counter() - started, 2.0)
        finally:
            holder.rollback()
            holder.close()

    async def test_chatless_event_never_touches_channels(self):
        await database.save_ping(ping(1, 7, win=True))
        await database.save_ping(ping(9, 7, chat_type="private"))
        self.assertEqual(await database.delete_pings(None, [7]), 1)
        self.assertIsNone((await database.get_ping_by_message_ref(1, 7))["deleted_at"])
        self.assertIsNone(await database.get_ping_by_message_ref(9, 7))

    async def test_empty_and_junk_ids(self):
        self.assertEqual(await database.delete_pings(1, []), 0)
        self.assertEqual(await database.delete_pings(1, [None, "x", True]), 0)


class DeletionEventTests(DbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        state.processed_msg_ids.clear()

    async def test_a_channel_deletion_is_handled_once_for_all_accounts(self):
        await database.save_ping(ping(-100, 11))
        with mock.patch("database.delete_pings", wraps=database.delete_pings) as spy:
            results = [await on_messages_deleted(session, -100, [11, 12]) for session in ("a", "b", "c")]
        self.assertEqual(results, [1, 0, 0])
        self.assertEqual(spy.await_count, 1)

    async def test_chatless_deletions_are_per_account(self):
        # Private chats and basic groups number messages per account: id 7 of one
        # account is not id 7 of another, so neither may swallow the other's event.
        self.assertNotEqual(deletion_key(None, [7], "a"), deletion_key(None, [7], "b"))
        self.assertEqual(deletion_key(-100, [2, 1], "a"), deletion_key(-100, [1, 2], "b"))
        with mock.patch("database.delete_pings", mock.AsyncMock(return_value=0)) as spy:
            await on_messages_deleted("a", None, [7])
            await on_messages_deleted("b", None, [7])
        self.assertEqual(spy.await_count, 2)


class RetryLockedTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_a_locked_write_and_gives_up_on_anything_else(self):
        calls = []

        @database.retry_locked(attempts=3, base_delay=0.001)
        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        self.assertEqual(await flaky(), "ok")
        self.assertEqual(len(calls), 3)

        @database.retry_locked(attempts=3, base_delay=0.001)
        async def broken():
            calls.append(2)
            raise sqlite3.OperationalError("no such table: nope")

        calls.clear()
        with self.assertRaises(sqlite3.OperationalError):
            await broken()
        self.assertEqual(len(calls), 1)

    async def test_last_locked_error_still_raises(self):
        @database.retry_locked(attempts=2, base_delay=0.001)
        async def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            await always_locked()


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_raising_handler_is_logged_once_a_minute_and_never_raises(self):
        now = [1000.0]
        logger = logging.getLogger("test.guard")
        errors = ThrottledErrors(logger, every=60.0, clock=lambda: now[0])

        @guard("deleted-messages", errors, "acc")
        async def handler(event):
            raise sqlite3.OperationalError("database is locked")

        with self.assertLogs(logger, "ERROR") as logs:
            for _ in range(5):
                await handler(object())
            now[0] += 61
            await handler(object())
        self.assertEqual(len(logs.records), 2)
        self.assertIn("and 4 more", logs.records[1].getMessage())
        self.assertEqual(errors.total(), 6)

    async def test_cancellation_still_propagates(self):
        errors = ThrottledErrors(logging.getLogger("test.guard"))

        @guard("new-message", errors)
        async def handler(event):
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await handler(object())


class LoopWatchTests(unittest.IsolatedAsyncioTestCase):
    def test_stall_bookkeeping(self):
        watch = LoopWatch(logging.getLogger("test.loop"), tick=0.5, stall=3.0, clock=lambda: 0.0)
        watch.last_tick = 100.0
        self.assertIsNone(watch.check(102.0))
        self.assertAlmostEqual(watch.check(104.0), 4.0)
        self.assertIsNone(watch.check(106.0))  # the same stall is reported once
        with self.assertLogs("test.loop", "WARNING") as logs:
            watch.note_tick(107.0, 6.5)
        self.assertIn("7.0s stall", logs.output[0])
        stats = watch.stats()
        self.assertEqual(stats["loop_stalls"], 1)
        self.assertEqual(stats["loop_longest_stall_ms"], 7000)
        self.assertEqual(stats["loop_max_lag_ms"], 6500)
        self.assertFalse(stats["loop_stalled_now"])

    async def test_a_blocking_call_is_caught_with_its_stack(self):
        watch = LoopWatch(logging.getLogger("test.loop"), tick=0.02, stall=0.15)
        with self.assertLogs("test.loop", "WARNING") as logs:
            watch.start()
            try:
                await asyncio.sleep(0.05)
                _block_the_loop(0.4)
                await asyncio.sleep(0.1)
            finally:
                watch.stop()
        text = "\n".join(logs.output)
        self.assertIn("Event loop blocked", text)
        self.assertIn("_block_the_loop", text)
        self.assertIn("is back after", text)
        self.assertEqual(watch.stats()["loop_stalls"], 1)


def _block_the_loop(seconds: float) -> None:
    time.sleep(seconds)


class ScanProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scan_engine._progress_mark.update(run=None, at=0.0)

    async def test_progress_is_throttled_and_never_raises(self):
        update = mock.AsyncMock(side_effect=[None, sqlite3.OperationalError("database is locked"), None])
        with mock.patch("database.update_scan_run", update):
            self.assertTrue(await scan_engine.save_scan_progress(7, found=1))
            self.assertFalse(await scan_engine.save_scan_progress(7, found=2))  # within 5 s
            self.assertFalse(await scan_engine.save_scan_progress(7, force=True, found=3))  # locked: swallowed
            self.assertTrue(await scan_engine.save_scan_progress(7, force=True, found=4))
        self.assertEqual(update.await_count, 3)


class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_busy_database_does_not_hang_the_health_answer(self):
        from routers import health

        async def stuck():
            await asyncio.sleep(10)

        with mock.patch.object(health, "DB_FIELD_TIMEOUT_SECONDS", 0.05), \
                mock.patch("database.pending_sends_backlog", stuck), \
                mock.patch("database.count_owed_ping_notifications", stuck):
            started = time.perf_counter()
            info = await health.health()
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(info["db_slow"])
        self.assertIsNone(info["owed_ping_cards"])
        for key in ("loop_lag_ms", "loop_stalls", "account_handler_errors"):
            self.assertIn(key, info)


if __name__ == "__main__":
    unittest.main()


class DedupeSpeedTests(unittest.TestCase):
    """group_duplicates ran on the event loop at every start: 2.9 s frozen (24.09)."""

    @staticmethod
    def reference(rows):
        from pulse_desk.dedupe import _ts, same_win, source_rank

        groups = []
        for row in sorted(rows, key=_ts):
            for group in groups:
                primary = min(group, key=source_rank)
                if row.get("chat_id") != primary.get("chat_id") and any(same_win(row, m) for m in group):
                    group.append(row)
                    break
            else:
                groups.append([row])
        out = []
        for group in groups:
            if len(group) > 1:
                ordered = sorted(group, key=source_rank)
                out.append((int(ordered[0]["id"]), [int(r["id"]) for r in ordered[1:]]))
        return out

    @staticmethod
    def rows(count):
        import random

        rng = random.Random(7)
        templates = [f"Итоги розыгрыша номер {n}: победители @alpha и @beta, пишите админу за призом" for n in range(40)]
        rows = []
        for i in range(1, count + 1):
            rows.append({
                "id": i, "chat_id": rng.choice([1, 2, 3, 4, 5, 6]),
                "chat_type": rng.choice(["channel", "group", "private"]),
                "text": rng.choice(templates) if rng.random() < 0.6 else f"уникальный текст поста {i} " * 3,
                "mentions": ["@alpha"] if rng.random() < 0.8 else ["@gamma"],
                "detected_at": f"2026-09-{rng.randint(1, 20):02d}T{rng.randint(0, 23):02d}:00:00",
            })
        return rows

    def test_same_groups_as_before(self):
        from pulse_desk.dedupe import group_duplicates

        for count in (0, 1, 50, 400):
            rows = self.rows(count)
            with self.subTest(count=count):
                self.assertEqual(group_duplicates(rows), self.reference(rows))

    def test_two_thousand_wins_group_fast(self):
        from pulse_desk.dedupe import group_duplicates

        rows = self.rows(2000)
        started = time.perf_counter()
        group_duplicates(rows)
        self.assertLess(time.perf_counter() - started, 1.0)


class PanelServerTests(unittest.IsolatedAsyncioTestCase):
    """The panel's port must never take the process down, nor answer a bare 500."""

    def client(self):
        from fastapi.testclient import TestClient

        from pulse_desk.miniapp_server import build_miniapp

        app = build_miniapp()

        async def boom():
            raise RuntimeError("kaputt")

        async def busy():
            raise sqlite3.OperationalError("database is locked")

        app.add_api_route("/api/app/test-boom", boom)
        app.add_api_route("/api/app/test-busy", busy)
        return TestClient(app, raise_server_exceptions=False)

    async def test_an_endpoint_that_raises_answers_json_with_the_usual_headers(self):
        from pulse_desk import miniapp_server

        with self.assertLogs("pulse_desk", "ERROR"), \
                mock.patch.object(miniapp_server, "record_app_event", mock.AsyncMock()) as event:
            response = self.client().get("/api/app/test-boom")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], miniapp_server.INTERNAL)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        event.assert_awaited_once()

    async def test_a_busy_database_is_503_with_retry_after(self):
        from pulse_desk import miniapp_server

        response = self.client().get("/api/app/test-busy")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["Retry-After"], "2")
        self.assertEqual(response.json()["detail"], miniapp_server.BUSY)

    async def test_a_taken_port_is_an_error_not_a_process_exit(self):
        from pulse_desk import miniapp_server

        async def exits(self, sockets=None):
            raise SystemExit(1)

        with mock.patch("uvicorn.Server.serve", exits):
            with self.assertRaises(RuntimeError):
                await miniapp_server.serve_miniapp()

    async def test_the_supervisor_survives_a_job_that_calls_exit(self):
        from pulse_desk.jobs import start_supervised_task
        from pulse_desk.runtime import AppState

        runs = []

        async def job():
            runs.append(1)
            if len(runs) == 1:
                raise SystemExit(1)
            await asyncio.sleep(3600)

        app_state = AppState()
        with self.assertLogs("test.jobs", "ERROR"):
            task = start_supervised_task(app_state, logging.getLogger("test.jobs"), "panel", job,
                                         backoff_base=0.01, backoff_max=0.01)
            for _ in range(100):
                if len(runs) >= 2:
                    break
                await asyncio.sleep(0.02)
        self.assertEqual(len(runs), 2)
        self.assertEqual(app_state.job_restart_count["panel"], 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


class RateLimiterTests(unittest.TestCase):
    def test_idle_people_are_forgotten(self):
        from routers.miniapp.common import RateLimiter

        now = [0.0]
        limiter = RateLimiter(window=60, clock=lambda: now[0])
        for person in range(50):
            self.assertTrue(limiter.allow(person, 5))
        now[0] = 61
        self.assertTrue(limiter.allow("fresh", 5))
        self.assertEqual(list(limiter.hits), ["fresh"])
