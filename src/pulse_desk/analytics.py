"""Analytics aggregation.

`build_analytics` feeds the dashboard summary and the bot's home/summary cards;
`build_detailed_analytics` backs the bot's 📈 Аналитика section (the web app has
no analytics page any more — the whole report lives in the bot).
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from datetime import datetime, timedelta

import aiosqlite

from .app_ctx import state
from .latency import build_latency

# Seconds an analytics snapshot stays usable. The bot's 📈 section re-runs 24
# aggregates on every tab switch, and the numbers cannot move between two clicks
# a second apart — so the second click was pure cost, and almost always ended in
# a MessageNotModified anyway.
ANALYTICS_TTL_SECONDS = 60.0

# name -> (monotonic stamp, value)
_cache: dict[str, tuple[float, Any]] = {}


def cache_fresh(stamp: Optional[float], ttl: float, now: float) -> bool:
    """True when a snapshot taken at `stamp` is still within `ttl` of `now`.

    Pure, so the expiry rule is unit-testable without a clock.
    """
    if stamp is None:
        return False
    return (now - stamp) < ttl


def invalidate_analytics_cache() -> None:
    """Drop cached snapshots — called when a new ping lands."""
    _cache.clear()


async def _cached(name: str, build: Callable[[], Any], ttl: float = ANALYTICS_TTL_SECONDS) -> Any:
    entry = _cache.get(name)
    now = time.monotonic()
    if entry and cache_fresh(entry[0], ttl, now):
        return entry[1]
    value = await build()
    _cache[name] = (now, value)
    return value


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


async def build_home_counters() -> dict[str, Any]:
    """The three numbers the bot's home screen actually prints.

    ``build_analytics`` computes 16 aggregates over the whole ``pings`` table;
    the home card used three of them. This is one pass for the one counter that
    needs SQL — ``accounts_online`` is in-memory.
    """
    import database

    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        row = await (await db.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'new' THEN 1 ELSE 0 END) AS new_pings FROM pings"
        )).fetchone()
    return {
        "total_pings": int(row["total"] or 0),
        "new_pings": int(row["new_pings"] or 0),
        "accounts_online": len(state.clients),
    }


async def _build_analytics_uncached() -> dict[str, Any]:
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


async def _build_detailed_analytics_uncached() -> dict[str, Any]:
    """Heavy per-dimension report: who posts, where, when, and how it ends."""
    import database
    from database import get_source_scores

    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        heatmap = [dict(row) for row in await (await db.execute(
            "SELECT strftime('%H', detected_at) AS hour, chat_type, COUNT(*) AS count FROM pings GROUP BY hour, chat_type"
        )).fetchall()]
        senders = [dict(row) for row in await (await db.execute(
            """
            SELECT sender, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins
            FROM pings
            GROUP BY sender
            ORDER BY wins DESC, count DESC
            LIMIT 10
            """
        )).fetchall()]
        chats = [dict(row) for row in await (await db.execute(
            """
            SELECT chat, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            GROUP BY chat
            ORDER BY avg_priority DESC, wins DESC, count DESC
            LIMIT 12
            """
        )).fetchall()]
        priorities = [dict(row) for row in await (await db.execute(
            "SELECT priority_label, COUNT(*) AS count FROM pings GROUP BY priority_label ORDER BY count DESC"
        )).fetchall()]
        daily_quality = [dict(row) for row in await (await db.execute(
            """
            SELECT
                date(detected_at) AS day,
                COUNT(*) AS total,
                COALESCE(SUM(is_win), 0) AS wins,
                COALESCE(SUM(is_giveaway), 0) AS giveaways,
                COALESCE(SUM(CASE WHEN priority_score >= 60 OR status = 'important' THEN 1 ELSE 0 END), 0) AS important,
                COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) AS resolved
            FROM pings
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
            """
        )).fetchall()]
        top_mentions = [dict(row) for row in await (await db.execute(
            """
            SELECT username, COUNT(*) AS count
            FROM ping_mentions
            GROUP BY username
            ORDER BY count DESC, username ASC
            LIMIT 12
            """
        )).fetchall()]
        status_flow = [dict(row) for row in await (await db.execute(
            """
            SELECT
                COALESCE(status, 'unknown') AS status,
                COALESCE(action_status, 'new') AS action_status,
                COUNT(*) AS count
            FROM pings
            GROUP BY status, action_status
            ORDER BY count DESC
            LIMIT 16
            """
        )).fetchall()]
        since = (datetime.now() - timedelta(days=30)).replace(microsecond=0).isoformat()
        latency_rows = [dict(row) for row in await (await db.execute(
            "SELECT chat, date, detected_at, is_win, edited_at, win_detected_at "
            "FROM pings WHERE detected_at >= ?",
            (since,),
        )).fetchall()]
    sources = await get_source_scores(limit=8)
    channels_by_account = channel_account_stats()
    return {
        "latency": build_latency(latency_rows),
        "heatmap": heatmap,
        "senders": senders,
        "chats": chats,
        "priorities": priorities,
        "daily_quality": daily_quality,
        "top_mentions": top_mentions,
        "status_flow": status_flow,
        "sources": sources,
        "channels_by_account": channels_by_account,
        "channel_memberships_total": sum(int(row.get("channels") or 0) for row in channels_by_account),
    }


async def build_analytics() -> dict[str, Any]:
    """Dashboard/bot analytics, memoised for ``ANALYTICS_TTL_SECONDS``."""
    return await _cached("analytics", _build_analytics_uncached)


async def build_detailed_analytics() -> dict[str, Any]:
    """Heavy per-dimension report, memoised for ``ANALYTICS_TTL_SECONDS``."""
    return await _cached("detailed", _build_detailed_analytics_uncached)
