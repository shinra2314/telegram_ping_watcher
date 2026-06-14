"""Scan-run bookkeeping (history sweeps)."""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso


async def start_scan_run(total_accounts: int, total_usernames: int) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO scan_runs (
                status, started_at, total_accounts, processed_accounts,
                total_usernames, processed_usernames, found, last_error, cancel_requested
            )
            VALUES ('running', ?, ?, 0, ?, 0, 0, NULL, 0)
            """,
            (_now_iso(), total_accounts, total_usernames),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_scan_run(scan_run_id: int, **fields: Any) -> None:
    allowed = {
        "status",
        "finished_at",
        "total_accounts",
        "processed_accounts",
        "total_usernames",
        "processed_usernames",
        "found",
        "last_error",
        "cancel_requested",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    async with _connect() as db:
        await db.execute(f"UPDATE scan_runs SET {set_clause} WHERE id = ?", (*updates.values(), scan_run_id))
        await db.commit()


async def get_latest_scan_run() -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_scan_runs(limit: int = 20) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,))).fetchall()
        return [dict(row) for row in rows]


async def interrupt_stale_scan_runs(reason: str = "Application restarted before scan finished") -> list[dict[str, Any]]:
    finished_at = _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'running'
            ORDER BY id DESC
            """
        )).fetchall()
        if not rows:
            return []
        await db.execute(
            """
            UPDATE scan_runs
            SET status = 'interrupted',
                finished_at = ?,
                last_error = COALESCE(NULLIF(last_error, ''), ?),
                cancel_requested = 0
            WHERE status = 'running'
            """,
            (finished_at, reason),
        )
        await db.commit()
        return [dict(row) for row in rows]


async def get_scan_run_health(limit: int = 5) -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        running = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'running'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        interrupted = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'interrupted'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        counts = await (await db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM scan_runs
            GROUP BY status
            ORDER BY count DESC
            """
        )).fetchall()
        return {
            "running": [dict(row) for row in running],
            "recent_interrupted": [dict(row) for row in interrupted],
            "status_counts": [dict(row) for row in counts],
        }
