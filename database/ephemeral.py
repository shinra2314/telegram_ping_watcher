"""Bot messages scheduled for deletion (auto-cleaned minor notifications).

Written when a notification goes out to someone who switched auto-delete on;
drained by the ``bot-janitor`` job. Telegram refuses to let a bot delete a
private-chat message older than 48 hours, so a row whose moment passed long ago
is dropped without a delete attempt (``prune_ephemeral_messages``).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

import aiosqlite

from ._core import _connect, _now_iso

# Past this age Telegram answers every delete with MESSAGE_DELETE_FORBIDDEN.
DELETE_WINDOW = timedelta(hours=48)


async def schedule_message_deletion(
    rows: Iterable[tuple[int, int]], delete_after: datetime, kind: str = ""
) -> int:
    """Remember (chat_id, message_id) pairs to delete at ``delete_after``."""
    values = [(int(chat), int(mid), kind, _now_iso(), delete_after.replace(microsecond=0).isoformat())
              for chat, mid in rows if chat and mid]
    if not values:
        return 0
    async with _connect() as db:
        await db.executemany(
            "INSERT INTO bot_ephemeral_messages (chat_id, message_id, kind, created_at, delete_after) "
            "VALUES (?, ?, ?, ?, ?)",
            values,
        )
        await db.commit()
    return len(values)


async def get_due_ephemeral_messages(now: datetime | None = None, limit: int = 200) -> list[dict]:
    moment = (now or datetime.now()).replace(microsecond=0).isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM bot_ephemeral_messages WHERE delete_after <= ? ORDER BY delete_after LIMIT ?",
            (moment, limit),
        )).fetchall()
    return [dict(row) for row in rows]


async def delete_ephemeral_rows(ids: Iterable[int]) -> int:
    batch = [int(i) for i in ids]
    if not batch:
        return 0
    async with _connect() as db:
        cur = await db.execute(
            f"DELETE FROM bot_ephemeral_messages WHERE id IN ({','.join('?' * len(batch))})", batch
        )
        await db.commit()
        return cur.rowcount or 0


async def prune_ephemeral_messages(now: datetime | None = None) -> int:
    """Drop rows whose message is already too old for Telegram to delete."""
    cutoff = ((now or datetime.now()) - DELETE_WINDOW).replace(microsecond=0).isoformat()
    async with _connect() as db:
        cur = await db.execute("DELETE FROM bot_ephemeral_messages WHERE created_at < ?", (cutoff,))
        await db.commit()
        return cur.rowcount or 0
