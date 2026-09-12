"""Bot access keys, members, and broadcast message tracking (multi-user bot access)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from ._core import _connect, _now_iso


async def create_bot_key(
    label: str,
    secret: str,
    role: str = "viewer",
    expires_at: Optional[str] = None,
    permissions: str = "",
) -> dict:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO bot_access_keys (label, secret, role, created_at, expires_at, revoked, permissions)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (label or "", secret, role or "viewer", _now_iso(), expires_at, permissions or ""),
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


async def get_bot_key(key_id: int) -> Optional[dict]:
    """One key with its member_count, revoked or not — the panel edits both."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT k.*, (SELECT COUNT(*) FROM bot_members m WHERE m.key_id = k.id) AS member_count
                FROM bot_access_keys k WHERE k.id = ?
                """,
                (int(key_id),),
            )
        ).fetchone()
    return dict(row) if row else None


async def set_bot_key_permissions(key_id: int, permissions: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_access_keys SET permissions = ? WHERE id = ?",
            (permissions or "", int(key_id)),
        )
        await db.commit()


async def set_bot_key_label(key_id: int, label: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_access_keys SET label = ? WHERE id = ?",
            ((label or "").strip(), int(key_id)),
        )
        await db.commit()


async def set_bot_key_role(key_id: int, role: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_access_keys SET role = ? WHERE id = ?",
            (role or "viewer", int(key_id)),
        )
        await db.commit()


async def set_bot_key_expiry(key_id: int, expires_at: Optional[str]) -> None:
    """Set or clear the key's expiry. ``None`` means the invite never expires."""
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_access_keys SET expires_at = ? WHERE id = ?",
            (expires_at or None, int(key_id)),
        )
        await db.commit()


async def list_bot_key_members(key_id: int) -> list[dict]:
    """People who joined through this key, newest first."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM bot_members WHERE key_id = ? ORDER BY joined_at DESC",
                (int(key_id),),
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


async def set_bot_key_revoked(key_id: int, revoked: bool) -> None:
    """Flip the invite on or off. Revoking stops new redeems only — people who
    already joined keep the grants snapshotted on their member row."""
    async with _connect() as db:
        await db.execute(
            "UPDATE bot_access_keys SET revoked = ? WHERE id = ?",
            (1 if revoked else 0, int(key_id)),
        )
        await db.commit()


async def revoke_bot_key(key_id: int) -> None:
    await set_bot_key_revoked(key_id, True)


async def delete_bot_key(key_id: int) -> Optional[dict]:
    """Erase a key row for good. Returns the deleted key, or None if it was gone.

    Members who already joined with it keep their access and their grants — the
    grants are snapshotted onto the member row at redeem time. Their `key_id` is
    cleared so nothing points at a row that no longer exists; cut a person off
    by blocking them, not by deleting the key they came in through.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT k.*, (SELECT COUNT(*) FROM bot_members m WHERE m.key_id = k.id) AS member_count
                FROM bot_access_keys k
                WHERE k.id = ?
                """,
                (key_id,),
            )
        ).fetchone()
        if row is None:
            return None
        await db.execute("UPDATE bot_members SET key_id = NULL WHERE key_id = ?", (key_id,))
        await db.execute("DELETE FROM bot_access_keys WHERE id = ?", (key_id,))
        await db.commit()
    return dict(row)


async def upsert_bot_member(
    tg_id: int,
    tg_username: str,
    name: str,
    key_id: Optional[int],
    role: str = "viewer",
    permissions: str = "",
) -> None:
    """Create or refresh a member. Grants are copied from the redeemed key, so a
    later key edit or revoke never leaves an onboarded guest without a menu."""
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO bot_members (tg_id, tg_username, name, key_id, role, joined_at, last_seen_at, blocked, permissions)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                tg_username = excluded.tg_username,
                name = excluded.name,
                key_id = excluded.key_id,
                role = excluded.role,
                last_seen_at = excluded.last_seen_at,
                permissions = excluded.permissions
            """,
            (tg_id, tg_username or "", name or "", key_id, role or "viewer", _now_iso(), _now_iso(), permissions or ""),
        )
        await db.commit()


async def get_bot_member(tg_id: int) -> Optional[dict]:
    """One member row, with the label of the key they joined through.

    ``key_label`` is what ties a person to their row in the salary workbook, so
    it travels with the member everywhere the row goes — same join as
    ``list_bot_members``. A deleted key clears ``key_id``, and the label comes
    back NULL; revoke a key instead of deleting it to keep the link.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT m.*, k.label AS key_label
                FROM bot_members m
                LEFT JOIN bot_access_keys k ON k.id = m.key_id
                WHERE m.tg_id = ?
                """,
                (tg_id,),
            )
        ).fetchone()
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
