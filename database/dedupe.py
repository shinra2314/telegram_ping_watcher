"""Storage side of win de-duplication (rules live in pulse_desk/dedupe.py)."""
from __future__ import annotations

from typing import Iterable, Optional

import aiosqlite

from ._core import _connect

_COLUMNS = "id, chat, chat_id, chat_type, text, mentions, detected_at, duplicate_of, giveaway_status, action_status"


async def get_wins_for_dedupe(since_iso: Optional[str] = None, *, primaries_only: bool = False) -> list[dict]:
    sql = f"SELECT {_COLUMNS} FROM pings WHERE is_win = 1"
    params: list = []
    if since_iso:
        sql += " AND detected_at >= ?"
        params.append(since_iso)
    if primaries_only:
        sql += " AND duplicate_of IS NULL"
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(sql + " ORDER BY detected_at", params)).fetchall()
    return [dict(r) for r in rows]


async def mark_duplicates(primary_id: int, duplicate_ids: Iterable[int]) -> int:
    """Point copies (and any copies already pointing at them) at the primary.

    A duplicate that was never handled takes the primary's statuses, so it drops
    off the board the moment the primary is claimed or marked scam.
    """
    ids = [int(i) for i in duplicate_ids if int(i) != int(primary_id)]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    async with _connect() as db:
        await db.execute(f"UPDATE pings SET duplicate_of = ? WHERE duplicate_of IN ({marks})", (primary_id, *ids))
        # Rows already pointing at this primary are not counted: the startup
        # backfill runs on every start and must report only new links.
        cur = await db.execute(
            f"UPDATE pings SET duplicate_of = ? WHERE id IN ({marks}) AND COALESCE(duplicate_of, 0) != ?",
            (primary_id, *ids, primary_id),
        )
        await db.execute("UPDATE pings SET duplicate_of = NULL WHERE id = ?", (primary_id,))
        # A primary already handled (claimed, scam) closes its new copies too.
        await db.execute(
            f"""
            UPDATE pings SET
                giveaway_status = (SELECT giveaway_status FROM pings WHERE id = ?),
                action_status = (SELECT action_status FROM pings WHERE id = ?)
            WHERE id IN ({marks})
              AND (SELECT COALESCE(action_status, 'new') FROM pings WHERE id = ?)
                  IN ('claimed', 'scam', 'missed', 'closed')
            """,
            (primary_id, primary_id, *ids, primary_id),
        )
        await db.commit()
        return cur.rowcount or 0


async def propagate_status_to_duplicates(ping_id: int, giveaway_status: Optional[str],
                                         action_status: Optional[str]) -> int:
    updates, params = [], []
    if giveaway_status is not None:
        updates.append("giveaway_status = ?")
        params.append(giveaway_status)
    if action_status is not None:
        updates.append("action_status = ?")
        params.append(action_status)
    if not updates:
        return 0
    async with _connect() as db:
        cur = await db.execute(
            f"UPDATE pings SET {', '.join(updates)} WHERE duplicate_of = ?", (*params, int(ping_id))
        )
        await db.commit()
        return cur.rowcount or 0


async def get_duplicates(ping_id: int) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, chat, chat_type, link, detected_at FROM pings WHERE duplicate_of = ? ORDER BY detected_at",
            (int(ping_id),),
        )).fetchall()
    return [dict(r) for r in rows]
