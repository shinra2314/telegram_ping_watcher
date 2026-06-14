"""Application event log."""
from __future__ import annotations

import json
from typing import Any, Optional

import aiosqlite

from ._core import _connect, _json_loads, _now_iso


async def record_event(level: str, source: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO app_events (created_at, level, source, message, context) VALUES (?, ?, ?, ?, ?)",
            (_now_iso(), level.upper(), source, message, json.dumps(context or {}, ensure_ascii=False)),
        )
        await db.commit()


async def get_events(limit: int = 100, level: Optional[str] = None) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM app_events"
        params: list[Any] = []
        if level:
            query += " WHERE level = ?"
            params.append(level.upper())
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = await (await db.execute(query, params)).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["context"] = json.loads(item.get("context") or "{}")
            except json.JSONDecodeError:
                item["context"] = {}
            events.append(item)
        return events


async def get_recent_problem_events(limit: int = 10) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT *
            FROM app_events
            WHERE UPPER(level) IN ('ERROR', 'WARNING')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = _json_loads(item.get("context"), {})
            events.append(item)
        return events
