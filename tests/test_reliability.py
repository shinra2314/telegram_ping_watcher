"""Tests for the Sprint 1 reliability improvements.

Covers:
- jobs.runtime_health and start_supervised_task metrics
- database.cleanup_old_data with pings retention
- database.search_pings_fts FTS5 search
"""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import os
TEST_DB_DIR = tempfile.mkdtemp(prefix="pulse_test_")
TEST_DB_PATH = Path(TEST_DB_DIR) / "test_pulse.db"
os.environ["PULSE_DB_PATH"] = str(TEST_DB_PATH)
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token-1234567890")
os.environ.setdefault("VIEWER_TOKEN", "test-viewer-token-1234567890")

# Clear lru_cache for settings since env may have changed
from pulse_desk.config import get_settings  # noqa: E402

get_settings.cache_clear()

import database  # noqa: E402

# Force database module to use the test path
database.DB_PATH = TEST_DB_PATH

from pulse_desk.jobs import runtime_health, start_supervised_task, start_tracked_task  # noqa: E402
from pulse_desk.live_hub import LiveHub  # noqa: E402
from pulse_desk.runtime import AppState  # noqa: E402


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class CleanupOldDataTests(unittest.TestCase):
    def setUp(self):
        asyncio.run(database.init_db())

    def tearDown(self):
        # Each test starts fresh by deleting all rows
        async def _wipe():
            async with database._connect() as db:
                await db.execute("DELETE FROM pings")
                await db.execute("DELETE FROM app_events")
                await db.execute("DELETE FROM market_history")
                await db.commit()
        asyncio.run(_wipe())

    def _insert_old_ping(self, days_ago: int) -> int:
        async def _do():
            detected = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
            async with database._connect() as db:
                cur = await db.execute(
                    "INSERT INTO pings (chat, sender, text, detected_at, status) "
                    "VALUES (?, ?, ?, ?, 'new')",
                    ("test_chat", "test_sender", "some text", detected),
                )
                await db.commit()
                return cur.lastrowid
        return asyncio.run(_do())

    def test_cleanup_deletes_old_pings_with_retention(self):
        old_id = self._insert_old_ping(days_ago=200)
        fresh_id = self._insert_old_ping(days_ago=5)
        stats = asyncio.run(database.cleanup_old_data(days=7, pings_retention_days=90))
        self.assertGreaterEqual(stats["pings"], 1)
        self.assertEqual(stats["vacuumed"], 0)

        async def _check():
            async with database._connect() as db:
                cur = await db.execute("SELECT id FROM pings WHERE id = ?", (old_id,))
                row_old = await cur.fetchone()
                cur = await db.execute("SELECT id FROM pings WHERE id = ?", (fresh_id,))
                row_fresh = await cur.fetchone()
                return row_old, row_fresh

        row_old, row_fresh = asyncio.run(_check())
        self.assertIsNone(row_old)
        self.assertIsNotNone(row_fresh)

    def test_cleanup_skips_pings_when_retention_zero(self):
        self._insert_old_ping(days_ago=400)
        stats = asyncio.run(database.cleanup_old_data(days=7, pings_retention_days=0))
        self.assertEqual(stats["pings"], 0)


class SearchFtsTests(unittest.TestCase):
    def setUp(self):
        asyncio.run(database.init_db())

    def tearDown(self):
        async def _wipe():
            async with database._connect() as db:
                await db.execute("DELETE FROM pings")
                await db.execute("DELETE FROM pings_fts")
                await db.commit()
        asyncio.run(_wipe())

    def _insert(self, text: str, chat: str = "ch") -> int:
        async def _do():
            now = datetime.now().isoformat(timespec="seconds")
            async with database._connect() as db:
                cur = await db.execute(
                    "INSERT INTO pings (chat, sender, text, detected_at, status) VALUES (?, ?, ?, ?, 'new')",
                    (chat, "sender", text, now),
                )
                ping_id = cur.lastrowid
                await db.execute(
                    "INSERT INTO pings_fts(rowid, chat, sender, mentions, text) VALUES (?, ?, ?, ?, ?)",
                    (ping_id, chat, "sender", "", text),
                )
                await db.commit()
                return ping_id
        return asyncio.run(_do())

    def test_search_returns_matches(self):
        self._insert("Розыгрыш призов уже сегодня!")
        self._insert("Просто рандомный текст без матча.")
        rows = asyncio.run(database.search_pings_fts("призов", limit=10))
        self.assertEqual(len(rows), 1)
        self.assertIn("<mark>", rows[0]["snippet"])

    def test_search_empty_query_returns_empty(self):
        self._insert("anything")
        rows = asyncio.run(database.search_pings_fts("   ", limit=10))
        self.assertEqual(rows, [])


class RuntimeHealthTests(unittest.TestCase):
    def test_health_reports_missing_jobs(self):
        state = AppState()
        result = runtime_health(state, accounts_online=0, accounts_configured=2)
        self.assertEqual(result["accounts_online"], 0)
        self.assertEqual(result["accounts_configured"], 2)
        self.assertFalse(result["accounts_ok"])
        self.assertIn("auto-scan", result["missing_background_tasks"])
        self.assertEqual(result["background_tasks"], [])

    def test_supervised_task_restarts_after_crash(self):
        state = AppState()
        logger = logging.getLogger("test")
        attempts = []

        async def flaky():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("boom")
            # Second attempt: stop the loop by signalling shutdown
            state.shutting_down = True

        async def _drive():
            task = start_supervised_task(state, logger, "flaky", flaky, backoff_base=0.1, backoff_max=0.2)
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.CancelledError:
                pass

        asyncio.run(_drive())
        self.assertGreaterEqual(len(attempts), 2)
        self.assertGreaterEqual(state.job_restart_count.get("flaky", 0), 1)
        self.assertIsNotNone(state.job_last_error.get("flaky"))


class LiveHubTests(unittest.TestCase):
    def test_fanout_to_multiple_subscribers(self):
        async def _drive():
            hub = LiveHub()
            sub_a = await hub.subscribe()
            sub_b = await hub.subscribe()
            self.assertEqual(hub.subscriber_count(), 2)
            hub.publish("ping", {"id": 1, "chat": "x"}, event_id=42)
            ev_a = await asyncio.wait_for(sub_a.queue.get(), timeout=1.0)
            ev_b = await asyncio.wait_for(sub_b.queue.get(), timeout=1.0)
            self.assertEqual(ev_a["event_type"], "ping")
            self.assertEqual(ev_a["id"], 42)
            self.assertEqual(ev_a["payload"]["id"], 1)
            self.assertEqual(ev_b["payload"]["chat"], "x")
            await hub.unsubscribe(sub_a)
            await hub.unsubscribe(sub_b)
            self.assertEqual(hub.subscriber_count(), 0)

        asyncio.run(_drive())

    def test_lagged_flag_on_queue_full(self):
        async def _drive():
            hub = LiveHub()
            sub = await hub.subscribe(maxsize=2)
            for i in range(5):
                hub.publish("ping", {"i": i})
            # Should have at most 2 events, lagged flag set
            self.assertEqual(sub.queue.qsize(), 2)
            self.assertTrue(sub._lagged)

        asyncio.run(_drive())


if __name__ == "__main__":
    unittest.main()
