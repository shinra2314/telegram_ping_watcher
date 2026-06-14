"""Read-only stats and report aggregation."""
from __future__ import annotations

from typing import Any

import aiosqlite

from ._core import _connect, _now_iso


async def get_account_ping_stats() -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT sender_id, sender, COUNT(*) AS total,
                   COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   MAX(detected_at) AS last_ping_at
            FROM pings
            GROUP BY sender_id, sender
            """
        )).fetchall()
        return [dict(row) for row in rows]


async def get_report_data(limit: int = 200) -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        totals = dict(await (await db.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status = 'new'), 0) AS new_count,
                   COALESCE(SUM(status = 'important'), 0) AS important_count,
                   COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            """
        )).fetchone())
        top_chats = [dict(row) for row in await (await db.execute(
            """
            SELECT chat, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            GROUP BY chat
            ORDER BY avg_priority DESC, count DESC
            LIMIT 15
            """
        )).fetchall()]
        recent = [dict(row) for row in await (await db.execute(
            "SELECT * FROM pings ORDER BY priority_score DESC, detected_at DESC LIMIT ?",
            (limit,),
        )).fetchall()]
        return {"generated_at": _now_iso(), "totals": totals, "top_chats": top_chats, "recent": recent}


async def get_db_stats() -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute("SELECT COUNT(*) AS count FROM pings")).fetchone())["count"]
        unique_chats = (await (await db.execute("SELECT COUNT(DISTINCT chat_id) AS count FROM pings")).fetchone())["count"]
        favorites = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_favorite = 1")).fetchone())["count"]
        return {"total": total, "unique_chats": unique_chats, "favorites": favorites}


async def get_detailed_stats() -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        top_chats = [dict(row) for row in await (await db.execute(
            "SELECT chat, COUNT(*) AS count FROM pings GROUP BY chat ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        top_senders = [dict(row) for row in await (await db.execute(
            "SELECT sender, COUNT(*) AS count FROM pings GROUP BY sender ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        monthly = [dict(row) for row in await (await db.execute(
            "SELECT strftime('%Y-%m', detected_at) AS month, COUNT(*) AS count FROM pings GROUP BY month ORDER BY month DESC"
        )).fetchall()]
        return {"top_chats": top_chats, "top_senders": top_senders, "monthly": monthly}
