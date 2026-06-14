"""Web Push subscriptions (PWA)."""
from __future__ import annotations

import aiosqlite

from ._core import _connect, _now_iso


async def save_push_subscription(endpoint: str, p256dh: str, auth: str) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth
            """,
            (endpoint, p256dh, auth, _now_iso()),
        )
        await db.commit()


async def delete_push_subscription(endpoint: str) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()


async def get_push_subscriptions() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions")).fetchall()
    return [dict(r) for r in rows]
