"""Paging from the last row on screen: no repeats, no gaps, in every order.

The panel's «Показать ещё» used to re-request pages 1..N by offset. A row
arriving at the top between two taps then showed up twice, and one leaving the
filter hid the next. ``get_pings(after=…)`` continues from the ``(value, id)``
of the last row instead; ties and NULL sort values are where keyset paging
usually goes wrong, so the fixture is made of them.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

import database  # noqa: E402
from pulse_desk.bot.sections.feed import fetch  # noqa: E402
from pulse_desk.bot.views import FeedFilter  # noqa: E402

PAGE = 4


class KeysetPagingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tmp.name) / "paging.db"
        await database.init_db()
        for i in range(19):
            await self.add(i)

    async def asyncTearDown(self):
        database.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    async def add(self, i: int) -> int:
        # Three rows per detected_at second (ties), every fourth post undated.
        return await database.save_ping({
            "date": None if i % 4 == 0 else f"2026-09-{10 + i % 5:02d}T12:00:00",
            "chat": f"chat{i % 3}",
            "chat_id": 1000 + i,
            "message_id": i + 1,
            "sender": "s",
            "text": f"post {i}",
            "mentions": ["muver"],
            "chat_type": "channel",
            "detected_at": f"2026-09-20T10:00:{i // 3:02d}",
        })

    async def walk(self, sort_by: str, order: str, between=None) -> list[int]:
        seen: list[int] = []
        last = None
        while True:
            after = (last[sort_by], last["id"]) if last else None
            rows = await database.get_pings(limit=PAGE, sort_by=sort_by, sort_order=order, after=after)
            seen += [r["id"] for r in rows]
            if len(rows) < PAGE:
                return seen
            last = rows[-1]
            if between:
                await between()
                between = None

    async def test_cursor_walk_matches_the_full_ordering(self):
        for sort_by in ("detected_at", "date", "chat", "priority_score"):
            for order in ("DESC", "ASC"):
                with self.subTest(sort_by=sort_by, order=order):
                    full = [r["id"] for r in await database.get_pings(limit=0, sort_by=sort_by, sort_order=order)]
                    self.assertEqual(await self.walk(sort_by, order), full)

    async def test_a_row_arriving_mid_walk_is_neither_repeated_nor_skipped(self):
        before = [r["id"] for r in await database.get_pings(limit=0)]

        async def arrive():
            await self.add(99)   # newest detected_at: lands above the cursor

        walked = await self.walk("detected_at", "DESC", between=arrive)
        self.assertEqual(walked, before)
        self.assertEqual(len(walked), len(set(walked)))

    async def test_feed_fetch_continues_from_the_given_row(self):
        filt = FeedFilter(sort="m")
        first, more = await fetch(filt, {}, "", page_size=PAGE)
        self.assertTrue(more)
        second, _ = await fetch(filt, {}, "", page_size=PAGE, after=first[-1], offset=len(first))
        by_offset, _ = await fetch(filt._replace(page=2), {}, "", page_size=PAGE)
        self.assertEqual([r["id"] for r in second], [r["id"] for r in by_offset])


if __name__ == "__main__":
    unittest.main()
