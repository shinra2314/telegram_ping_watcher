"""Key-value app settings with change history."""
from __future__ import annotations

import json
from typing import Any, Optional

import aiosqlite

from ._core import _connect, _now_iso


async def get_setting(key: str, default: Any = None) -> Any:
    async with _connect() as db:
        async with db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return default


async def set_setting(key: str, value: Any) -> None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        existing = await (await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))).fetchone()
        old_value = existing["value"] if existing else None
        new_value = json.dumps(value, ensure_ascii=False)
        await db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, new_value, _now_iso()),
        )
        if old_value != new_value:
            await db.execute(
                "INSERT INTO settings_history (key, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)",
                (key, old_value, new_value, _now_iso()),
            )
        await db.commit()


async def get_settings_history(*, key: Optional[str] = None, limit: int = 50) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if key:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history WHERE key = ? ORDER BY changed_at DESC LIMIT ?",
                    (key, limit),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history ORDER BY changed_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
    return [dict(r) for r in rows]
