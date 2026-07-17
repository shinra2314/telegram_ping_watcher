"""Pending member broadcasts awaiting owner approval (moderated mode)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso


async def create_pending_broadcast(
    ping_id: Optional[int],
    notif_type: str,
    message: str,
    link: str = "",
    file_path: str = "",
    expires_at: str = "",
    bc_token: str = "",
) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO pending_broadcasts (ping_id, notif_type, message, link, file_path, status, created_at, expires_at, bc_token)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (ping_id, notif_type, message, link or "", file_path or "", _now_iso(), expires_at, bc_token or ""),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def set_pending_broadcast_admin_message(pb_id: int, admin_message_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE pending_broadcasts SET admin_message_id = ? WHERE id = ?",
            (int(admin_message_id), int(pb_id)),
        )
        await db.commit()


async def get_pending_broadcast(pb_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM pending_broadcasts WHERE id = ?", (int(pb_id),))
        ).fetchone()
    return dict(row) if row else None


async def claim_pending_broadcast(pb_id: int, status: str, decided_by: Optional[int] = None) -> Optional[dict[str, Any]]:
    """Atomically move a pending row to a final status; None if already decided."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "UPDATE pending_broadcasts SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, decided_by, _now_iso(), int(pb_id)),
        )
        if not cursor.rowcount:
            return None
        row = await (
            await db.execute("SELECT * FROM pending_broadcasts WHERE id = ?", (int(pb_id),))
        ).fetchone()
        await db.commit()
    return dict(row) if row else None


async def get_due_pending_broadcasts(now_iso: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT * FROM pending_broadcasts
                WHERE status = 'pending' AND expires_at <= ?
                ORDER BY expires_at ASC
                LIMIT ?
                """,
                (now_iso, limit),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def prune_pending_broadcasts(days: int = 7) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM pending_broadcasts WHERE status != 'pending' AND created_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cursor.rowcount or 0
