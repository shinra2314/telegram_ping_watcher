"""Long-running background loops: market, reminders, digest, scores, auto-scan."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
from telethon import Button

from . import watch_settings as ws
from .app_ctx import (
    ADMIN_ID,
    MARKET_RETENTION_DAYS,
    PINGS_RETENTION_DAYS,
    STARTUP_SCAN_WAIT_SECONDS,
    VACUUM_INTERVAL_HOURS,
    logger,
    settings,
    state,
)
from .bot_notify import broadcast_member_notification, send_admin_bot_message
from .common import now_iso, record_app_event, start_supervised
from .digest import format_digest
from .live import publish_live_event
from .scan_engine import full_history_scan


async def fetch_market_data() -> None:
    from database import save_market_snapshot

    ids = "tether,the-open-network,bitcoin,ethereum,solana,binancecoin,notcoin,dogs-2"
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ids, "vs_currencies": "usd,uah", "include_24hr_change": "true"}

    while True:
        try:
            # Retry logic for transient network issues
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        resp = await http_client.get(url, params=params)
                        resp.raise_for_status()
                        data = resp.json()
                        data["fetched_at_iso"] = now_iso()
                        await save_market_snapshot(data)
                        break  # Success
                except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 5
                        logger.warning(f"Market fetch timeout (attempt {attempt+1}/3), retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:  # Rate limit
                        wait = 60
                        logger.warning(f"Market fetch rate limited, retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
        except Exception:
            logger.exception("Market data fetch failed after retries")

        await asyncio.sleep(ws.MARKET_POLL_SECONDS)


async def monitor_market_volatility() -> None:
    from database import get_market_history

    while True:
        try:
            rows = await get_market_history(limit=2)
            if len(rows) >= 2:
                current, previous = rows[0], rows[1]
                for asset in ("bitcoin", "the-open-network", "solana"):
                    curr_price = current.get(asset, {}).get("usd") or 0
                    prev_price = previous.get(asset, {}).get("usd") or 0
                    if prev_price:
                        change = abs((curr_price - prev_price) / prev_price) * 100
                        if change >= ws.MARKET_ALERT_CHANGE_PCT:
                            await send_admin_bot_message(
                                f"**Резкое движение {asset.upper()}**\nИзменение: `{change:.2f}%`\nЦена: `${curr_price:,.2f}`",
                            )
        except Exception:
            logger.exception("Market volatility monitor failed")
        await asyncio.sleep(3600)


async def reminder_loop() -> None:
    from database import get_due_reminders, mark_reminder_sent

    while True:
        try:
            for reminder in await get_due_reminders(limit=25):
                ping_id = reminder.get("ping_id")
                deadline_at = reminder.get("deadline_at")
                chat = reminder.get("chat") or "чат"
                text = (reminder.get("text") or "").strip().replace("\n", " ")
                message = (
                    f"Напоминание Pulse Desk\n\n"
                    f"Дедлайн: {deadline_at or 'не указан'}\n"
                    f"Источник: {chat}\n"
                    f"{text[:300]}"
                )
                if state.bot_client and ADMIN_ID:
                    buttons = []
                    if reminder.get("link"):
                        buttons.append([Button.url("Открыть в Telegram", reminder["link"])])
                    await send_admin_bot_message(message, buttons=buttons or None)
                    await broadcast_member_notification(message, buttons or None, notif_type="deadline")
                await mark_reminder_sent(int(reminder["id"]))
                await record_app_event("WARNING", "reminder", "Deadline reminder sent", {"ping_id": ping_id, "deadline_at": deadline_at})
                await publish_live_event("reminder", {"ping_id": ping_id, "chat": chat, "deadline_at": deadline_at})
        except Exception:
            logger.exception("Reminder loop failed")
        await asyncio.sleep(60)


async def digest_loop() -> None:
    from database import get_pings

    from .bot_prefs import seconds_until_hhmm

    while True:
        cfg = await ws.load_digest_settings()
        await asyncio.sleep(seconds_until_hhmm(datetime.now(), cfg["time"]))
        try:
            cfg = await ws.load_digest_settings()
            if not cfg["enabled"]:
                await asyncio.sleep(61)
                continue
            since = (datetime.now() - timedelta(hours=24)).isoformat()
            pings = await get_pings(limit=200, date_from=since)
            text = format_digest(pings, period_label="за последние 24 ч")
            await send_admin_bot_message(text)
            await broadcast_member_notification(text, None, notif_type="digest")
            await record_app_event("INFO", "digest", "Daily digest sent", {"pings": len(pings)})
        except Exception:
            logger.exception("Digest loop failed")
        # Guard against double-fire within the same minute.
        await asyncio.sleep(61)


async def obsidian_sync_loop() -> None:
    """Poll the Obsidian Долги note and reconcile it with the debt board."""
    from .obsidian_debts import sync_once

    interval = max(5, int(settings.obsidian_sync_poll_seconds or 30))
    while True:
        try:
            if settings.obsidian_sync_enabled and (settings.obsidian_debts_path or "").strip():
                await sync_once(state, settings)
        except Exception:
            logger.exception("Obsidian sync loop failed")
        await asyncio.sleep(interval)


async def source_score_loop() -> None:
    from database import cleanup_outbox, recalculate_source_scores

    while True:
        try:
            await recalculate_source_scores()
            await cleanup_outbox(days=2, max_events=2500)
        except Exception:
            logger.exception("Source score recalculation failed")
        await asyncio.sleep(300)


async def auto_scan_loop() -> None:
    from database import cleanup_old_data

    start_supervised("market-volatility", monitor_market_volatility, backoff_base=60.0, backoff_max=1800.0)
    if ws.STARTUP_SCAN_DELAY_SECONDS:
        await asyncio.sleep(ws.STARTUP_SCAN_DELAY_SECONDS)
    waited = 0.0
    while not state.clients and waited < STARTUP_SCAN_WAIT_SECONDS:
        await asyncio.sleep(1)
        waited += 1
    last_vacuum = datetime.now()
    while True:
        try:
            if state.clients:
                await full_history_scan()
                state.last_scan_finished_at = datetime.now()
                state.last_scan_status = "ok"
            vacuum_due = (datetime.now() - last_vacuum).total_seconds() >= VACUUM_INTERVAL_HOURS * 3600
            stats = await cleanup_old_data(
                days=MARKET_RETENTION_DAYS,
                pings_retention_days=PINGS_RETENTION_DAYS,
                vacuum=vacuum_due,
            )
            if vacuum_due:
                last_vacuum = datetime.now()
            if stats.get("pings") or stats.get("market_history") or stats.get("vacuumed"):
                await record_app_event("INFO", "maintenance", "Periodic cleanup completed", stats)
        except Exception:
            logger.exception("Automatic scan loop failed")
            state.last_scan_status = "error"
        await asyncio.sleep(ws.SCAN_INTERVAL_SECONDS)


async def startup_maintenance() -> None:
    from database import (
        backfill_deadlines_from_text,
        cleanup_outbox,
        prune_broadcast_messages,
        rebuild_search_indexes,
        reconcile_giveaway_flags,
        reconcile_giveaway_outcomes,
        reconcile_win_flags,
    )

    try:
        search_reindex = await rebuild_search_indexes()
        await record_app_event("INFO", "search", "Search indexes rebuilt on startup", search_reindex)
        giveaway_reconcile = await reconcile_giveaway_flags(state.giveaway_keywords)
        if giveaway_reconcile["enabled"] or giveaway_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored giveaways with channel keyword rule", giveaway_reconcile)
        win_reconcile = await reconcile_win_flags(state.win_keywords)
        if win_reconcile["enabled"] or win_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored win/result flags", win_reconcile)
        outcome_reconcile = await reconcile_giveaway_outcomes()
        if outcome_reconcile["marked"]:
            await record_app_event("INFO", "giveaway", "Marked giveaway result posts as prize claims", outcome_reconcile)
        await cleanup_outbox(days=2, max_events=2500)
        pruned_broadcasts = await prune_broadcast_messages(days=7)
        if pruned_broadcasts:
            await record_app_event("INFO", "maintenance", "Pruned stale bot broadcast records", {"count": pruned_broadcasts})
        backfilled_deadlines = await backfill_deadlines_from_text()
        if backfilled_deadlines:
            await record_app_event("INFO", "deadline", "Backfilled deadlines from giveaway text", {"count": backfilled_deadlines})
    except Exception as exc:
        logger.exception("Startup maintenance failed")
        await record_app_event("ERROR", "app", "Startup maintenance failed", {"error": str(exc)})
