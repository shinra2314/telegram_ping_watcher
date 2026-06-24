"""Per-member scheduled-access windows + audit trail.

Stores the rules that ``pulse_desk.access_control`` resolves at request time.
``access_schedule`` holds windows (one per row); ``access_audit`` is an
append-only journal for changes and automatic flips (enables rollback).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso


def _utc_now_iso() -> str:
    """UTC wall-clock (naive ISO). Window start/end are interpreted as UTC by
    access_control, so manual blackouts must be stamped in UTC, not local."""
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def _dump_repeat(repeat_rule: Any) -> str:
    if isinstance(repeat_rule, str):
        return repeat_rule or '{"type":"none"}'
    return json.dumps(repeat_rule or {"type": "none"}, ensure_ascii=False)


async def create_access_window(
    tg_id: int,
    enabled: bool,
    repeat_rule: Any = None,
    *,
    timezone: str = "UTC",
    priority: int = 100,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    label: str = "",
    created_by: Optional[int] = None,
) -> dict:
    now = _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            INSERT INTO access_schedule
                (tg_id, enabled, active, start_at, end_at, timezone, repeat_rule,
                 priority, label, created_by, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_id, 1 if enabled else 0, start_at, end_at, timezone or "UTC",
                _dump_repeat(repeat_rule), int(priority), label or "", created_by, now, now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM access_schedule WHERE id = ?", (cursor.lastrowid,))).fetchone()
    return dict(row)


async def create_disable_until_window(
    tg_id: int, until: Optional[str], *, created_by: Optional[int] = None, label: str = "off until"
) -> dict:
    """One-shot blackout from now until ``until`` (or open-ended), high priority."""
    return await create_access_window(
        tg_id, enabled=False, repeat_rule={"type": "none"},
        start_at=_utc_now_iso(), end_at=until, priority=1000, label=label, created_by=created_by,
    )


async def get_access_window(window_id: int) -> Optional[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM access_schedule WHERE id = ?", (window_id,))).fetchone()
    return dict(row) if row else None


async def list_access_windows(tg_id: int, active_only: bool = True) -> list[dict]:
    where = "WHERE tg_id = ?" + (" AND active = 1" if active_only else "")
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(f"SELECT * FROM access_schedule {where} ORDER BY priority DESC, id ASC", (tg_id,))
        ).fetchall()
    return [dict(r) for r in rows]


async def list_all_access_windows(active_only: bool = True) -> dict[int, list[dict]]:
    where = "WHERE active = 1" if active_only else ""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(f"SELECT * FROM access_schedule {where} ORDER BY tg_id, priority DESC")
        ).fetchall()
    grouped: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[int(r["tg_id"])].append(dict(r))
    return dict(grouped)


async def deactivate_access_window(window_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE access_schedule SET active = 0, updated_at = ? WHERE id = ?",
            (_now_iso(), window_id),
        )
        await db.commit()


async def set_access_window_active(window_id: int, active: bool) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE access_schedule SET active = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, _now_iso(), window_id),
        )
        await db.commit()


async def set_member_default_policy(tg_id: int, policy: str) -> None:
    policy = policy if policy in ("allow", "deny") else "allow"
    async with _connect() as db:
        await db.execute("UPDATE bot_members SET access_default_policy = ? WHERE tg_id = ?", (policy, tg_id))
        await db.commit()


async def set_member_timezone(tg_id: int, tz: str) -> None:
    async with _connect() as db:
        await db.execute("UPDATE bot_members SET timezone = ? WHERE tg_id = ?", (tz or "", tg_id))
        await db.commit()


async def record_access_audit(
    tg_id: int,
    schedule_id: Optional[int],
    action: str,
    actor: str,
    old_value: Any = None,
    new_value: Any = None,
) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO access_audit (tg_id, schedule_id, action, actor, old_value, new_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tg_id, schedule_id, action, actor,
                json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                _now_iso(),
            ),
        )
        await db.commit()


async def get_access_audit(tg_id: int, limit: int = 20) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM access_audit WHERE tg_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (tg_id, limit),
            )
        ).fetchall()
    return [dict(r) for r in rows]
