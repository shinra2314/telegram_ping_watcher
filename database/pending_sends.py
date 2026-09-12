"""Durable queue for member notifications held back by a per-key send delay.

A key's ``delay_minutes`` grant postpones its members' copies. The wait has to
survive a restart, so scheduled copies live here rather than in an in-process
``asyncio.sleep``. Rows carry the same ``token`` as the immediate copies, so the
owner's "hide from friends" button reaches both.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite

from ._core import _connect, _now_iso


async def queue_pending_send(
    tg_id: int,
    send_at: str,
    message: str,
    *,
    token: str = "",
    notif_type: str = "mention",
    link: str = "",
    file_path: str = "",
) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO bot_pending_sends (tg_id, send_at, token, notif_type, message, link, file_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (int(tg_id), send_at, token or "", notif_type or "mention", message, link or "", file_path or "", _now_iso()),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def get_due_pending_sends(limit: int = 25) -> list[dict]:
    """Rows whose delay elapsed and that were neither sent nor cancelled."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT * FROM bot_pending_sends
                WHERE sent_at IS NULL AND cancelled_at IS NULL AND send_at <= ?
                ORDER BY send_at
                LIMIT ?
                """,
                (_now_iso(), int(limit)),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def mark_pending_send_result(row_id: int, *, sent: bool) -> None:
    """Close a row as delivered, or bump its attempt counter for a retry."""
    async with _connect() as db:
        if sent:
            await db.execute("UPDATE bot_pending_sends SET sent_at = ? WHERE id = ?", (_now_iso(), int(row_id)))
        else:
            await db.execute("UPDATE bot_pending_sends SET attempts = attempts + 1 WHERE id = ?", (int(row_id),))
        await db.commit()


async def cancel_pending_send(row_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_pending_sends SET cancelled_at = ? WHERE id = ? AND sent_at IS NULL AND cancelled_at IS NULL",
            (_now_iso(), int(row_id)),
        )
        await db.commit()


async def cancel_pending_sends(token: str) -> int:
    """Drop every undelivered copy of a broadcast; returns how many were dropped."""
    if not token:
        return 0
    async with _connect() as db:
        cursor = await db.execute(
            "UPDATE bot_pending_sends SET cancelled_at = ? WHERE token = ? AND sent_at IS NULL AND cancelled_at IS NULL",
            (_now_iso(), token),
        )
        await db.commit()
        return cursor.rowcount or 0


async def count_pending_sends(token: str) -> int:
    if not token:
        return 0
    async with _connect() as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM bot_pending_sends WHERE token = ? AND sent_at IS NULL AND cancelled_at IS NULL",
                (token,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def pending_sends_backlog() -> int:
    """Queued member copies not yet delivered — the outbox depth for /api/health.

    A rising number means ``pending_send_loop`` is wedged or the bot cannot
    send; nothing surfaced that before, so a stuck outbox was invisible.
    """
    async with _connect() as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM bot_pending_sends WHERE sent_at IS NULL AND cancelled_at IS NULL"
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def prune_pending_sends(days: int = 7) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM bot_pending_sends WHERE created_at < ? AND (sent_at IS NOT NULL OR cancelled_at IS NOT NULL)",
            (cutoff,),
        )
        await db.commit()
        return cursor.rowcount or 0
