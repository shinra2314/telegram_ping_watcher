"""The owner's ping cards as an outbox (schema 24).

``pings.notified_at`` is when the owner's card for a ping went out and
``pings.win_notified_at`` when its 🏆 card did. NULL means the card is still
owed — the bot was offline, the send failed, or the process died between saving
the ping and sending it. ``ping_notify`` delivers what is owed.
"""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso, _parse_mentions

OWED_WHERE = "(notified_at IS NULL OR (is_win = 1 AND win_notified_at IS NULL))"


async def mark_ping_notified(ping_id: int, *, win: bool, at: Optional[str] = None) -> None:
    """Settle the owner's card for ``ping_id``; ``win`` settles the 🏆 card too.

    Only NULLs are filled, so a late retry never rewrites when the card really
    went out.
    """
    stamp = at or _now_iso()
    async with _connect() as db:
        await db.execute(
            """
            UPDATE pings
            SET notified_at = COALESCE(notified_at, ?),
                win_notified_at = CASE WHEN ? = 1 AND is_win = 1 THEN COALESCE(win_notified_at, ?)
                                       ELSE win_notified_at END
            WHERE id = ?
            """,
            (stamp, 1 if win else 0, stamp, int(ping_id)),
        )
        await db.commit()


async def get_ping_notify_state(ping_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, is_win, notified_at, win_notified_at FROM pings WHERE id = ?",
            (int(ping_id),),
        )).fetchone()
        return dict(row) if row else None


async def list_owed_ping_notifications(*, since: str, before: str, limit: int = 20) -> list[dict[str, Any]]:
    """Pings whose card is owed, detected in ``[since, before)``, oldest first.

    ``before`` keeps a grace period: a ping saved a moment ago is still being
    handled by the pipeline that found it. Copies of a win post are left out —
    their primary carries the card.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""
            SELECT * FROM pings
            WHERE {OWED_WHERE}
              AND duplicate_of IS NULL
              AND COALESCE(win_detected_at, detected_at) >= ?
              AND COALESCE(win_detected_at, detected_at) < ?
            ORDER BY COALESCE(win_detected_at, detected_at) ASC, id ASC
            LIMIT ?
            """,
            (since, before, int(limit)),
        )).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["mentions"] = _parse_mentions(item.get("mentions"))
        items.append(item)
    return items


async def expire_owed_ping_notifications(*, before: str) -> int:
    """Give up on cards owed since before ``before``; returns how many.

    After an outage that long a card per ping would be a flood of stale news,
    so they are settled in bulk and the owner gets one summary instead.
    """
    stamp = _now_iso()
    async with _connect() as db:
        cursor = await db.execute(
            f"""
            UPDATE pings
            SET notified_at = COALESCE(notified_at, ?),
                win_notified_at = CASE WHEN is_win = 1 THEN COALESCE(win_notified_at, ?) ELSE win_notified_at END
            WHERE {OWED_WHERE}
              AND COALESCE(win_detected_at, detected_at) < ?
            """,
            (stamp, stamp, before),
        )
        await db.commit()
        return int(cursor.rowcount or 0)


async def count_owed_ping_notifications() -> int:
    async with _connect() as db:
        row = await (await db.execute(
            f"SELECT COUNT(*) FROM pings WHERE {OWED_WHERE} AND duplicate_of IS NULL"
        )).fetchone()
        return int(row[0]) if row else 0
