"""The bot's hot queries: same answers as before, and actually indexed.

`giveaway_account_counts` and the scoped feed filter moved from Python loops
over an unbounded result set into SQL, and the giveaway board picked up a
composite index. Both changes are only worth having if the results are
identical and the planner really uses the index — hence EXPLAIN QUERY PLAN.
"""
from __future__ import annotations

import json
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

try:
    import aiosqlite
    import database
except ModuleNotFoundError as exc:
    if exc.name not in ("aiosqlite",):
        raise
    aiosqlite = None
    database = None


def _reference_account_counts(rows):
    """The pre-SQL tally: parse the JSON `mentions` column row by row."""
    counts: dict[str, dict[str, int]] = {}
    for mentions, is_win in rows:
        for name in mentions:
            key = str(name).strip().lstrip("@").lower()
            if not key:
                continue
            slot = counts.setdefault(key, {"wins": 0, "giveaways": 0, "total": 0})
            slot["total"] += 1
            slot["wins" if is_win else "giveaways"] += 1
    return counts


@unittest.skipIf(database is None, "aiosqlite is not installed")
class BoardQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "queries.db"
        await database.init_db()
        self.raw: list[tuple[str, int]] = []
        now = datetime.now()
        for i in range(30):
            mentions = ["MuverGT"] if i % 2 else ["w3v8f0rm", "MuverGT"]
            is_win = 1 if i % 3 == 0 else 0
            await database.save_ping({
                "date": (now - timedelta(hours=i)).isoformat(),
                "chat": f"chat {i % 4}",
                "chat_id": 1000 + (i % 4),
                "sender": "someone",
                "message_id": i,
                "mentions": mentions,
                "link": f"https://t.me/c/{i}",
                "text": f"розыгрыш номер {i}",
                "chat_type": "channel",
                "detected_at": (now - timedelta(hours=i)).isoformat(),
                "is_giveaway": 1,
                "is_win": is_win,
            })
            self.raw.append((mentions, is_win))

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def test_account_counts_match_the_python_tally(self):
        got = await database.giveaway_account_counts(open_only=False)
        self.assertEqual(got, _reference_account_counts(self.raw))

    async def test_account_counts_are_not_empty(self):
        got = await database.giveaway_account_counts(open_only=False)
        self.assertEqual(set(got), {"muvergt", "w3v8f0rm"})
        self.assertEqual(got["muvergt"]["total"], 30)

    async def test_bucket_total_matches_the_full_board(self):
        # The home screen reads the cheap counter; it must agree with the board
        # it replaced, or "к действию" silently lies.
        board = await database.get_giveaway_board(limit=10, include_outbox=False)
        cheap = await database.giveaway_bucket_total("need_action")
        self.assertEqual(cheap, board["bucket_totals"]["need_action"])

    async def test_board_can_skip_the_outbox_block(self):
        with_outbox = await database.get_giveaway_board(limit=5)
        without = await database.get_giveaway_board(limit=5, include_outbox=False)
        self.assertEqual(without["outbox"], {})
        self.assertNotEqual(with_outbox["outbox"], {})
        self.assertEqual(without["bucket_totals"], with_outbox["bucket_totals"])

    async def test_scoped_feed_filter_matches_python_filtering(self):
        scoped = await database.get_pings(limit=100, mention_any=["w3v8f0rm"])
        everything = await database.get_pings(limit=100)
        expected = [
            row["id"] for row in everything
            if any(str(m).lower() == "w3v8f0rm" for m in json.loads(row["mentions"]))
        ]
        self.assertEqual([row["id"] for row in scoped], expected)
        self.assertTrue(expected, "fixture should produce some scoped rows")

    async def test_scoped_feed_filter_pages_correctly(self):
        page1 = await database.get_pings(limit=5, offset=0, mention_any=["w3v8f0rm"])
        page2 = await database.get_pings(limit=5, offset=5, mention_any=["w3v8f0rm"])
        self.assertEqual(len(page1), 5)
        self.assertFalse({r["id"] for r in page1} & {r["id"] for r in page2})

    async def test_empty_scope_means_no_filter(self):
        self.assertEqual(
            len(await database.get_pings(limit=100, mention_any=[])),
            len(await database.get_pings(limit=100)),
        )


@unittest.skipIf(database is None, "aiosqlite is not installed")
class QueryPlanTests(unittest.IsolatedAsyncioTestCase):
    """Guard the schema-22 indexes: these queries must not full-scan `pings`."""

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "plans.db"
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def _plan(self, sql: str) -> str:
        async with aiosqlite.connect(database.DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("EXPLAIN QUERY PLAN " + sql)).fetchall()
        return "\n".join(row["detail"] for row in rows)

    async def test_schema_version_is_current(self):
        from database._core import SCHEMA_VERSION

        self.assertEqual(await database.get_schema_version(), SCHEMA_VERSION)

    async def test_new_indexes_exist(self):
        async with aiosqlite.connect(database.DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )).fetchall()
        names = {row[0] for row in rows}
        for expected in (
            "idx_pings_board", "idx_pings_date", "idx_pings_chat",
            "idx_bot_members_key", "idx_source_scores_score",
        ):
            self.assertIn(expected, names)

    async def test_giveaway_bucket_count_is_indexed(self):
        from database.boards import BUCKET_WHERE

        plan = await self._plan(
            "SELECT COUNT(*) FROM pings p LEFT JOIN giveaway_candidates c "
            f"ON c.ping_id = p.id WHERE {BUCKET_WHERE['need_action']}"
        )
        self.assertNotIn("SCAN p\n", plan + "\n")
        self.assertIn("INDEX", plan)

    async def test_account_counts_use_the_mentions_index(self):
        plan = await self._plan(
            "SELECT LOWER(m.username), COUNT(*) FROM ping_mentions m "
            "JOIN pings p ON p.id = m.ping_id "
            "WHERE (p.is_giveaway = 1 OR p.is_win = 1) GROUP BY LOWER(m.username)"
        )
        self.assertIn("INDEX", plan)

    async def test_key_member_count_does_not_scan_bot_members(self):
        plan = await self._plan(
            "SELECT k.id, (SELECT COUNT(*) FROM bot_members m WHERE m.key_id = k.id) "
            "FROM bot_access_keys k"
        )
        self.assertNotIn("SCAN m", plan)


if __name__ == "__main__":
    unittest.main()
