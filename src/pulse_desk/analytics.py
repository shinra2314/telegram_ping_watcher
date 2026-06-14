"""Dashboard analytics aggregation."""
from __future__ import annotations

from typing import Any

import aiosqlite

from .app_ctx import state


def channel_account_stats() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_name, account in sorted(list(state.accounts_state.items())):
        rows.append({
            "session_name": session_name,
            "display": account.get("display") or account.get("username") or session_name,
            "status": account.get("status") or "unknown",
            "channels": int(account.get("channels_total") or 0),
            "last_channel_scan_at": account.get("last_channel_scan_at"),
        })
    return rows


async def build_analytics() -> dict[str, Any]:
    import database

    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        total = (await (await db.execute("SELECT COUNT(*) AS total FROM pings")).fetchone())["total"]
        new = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'new'")).fetchone())["count"]
        favorites = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_favorite = 1")).fetchone())["count"]
        important = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE priority_score >= 60 OR status = 'important'")).fetchone())["count"]
        resolved = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'resolved'")).fetchone())["count"]
        wins = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_win = 1")).fetchone())["count"]
        giveaways = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_giveaway = 1")).fetchone())["count"]
        noise = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam'")).fetchone())["count"]
        last_24h = (await (await db.execute(
            "SELECT COUNT(*) AS count FROM pings WHERE datetime(detected_at) >= datetime('now', '-1 day')"
        )).fetchone())["count"]
        last_7d = (await (await db.execute(
            "SELECT COUNT(*) AS count FROM pings WHERE datetime(detected_at) >= datetime('now', '-7 day')"
        )).fetchone())["count"]
        priority_row = await (await db.execute(
            "SELECT COALESCE(AVG(priority_score), 0) AS avg_priority, MIN(detected_at) AS first_seen, MAX(detected_at) AS last_seen FROM pings"
        )).fetchone()
        channel_chats_total = (await (await db.execute(
            "SELECT COUNT(DISTINCT chat_id) AS count FROM pings WHERE chat_type = 'channel' AND chat_id IS NOT NULL"
        )).fetchone())["count"]
        rows = await (await db.execute("SELECT chat_type, COUNT(*) AS count FROM pings GROUP BY chat_type")).fetchall()
        by_type = {row["chat_type"] or "unknown": row["count"] for row in rows}
        statuses = {row["status"] or "unknown": row["count"] for row in await (await db.execute(
            "SELECT status, COUNT(*) AS count FROM pings GROUP BY status"
        )).fetchall()}
        daily = [dict(row) for row in await (await db.execute(
            "SELECT date(detected_at) AS day, COUNT(*) AS count FROM pings GROUP BY day ORDER BY day DESC LIMIT 7"
        )).fetchall()]
        hourly = {row["hour"]: row["count"] for row in await (await db.execute(
            "SELECT strftime('%H', detected_at) AS hour, COUNT(*) AS count FROM pings GROUP BY hour ORDER BY hour ASC"
        )).fetchall()}
        top_chats = [dict(row) for row in await (await db.execute(
            "SELECT chat, COUNT(*) AS count FROM pings GROUP BY chat ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        channels_by_account = channel_account_stats()
        channel_memberships_total = sum(int(row.get("channels") or 0) for row in channels_by_account)
        return {
            "total_pings": total,
            "new_pings": new,
            "favorites": favorites,
            "important": important,
            "resolved": resolved,
            "wins": wins,
            "giveaways": giveaways,
            "noise": noise,
            "last_24h": last_24h,
            "last_7d": last_7d,
            "avg_priority": round(float(priority_row["avg_priority"] or 0), 2),
            "first_seen": priority_row["first_seen"],
            "last_seen": priority_row["last_seen"],
            "unread_rate": round((new / total * 100), 2) if total else 0,
            "win_rate": round((wins / total * 100), 2) if total else 0,
            "giveaway_rate": round((giveaways / total * 100), 2) if total else 0,
            "channel_chats_total": channel_chats_total,
            "channel_memberships_total": channel_memberships_total,
            "total_channels": channel_memberships_total or channel_chats_total,
            "channels_by_account": channels_by_account,
            "by_type": by_type,
            "statuses": statuses,
            "daily": daily,
            "hourly": hourly,
            "top_chats": top_chats,
            "accounts_online": len(state.clients),
        }
