"""Analytics and dashboard summary endpoints (with short-lived cache)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends

import database
from database import get_giveaway_board, get_recent_problem_events, get_source_scores, get_task_overview
from pulse_desk.analytics import build_analytics, channel_account_stats
from pulse_desk.app_ctx import get_current_role
from pulse_desk.dashboard import build_dashboard_summary
from pulse_desk.live import register_dashboard_invalidator

from .system import status

router = APIRouter()


@router.get("/api/analytics")
async def get_analytics(role: str = Depends(get_current_role)):
    return await build_analytics()


_dashboard_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_dashboard_cache_ttl_seconds: float = 5.0
_dashboard_cache_lock = asyncio.Lock()


def invalidate_dashboard_cache() -> None:
    _dashboard_cache.clear()


# Wire the live-event hook so any published event clears the cache.
register_dashboard_invalidator(invalidate_dashboard_cache)


@router.get("/api/dashboard/summary")
async def get_dashboard_summary(role: str = Depends(get_current_role)):
    cache_key = role
    now = time.monotonic()
    cached = _dashboard_cache.get(cache_key)
    if cached and now - cached[0] < _dashboard_cache_ttl_seconds:
        return cached[1]
    async with _dashboard_cache_lock:
        cached = _dashboard_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _dashboard_cache_ttl_seconds:
            return cached[1]
        status_payload = await status(role)
        analytics = await build_analytics()
        tasks = await get_task_overview(limit=120)
        giveaway_board = await get_giveaway_board(limit=120)
        problem_events = await get_recent_problem_events(limit=5) if role == "admin" else []
        result = build_dashboard_summary(
            status=status_payload,
            analytics=analytics,
            tasks=tasks,
            giveaway_board=giveaway_board,
            problem_events=problem_events,
        )
        _dashboard_cache[cache_key] = (time.monotonic(), result)
        return result


@router.get("/api/analytics/detailed")
async def get_detailed_analytics(role: str = Depends(get_current_role)):
    async with aiosqlite.connect(database.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        conv = await (await db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(auto_joined), 0) AS joined FROM pings WHERE is_giveaway = 1"
        )).fetchone()
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
        deadlines = [dict(row) for row in await (await db.execute(
            """
            SELECT
                COALESCE(deadline_source, '') AS deadline_source,
                COUNT(*) AS count
            FROM pings
            WHERE is_giveaway = 1
            GROUP BY deadline_source
            """
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
        sources = await get_source_scores(limit=8)
        channels_by_account = channel_account_stats()
        return {
            "conversion": dict(conv),
            "heatmap": heatmap,
            "senders": senders,
            "chats": chats,
            "priorities": priorities,
            "deadlines": deadlines,
            "daily_quality": daily_quality,
            "top_mentions": top_mentions,
            "status_flow": status_flow,
            "sources": sources,
            "channels_by_account": channels_by_account,
            "channel_memberships_total": sum(int(row.get("channels") or 0) for row in channels_by_account),
        }


@router.get("/api/stats/detailed")
async def get_stats_detailed_alias(role: str = Depends(get_current_role)):
    return await get_detailed_analytics()
