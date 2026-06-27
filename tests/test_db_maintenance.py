"""Tests for DB growth control: size cap + archive + unbounded-table retention.

Covers database.cleanup_unbounded_tables and database.enforce_db_size_cap.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

TEST_DB_DIR = tempfile.mkdtemp(prefix="pulse_maint_")
TEST_DB_PATH = Path(TEST_DB_DIR) / "test_pulse.db"
os.environ["PULSE_DB_PATH"] = str(TEST_DB_PATH)
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token-1234567890")
os.environ.setdefault("VIEWER_TOKEN", "test-viewer-token-1234567890")

from pulse_desk.config import get_settings  # noqa: E402

get_settings.cache_clear()

import database  # noqa: E402


def _iso(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


class UnboundedTablesTests(unittest.TestCase):
    def setUp(self):
        database.DB_PATH = TEST_DB_PATH
        asyncio.run(database.init_db())
        asyncio.run(self._wipe())

    def tearDown(self):
        asyncio.run(self._wipe())

    async def _wipe(self):
        async with database._connect() as db:
            for table in ("scan_runs", "settings_history", "access_audit", "giveaway_actions"):
                await db.execute(f"DELETE FROM {table}")
            await db.commit()

    def test_cleanup_unbounded_tables(self):
        async def _seed():
            async with database._connect() as db:
                # One 'running' row first (low id) + ten finished rows.
                await db.execute(
                    "INSERT INTO scan_runs (status, started_at) VALUES ('running', ?)", (_iso(0),)
                )
                for _ in range(10):
                    await db.execute(
                        "INSERT INTO scan_runs (status, started_at, finished_at) VALUES ('ok', ?, ?)",
                        (_iso(1), _iso(1)),
                    )
                # Audit tables: one stale (>90d) + one fresh each.
                await db.execute(
                    "INSERT INTO settings_history (key, new_value, changed_at) VALUES ('k', 'v', ?)", (_iso(120),)
                )
                await db.execute(
                    "INSERT INTO settings_history (key, new_value, changed_at) VALUES ('k', 'v', ?)", (_iso(1),)
                )
                await db.execute(
                    "INSERT INTO access_audit (tg_id, action, actor, created_at) VALUES (1, 'grant', 'sys', ?)",
                    (_iso(120),),
                )
                await db.execute(
                    "INSERT INTO access_audit (tg_id, action, actor, created_at) VALUES (1, 'grant', 'sys', ?)",
                    (_iso(1),),
                )
                await db.execute(
                    "INSERT INTO giveaway_actions (ping_id, action, status, created_at) VALUES (NULL, 'join', 'ok', ?)",
                    (_iso(120),),
                )
                await db.execute(
                    "INSERT INTO giveaway_actions (ping_id, action, status, created_at) VALUES (NULL, 'join', 'ok', ?)",
                    (_iso(1),),
                )
                await db.commit()

        asyncio.run(_seed())
        stats = asyncio.run(database.cleanup_unbounded_tables(scan_runs_keep=5, audit_days=90))

        self.assertEqual(stats["scan_runs"], 5)  # 11 rows, keep newest 5 → 5 finished deleted
        self.assertEqual(stats["settings_history"], 1)
        self.assertEqual(stats["access_audit"], 1)
        self.assertEqual(stats["giveaway_actions"], 1)

        async def _verify():
            async with database._connect() as db:
                running = await (await db.execute(
                    "SELECT COUNT(*) FROM scan_runs WHERE status = 'running'"
                )).fetchone()
                total = await (await db.execute("SELECT COUNT(*) FROM scan_runs")).fetchone()
                sh = await (await db.execute("SELECT COUNT(*) FROM settings_history")).fetchone()
                return running[0], total[0], sh[0]

        running, total, sh = asyncio.run(_verify())
        self.assertEqual(running, 1)  # running row always spared
        self.assertEqual(total, 6)    # 5 newest finished + the running one
        self.assertEqual(sh, 1)       # only the fresh row remains


class SizeCapTests(unittest.TestCase):
    def setUp(self):
        database.DB_PATH = TEST_DB_PATH
        asyncio.run(database.init_db())
        asyncio.run(self._wipe())
        self._archive = database.archive_db_path()
        self._archive.unlink(missing_ok=True)

    def tearDown(self):
        asyncio.run(self._wipe())
        self._archive.unlink(missing_ok=True)

    async def _wipe(self):
        async with database._connect() as db:
            await db.execute("DELETE FROM pings")
            await db.execute("DELETE FROM pings_fts")
            await db.commit()

    def _seed_pings(self, count: int) -> None:
        async def _do():
            blob = "x" * 600  # fatten each row so the file clearly grows
            async with database._connect() as db:
                for i in range(count):
                    favorite = 1 if i % 50 == 0 else 0
                    win = 1 if i % 50 == 1 else 0
                    cur = await db.execute(
                        "INSERT INTO pings (chat, sender, text, detected_at, status, is_favorite, is_win) "
                        "VALUES (?, ?, ?, ?, 'new', ?, ?)",
                        ("chat", "sender", blob, _iso(count - i), favorite, win),
                    )
                    pid = cur.lastrowid
                    await db.execute(
                        "INSERT INTO pings_fts(rowid, chat, sender, mentions, text) VALUES (?, 'chat', 'sender', '', ?)",
                        (pid, blob),
                    )
                await db.commit()
        asyncio.run(_do())

    def test_size_cap_archives_and_trims(self):
        self._seed_pings(3000)
        before = database.db_size_bytes()
        favorites_before, wins_before = asyncio.run(self._fav_win_counts())

        stats = asyncio.run(database.enforce_db_size_cap(1, archive=True))  # 1 MB cap

        self.assertGreater(stats["pings_deleted"], 0)
        self.assertEqual(stats["pings_archived"], stats["pings_deleted"])
        self.assertLess(stats["size_after"], before)
        self.assertEqual(stats["vacuumed"], 1)

        favorites_after, wins_after = asyncio.run(self._fav_win_counts())
        self.assertEqual(favorites_after, favorites_before)  # favorites preserved
        self.assertEqual(wins_after, wins_before)            # wins preserved

        pings_count, fts_count, arch_count = asyncio.run(self._post_counts())
        self.assertEqual(pings_count, fts_count)             # FTS stays consistent
        self.assertTrue(self._archive.exists())
        self.assertEqual(arch_count, stats["pings_archived"])

    def test_size_cap_disabled(self):
        self._seed_pings(50)
        before = asyncio.run(self._ping_count())
        stats = asyncio.run(database.enforce_db_size_cap(0, archive=True))
        self.assertEqual(stats["pings_deleted"], 0)
        self.assertEqual(asyncio.run(self._ping_count()), before)
        self.assertFalse(self._archive.exists())

    async def _ping_count(self) -> int:
        async with database._connect() as db:
            return (await (await db.execute("SELECT COUNT(*) FROM pings")).fetchone())[0]

    async def _fav_win_counts(self):
        async with database._connect() as db:
            fav = (await (await db.execute("SELECT COUNT(*) FROM pings WHERE is_favorite = 1")).fetchone())[0]
            win = (await (await db.execute("SELECT COUNT(*) FROM pings WHERE is_win = 1")).fetchone())[0]
            return fav, win

    async def _post_counts(self):
        async with database._connect() as db:
            pings = (await (await db.execute("SELECT COUNT(*) FROM pings")).fetchone())[0]
            fts = (await (await db.execute("SELECT COUNT(*) FROM pings_fts")).fetchone())[0]
        async with database._connect() as db2:
            await db2.execute("ATTACH DATABASE ? AS arch", (str(self._archive),))
            arch = (await (await db2.execute("SELECT COUNT(*) FROM arch.pings")).fetchone())[0]
            await db2.execute("DETACH DATABASE arch")
        return pings, fts, arch


class ArchiveRetentionTests(unittest.TestCase):
    def setUp(self):
        database.DB_PATH = TEST_DB_PATH
        asyncio.run(database.init_db())
        self._archive = database.archive_db_path()
        self._archive.unlink(missing_ok=True)

    def tearDown(self):
        self._archive.unlink(missing_ok=True)

    def _seed_archive(self) -> None:
        async def _do():
            import aiosqlite
            async with aiosqlite.connect(str(self._archive)) as db:
                await db.execute(
                    "CREATE TABLE pings (id INTEGER PRIMARY KEY, chat TEXT, detected_at TEXT)"
                )
                # Three stale (>30d) + two fresh.
                for days in (120, 60, 31, 5, 0):
                    await db.execute(
                        "INSERT INTO pings (chat, detected_at) VALUES ('chat', ?)", (_iso(days),)
                    )
                await db.commit()
        asyncio.run(_do())

    async def _archive_count(self) -> int:
        import aiosqlite
        async with aiosqlite.connect(str(self._archive)) as db:
            return (await (await db.execute("SELECT COUNT(*) FROM pings")).fetchone())[0]

    def test_prunes_old_archive_records(self):
        self._seed_archive()
        stats = asyncio.run(database.cleanup_archive_db(30, vacuum=True))
        self.assertEqual(stats["archive_pings"], 3)   # 120/60/31 days old removed
        self.assertEqual(stats["archive_vacuumed"], 1)
        self.assertEqual(asyncio.run(self._archive_count()), 2)  # 5d/0d kept

    def test_disabled_keeps_everything(self):
        self._seed_archive()
        stats = asyncio.run(database.cleanup_archive_db(0))
        self.assertEqual(stats["archive_pings"], 0)
        self.assertEqual(asyncio.run(self._archive_count()), 5)

    def test_missing_archive_is_noop(self):
        # No file on disk → zeros, no crash, no file created.
        stats = asyncio.run(database.cleanup_archive_db(30))
        self.assertEqual(stats["archive_pings"], 0)
        self.assertFalse(self._archive.exists())


if __name__ == "__main__":
    unittest.main()
