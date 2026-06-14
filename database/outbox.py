"""Durable live-event outbox (SSE backfill)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

from ._core import _connect, _now_iso


async def enqueue_outbox_event(event_type: str, payload: dict[str, Any]) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT INTO app_notifications_outbox (created_at, event_type, payload) VALUES (?, ?, ?)",
            (_now_iso(), event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_outbox_stats() -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        total = await (await db.execute("SELECT COUNT(*) AS value FROM app_notifications_outbox")).fetchone()
        pending = await (await db.execute(
            "SELECT COUNT(*) AS value FROM app_notifications_outbox WHERE delivered_at IS NULL OR delivered_at = ''"
        )).fetchone()
        latest = await (await db.execute("SELECT MAX(created_at) AS value FROM app_notifications_outbox")).fetchone()
        oldest = await (await db.execute("SELECT MIN(created_at) AS value FROM app_notifications_outbox")).fetchone()
        recent = await (await db.execute(
            "SELECT COUNT(*) AS value FROM app_notifications_outbox WHERE created_at >= ?",
            ((datetime.now() - timedelta(hours=1)).replace(microsecond=0).isoformat(),),
        )).fetchone()
        by_type_rows = await (await db.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM app_notifications_outbox
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 8
            """
        )).fetchall()
        return {
            "total": int(total["value"] or 0),
            "pending": int(pending["value"] or 0),
            "recent_1h": int(recent["value"] or 0),
            "pressure": "high" if int(total["value"] or 0) > 4500 else "ok",
            "oldest_created_at": oldest["value"],
            "latest_created_at": latest["value"],
            "by_type": [dict(row) for row in by_type_rows],
        }


async def get_outbox_after(last_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT * FROM app_notifications_outbox
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (last_id, limit),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result


async def cleanup_outbox(days: int = 2, max_events: int = 5000) -> None:
    cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    async with _connect() as db:
        await db.execute("DELETE FROM app_notifications_outbox WHERE created_at < ?", (cutoff,))
        if max_events > 0:
            await db.execute(
                """
                DELETE FROM app_notifications_outbox
                WHERE id NOT IN (
                    SELECT id
                    FROM app_notifications_outbox
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (max_events,),
            )
        await db.commit()
