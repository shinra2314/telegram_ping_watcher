"""Channel profiles and source reliability scores."""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso


async def upsert_channel_profile(
    chat_id: int,
    chat: str,
    username: str = "",
    description: str = "",
    deadline_at: Optional[str] = None,
    deadline_text: str = "",
    last_error: str = "",
) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO channel_profiles (
                chat_id, chat, username, description, deadline_at,
                deadline_text, fetched_at, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat = excluded.chat,
                username = excluded.username,
                description = excluded.description,
                deadline_at = excluded.deadline_at,
                deadline_text = excluded.deadline_text,
                fetched_at = excluded.fetched_at,
                last_error = excluded.last_error
            """,
            (chat_id, chat, username, description, deadline_at, deadline_text, _now_iso(), last_error),
        )
        await db.commit()


async def get_channel_profile(chat_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM channel_profiles WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_channel_deadlines(chat_id: int, deadline_at: Optional[str], deadline_text: str = "") -> int:
    if not deadline_at:
        return 0
    async with _connect() as db:
        cursor = await db.execute(
            """
            UPDATE pings
            SET deadline_at = ?,
                deadline_source = 'channel_description',
                deadline_text = ?
            WHERE chat_id = ?
              AND is_giveaway = 1
              AND COALESCE(deadline_source, '') != 'manual'
              AND (deadline_at IS NULL OR deadline_at = '')
              AND COALESCE(action_status, 'new') NOT IN ('claimed', 'scam', 'missed', 'closed')
            """,
            (deadline_at, deadline_text, chat_id),
        )
        await db.commit()
        return int(cursor.rowcount or 0)


async def recalculate_source_scores() -> None:
    now = _now_iso()
    async with _connect() as db:
        await db.execute("DELETE FROM source_scores")
        await db.execute(
            """
            INSERT INTO source_scores (
                chat_id, chat, chat_type, total_pings, wins, giveaways, important,
                resolved, noise, avg_priority, score, last_ping_at, updated_at
            )
            SELECT
                chat_id,
                COALESCE(MAX(chat), 'unknown') AS chat,
                COALESCE(MAX(chat_type), 'unknown') AS chat_type,
                COUNT(*) AS total_pings,
                COALESCE(SUM(is_win), 0) AS wins,
                COALESCE(SUM(is_giveaway), 0) AS giveaways,
                COALESCE(SUM(CASE WHEN priority_score >= 60 OR status = 'important' THEN 1 ELSE 0 END), 0) AS important,
                COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) AS resolved,
                COALESCE(SUM(CASE WHEN status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam' THEN 1 ELSE 0 END), 0) AS noise,
                COALESCE(AVG(priority_score), 0) AS avg_priority,
                ROUND(
                    COALESCE(AVG(priority_score), 0)
                    + COALESCE(SUM(is_win), 0) * 25
                    + COALESCE(SUM(is_giveaway), 0) * 4
                    + COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) * 2
                    - COALESCE(SUM(CASE WHEN status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam' THEN 1 ELSE 0 END), 0) * 8,
                    2
                ) AS score,
                MAX(detected_at) AS last_ping_at,
                ? AS updated_at
            FROM pings
            WHERE chat_id IS NOT NULL
            GROUP BY chat_id
            """,
            (now,),
        )
        await db.commit()


async def get_source_scores(limit: int = 50) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM source_scores ORDER BY score DESC, total_pings DESC LIMIT ?",
            (limit,),
        )).fetchall()
        return [dict(row) for row in rows]


async def get_source_score(chat_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM source_scores WHERE chat_id = ?", (chat_id,))).fetchone()
        return dict(row) if row else None
