"""Member engagement with broadcast giveaways («участвую» / «пропустил»)."""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso

ENGAGEMENT_ACTIONS = ("joined", "skipped")


async def set_member_engagement(tg_id: int, ping_id: int, action: str) -> None:
    if action not in ENGAGEMENT_ACTIONS:
        raise ValueError(f"unknown engagement action: {action}")
    now = _now_iso()
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO member_engagement (tg_id, ping_id, action, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tg_id, ping_id) DO UPDATE SET
                action = excluded.action,
                updated_at = excluded.updated_at
            """,
            (int(tg_id), int(ping_id), action, now, now),
        )
        await db.commit()


async def get_member_engagement(tg_id: int, ping_id: int) -> Optional[str]:
    async with _connect() as db:
        row = await (
            await db.execute(
                "SELECT action FROM member_engagement WHERE tg_id = ? AND ping_id = ?",
                (int(tg_id), int(ping_id)),
            )
        ).fetchone()
    return str(row[0]) if row else None


async def member_engagement_stats(tg_id: int) -> dict[str, int]:
    async with _connect() as db:
        rows = await (
            await db.execute(
                "SELECT action, COUNT(*) FROM member_engagement WHERE tg_id = ? GROUP BY action",
                (int(tg_id),),
            )
        ).fetchall()
    stats = {"joined": 0, "skipped": 0}
    for action, count in rows:
        if action in stats:
            stats[action] = int(count)
    return stats


async def engagement_summary() -> dict[str, Any]:
    """Overall claim-rate inputs: totals and per-member breakdown."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT tg_id,
                       SUM(CASE WHEN action = 'joined' THEN 1 ELSE 0 END) AS joined,
                       SUM(CASE WHEN action = 'skipped' THEN 1 ELSE 0 END) AS skipped
                FROM member_engagement
                GROUP BY tg_id
                ORDER BY joined DESC
                """
            )
        ).fetchall()
    members = [dict(r) for r in rows]
    return {
        "members": members,
        "joined": sum(int(m["joined"] or 0) for m in members),
        "skipped": sum(int(m["skipped"] or 0) for m in members),
    }
