"""Crypto market snapshots."""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ._core import _connect, _now_iso


async def save_market_snapshot(snapshot: dict[str, Any]) -> None:
    fetched_at = snapshot.get("fetched_at_iso") or _now_iso()
    snapshot["fetched_at_iso"] = fetched_at
    async with _connect() as db:
        await db.execute(
            "INSERT INTO market_history (fetched_at_iso, data) VALUES (?, ?)",
            (fetched_at, json.dumps(snapshot, ensure_ascii=False)),
        )
        await db.commit()


async def get_market_history(limit: int = 50, since_iso: str | None = None) -> list[dict[str, Any]]:
    """Newest snapshots first; `since_iso` narrows the result to one time window."""
    query = "SELECT fetched_at_iso, data FROM market_history"
    params: list[Any] = []
    if since_iso:
        query += " WHERE fetched_at_iso >= ?"
        params.append(since_iso)
    query += " ORDER BY fetched_at_iso DESC LIMIT ?"
    params.append(limit)
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = json.loads(row["data"])
                item["fetched_at_iso"] = item.get("fetched_at_iso") or row["fetched_at_iso"]
                result.append(item)
            return result
