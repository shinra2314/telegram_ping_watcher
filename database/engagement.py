"""Member engagement with broadcast giveaways («участвую» / «пропустил»)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

import aiosqlite

from ._core import _connect, _now_iso

ENGAGEMENT_ACTIONS = ("joined", "skipped")


async def set_member_engagement(tg_id: int, ping_id: int, action: str) -> None:
    if action not in ENGAGEMENT_ACTIONS:
        raise ValueError(f"unknown engagement action: {action}")
    now = _now_iso()
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO member_engagement (tg_id, ping_id, action, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tg_id, ping_id) DO UPDATE SET
                action = excluded.action,
                updated_at = excluded.updated_at
            """,
            (int(tg_id), int(ping_id), action, now, now),
        )
        await db.commit()


async def get_member_engagement(tg_id: int, ping_id: int) -> Optional[str]:
    async with _connect() as db:
        row = await (
            await db.execute(
                "SELECT action FROM member_engagement WHERE tg_id = ? AND ping_id = ?",
                (int(tg_id), int(ping_id)),
            )
        ).fetchone()
    return str(row[0]) if row else None


async def member_engagement_for(tg_id: int, ping_ids: Sequence[int]) -> dict[int, str]:
    """This member's answer per ping, for a page of rows in one query.

    The panel marks the rows a guest has already answered and filters to the
    ones they have not; one ``get_member_engagement`` per row would be a query
    per line of the list.
    """
    ids = sorted({int(i) for i in ping_ids})
    if not ids:
        return {}
    found: dict[int, str] = {}
    async with _connect() as db:
        # SQLite caps bound parameters; a queue page is far below it, the
        # chunking only keeps a whole-board call honest.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = await (await db.execute(
                f"SELECT ping_id, action FROM member_engagement WHERE tg_id = ? "
                f"AND ping_id IN ({','.join('?' * len(chunk))})",
                (int(tg_id), *chunk),
            )).fetchall()
            found.update({int(r[0]): str(r[1]) for r in rows})
    return found


async def member_engagement_stats(tg_id: int) -> dict[str, int]:
    async with _connect() as db:
        rows = await (
            await db.execute(
                "SELECT action, COUNT(*) FROM member_engagement WHERE tg_id = ? GROUP BY action",
                (int(tg_id),),
            )
        ).fetchall()
    stats = {"joined": 0, "skipped": 0}
    for action, count in rows:
        if action in stats:
            stats[action] = int(count)
    return stats


async def engagement_summary() -> dict[str, Any]:
    """Overall claim-rate inputs: totals and per-member breakdown."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT tg_id,
                       SUM(CASE WHEN action = 'joined' THEN 1 ELSE 0 END) AS joined,
                       SUM(CASE WHEN action = 'skipped' THEN 1 ELSE 0 END) AS skipped
                FROM member_engagement
                GROUP BY tg_id
                ORDER BY joined DESC
                """
            )
        ).fetchall()
    members = [dict(r) for r in rows]
    return {
        "members": members,
        "joined": sum(int(m["joined"] or 0) for m in members),
        "skipped": sum(int(m["skipped"] or 0) for m in members),
    }


async def member_engagement_since(tg_id: int, since_iso: Optional[str] = None) -> dict[str, int]:
    """«Участвую» / «пропустил» of one member, optionally from a moment on."""
    sql = "SELECT action, COUNT(*) FROM member_engagement WHERE tg_id = ?"
    params: list[Any] = [int(tg_id)]
    if since_iso:
        sql += " AND updated_at >= ?"
        params.append(since_iso)
    async with _connect() as db:
        rows = await (await db.execute(sql + " GROUP BY action", params)).fetchall()
    stats = {"joined": 0, "skipped": 0}
    for action, count in rows:
        if action in stats:
            stats[action] = int(count)
    return stats


async def account_win_stats(usernames: list[str], since_iso: Optional[str] = None) -> dict[str, int]:
    """Wins about these tracked accounts: how many, how many already claimed.

    Claimed means the owner marked it so (``giveaway_status`` or
    ``action_status`` = claimed) — the same bit the debts board and the
    Obsidian note use.
    """
    names = sorted({u.strip().lstrip("@").lower() for u in usernames if u and u.strip()})
    if not names:
        return {"wins": 0, "claimed": 0}
    sql = (
        "SELECT COUNT(*), SUM(CASE WHEN COALESCE(p.giveaway_status, '') = 'claimed' "
        "OR COALESCE(p.action_status, '') = 'claimed' THEN 1 ELSE 0 END) "
        "FROM pings p WHERE p.is_win = 1 AND p.duplicate_of IS NULL AND p.id IN ("
        f"SELECT ping_id FROM ping_mentions WHERE lower(username) IN ({','.join('?' * len(names))}))"
    )
    params: list[Any] = list(names)
    if since_iso:
        sql += " AND p.detected_at >= ?"
        params.append(since_iso)
    async with _connect() as db:
        row = await (await db.execute(sql, params)).fetchone()
    return {"wins": int(row[0] or 0), "claimed": int(row[1] or 0)}
