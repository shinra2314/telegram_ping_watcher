"""Incremental scan checkpoints (per session/username)."""
from __future__ import annotations

from ._core import _connect, _now_iso


async def get_checkpoint(session_name: str, username: str) -> int:
    async with _connect() as db:
        async with db.execute(
            "SELECT last_message_id FROM scan_checkpoints WHERE session_name = ? AND username = ?",
            (session_name, username),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


async def get_checkpoints(session_name: str, usernames: list[str]) -> dict[str, int]:
    keys = [str(username) for username in usernames if str(username)]
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    async with _connect() as db:
        rows = await (
            await db.execute(
                f"""
                SELECT username, last_message_id
                FROM scan_checkpoints
                WHERE session_name = ? AND username IN ({placeholders})
                """,
                (session_name, *keys),
            )
        ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}


async def get_latest_checkpoints(usernames: list[str]) -> dict[str, int]:
    keys = [str(username) for username in usernames if str(username)]
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    async with _connect() as db:
        rows = await (
            await db.execute(
                f"""
                SELECT username, MAX(last_message_id)
                FROM scan_checkpoints
                WHERE username IN ({placeholders})
                GROUP BY username
                """,
                keys,
            )
        ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}


async def save_checkpoint(session_name: str, username: str, last_message_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO scan_checkpoints (session_name, username, last_message_id, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_name, username, last_message_id, _now_iso()),
        )
        await db.commit()


async def save_checkpoints(session_name: str, checkpoints: dict[str, int]) -> None:
    rows = [
        (session_name, str(username), int(last_message_id), _now_iso())
        for username, last_message_id in checkpoints.items()
        if str(username) and int(last_message_id or 0) > 0
    ]
    if not rows:
        return
    async with _connect() as db:
        await db.executemany(
            """
            INSERT OR REPLACE INTO scan_checkpoints (session_name, username, last_message_id, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()
