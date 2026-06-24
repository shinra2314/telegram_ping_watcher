"""Bot access keys, members, and broadcast message tracking (multi-user bot access)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from ._core import _connect, _now_iso


async def create_bot_key(label: str, secret: str, role: str = "viewer", expires_at: Optional[str] = None) -> dict:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO bot_access_keys (label, secret, role, created_at, expires_at, revoked)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (label or "", secret, role or "viewer", _now_iso(), expires_at),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM bot_access_keys WHERE id = ?", (cursor.lastrowid,))).fetchone()
    return dict(row)


async def list_bot_keys(include_revoked: bool = False) -> list[dict]:
    where = "" if include_revoked else "WHERE k.revoked = 0"
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                f"""
                SELECT k.*, (SELECT COUNT(*) FROM bot_members m WHERE m.key_id = k.id) AS member_count
                FROM bot_access_keys k
                {where}
                ORDER BY k.created_at DESC
                """
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_bot_key_by_secret(secret: str) -> Optional[dict]:
    if not secret:
        return None
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute("SELECT * FROM bot_access_keys WHERE secret = ?", (secret,))
        ).fetchone()
    if not row:
        return None
    key = dict(row)
    if key.get("revoked"):
        return None
    expires_at = key.get("expires_at")
    if expires_at and expires_at <= _now_iso():
        return None
    return key


async def revoke_bot_key(key_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE bot_access_keys SET revoked = 1 WHERE id = ?", (key_id,))
        await db.commit()


async def upsert_bot_member(tg_id: int, tg_username: str, name: str, key_id: Optional[int], role: str = "viewer") -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO bot_members (tg_id, tg_username, name, key_id, role, joined_at, last_seen_at, blocked)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(tg_id) DO UPDATE SET
                tg_username = excluded.tg_username,
                name = excluded.name,
                key_id = excluded.key_id,
                role = excluded.role,
                last_seen_at = excluded.last_seen_at
            """,
            (tg_id, tg_username or "", name or "", key_id, role or "viewer", _now_iso(), _now_iso()),
        )
        await db.commit()


async def get_bot_member(tg_id: int) -> Optional[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM bot_members WHERE tg_id = ?", (tg_id,))).fetchone()
    return dict(row) if row else None


async def list_bot_members() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT m.*, k.label AS key_label
                FROM bot_members m
                LEFT JOIN bot_access_keys k ON k.id = m.key_id
                ORDER BY m.joined_at DESC
                """
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def touch_bot_member(tg_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE bot_members SET last_seen_at = ? WHERE tg_id = ?", (_now_iso(), tg_id))
        await db.commit()


async def set_bot_member_blocked(tg_id: int, blocked: bool) -> None:
    async with _connect() as db:
        await db.execute("UPDATE bot_members SET blocked = ? WHERE tg_id = ?", (1 if blocked else 0, tg_id))
        await db.commit()


async def set_bot_member_prefs(tg_id: int, prefs: dict) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_members SET notification_prefs = ? WHERE tg_id = ?",
            (json.dumps(prefs, ensure_ascii=False), tg_id),
        )
        await db.commit()


async def save_broadcast_messages(token: str, rows: list[tuple[int, int]]) -> None:
    if not rows:
        return
    async with _connect() as db:
        await db.executemany(
            "INSERT INTO bot_broadcast_messages (token, tg_id, message_id, created_at) VALUES (?, ?, ?, ?)",
            [(token, int(tg_id), int(message_id), _now_iso()) for tg_id, message_id in rows],
        )
        await db.commit()


async def get_broadcast_messages(token: str) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM bot_broadcast_messages WHERE token = ?", (token,))
        ).fetchall()
    return [dict(r) for r in rows]


async def delete_broadcast_messages(token: str) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM bot_broadcast_messages WHERE token = ?", (token,))
        await db.commit()


async def prune_broadcast_messages(days: int = 7) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    async with _connect() as db:
        cursor = await db.execute("DELETE FROM bot_broadcast_messages WHERE created_at < ?", (cutoff,))
        await db.commit()
        return cursor.rowcount or 0
