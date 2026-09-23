"""Pulse Desk application entry point.

Runs the whole app: the Telegram watcher, the bot and every background job.
The web UI was removed on 2026-09-12 — everything it showed now lives in the
bot — so this FastAPI app serves exactly one endpoint, ``/api/health``. The
Telegram Mini App («🛰 Панель») is a separate ASGI app on its own port
(``miniapp_server.py``), because its port is the one published to the internet.

uvicorn stays the process host on purpose: the logon task
(``scripts/start_dashboard.ps1``) waits on that endpoint before declaring the
app up, and the external watchdog (``scripts/pulse_watchdog.ps1``) polls it
every five minutes. Replacing the entry point would mean rewriting both
scheduled tasks for no gain.
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from database import init_db, interrupt_stale_scan_runs

from pulse_desk import APP_VERSION
from pulse_desk import check_claimer, ignored_chats
from pulse_desk import watch_settings as ws
from pulse_desk.app_ctx import logger, settings, state
from pulse_desk.bot_service import bot_start_needs_retry, init_bot, retry_bot_start
from pulse_desk.common import record_app_event, start_background_task, start_supervised
from pulse_desk.loops import (
    access_scheduler_loop,
    account_health_loop,
    auto_scan_loop,
    bot_janitor_loop,
    broadcast_approval_loop,
    detect_downtime,
    digest_loop,
    fetch_market_data,
    maintenance_loop,
    obsidian_sync_loop,
    pending_send_loop,
    report_loop,
    roulette_loop,
    salary_sync_loop,
    source_score_loop,
    startup_maintenance,
    watchdog_loop,
)
from pulse_desk.miniapp_server import serve_miniapp
from pulse_desk.ping_notify import notify_retry_loop
from pulse_desk.process_supervisor import get_supervisor
from pulse_desk.service_registry import load_services
from pulse_desk.telegram_accounts import start_client
from pulse_desk.tunnel import tunnel_loop

state.session_names = settings.discover_sessions()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    interrupted_scans = await interrupt_stale_scan_runs()
    if interrupted_scans:
        await record_app_event(
            "WARNING",
            "scan",
            "Interrupted stale scan runs after application restart",
            {"count": len(interrupted_scans), "ids": [row["id"] for row in interrupted_scans[:10]]},
        )
    ws.apply_tracking_settings(await ws.load_tracking_settings())
    ws.apply_keyword_settings(await ws.load_keyword_settings())
    ws.apply_runtime_settings(await ws.load_runtime_settings())
    ignored_chats.apply(await ignored_chats.load())
    await check_claimer.load()
    try:
        from pulse_desk.obsidian_debts import apply_prefs as _apply_obsidian_prefs, load_prefs as _load_obsidian_prefs

        _apply_obsidian_prefs(settings, await _load_obsidian_prefs())
    except Exception:
        logger.warning("Could not apply Obsidian sync prefs", exc_info=True)
    # Before `maintenance` starts: it overwrites the previous process's last
    # liveness stamp, which is what tells a restart from the nightly shutdown.
    await detect_downtime()
    await init_bot()
    if bot_start_needs_retry():
        # Network not up yet after boot: keep trying instead of running the
        # whole day with a dead bot. Pings found meanwhile owe their cards.
        start_background_task("bot-start-retry", retry_bot_start())
    start_supervised("maintenance", maintenance_loop, backoff_base=60.0, backoff_max=1800.0)
    start_supervised("bot-janitor", bot_janitor_loop, backoff_base=15.0, backoff_max=600.0)
    start_supervised("account-health", account_health_loop, backoff_base=30.0, backoff_max=900.0)
    start_supervised("weekly-report", report_loop, backoff_base=60.0, backoff_max=1800.0)
    start_supervised("market-fetch", fetch_market_data, backoff_base=30.0, backoff_max=1800.0)
    start_supervised("broadcast-approval", broadcast_approval_loop, backoff_base=10.0, backoff_max=600.0)
    start_supervised("pending-sends", pending_send_loop, backoff_base=10.0, backoff_max=600.0)
    start_supervised("notify-retry", notify_retry_loop, backoff_base=10.0, backoff_max=300.0)
    start_supervised("daily-digest", digest_loop, backoff_base=60.0, backoff_max=3600.0)
    start_supervised("roulette-reminder", roulette_loop, backoff_base=60.0, backoff_max=3600.0)
    start_supervised("source-scores", source_score_loop, backoff_base=30.0, backoff_max=600.0)
    start_supervised("obsidian-sync", obsidian_sync_loop, backoff_base=30.0, backoff_max=600.0)
    # Зарплаты живут в книге Excel: без пути к ней джобу нечего опрашивать.
    if (settings.salary_xlsx_path or "").strip():
        start_supervised("salary-sync", salary_sync_loop, backoff_base=30.0, backoff_max=600.0)
    start_supervised("access-scheduler", access_scheduler_loop, backoff_base=15.0, backoff_max=600.0)
    # Панель и её туннель — только по флагу: start_supervised перезапускает
    # вернувшийся джоб, так что выключенный туннель крутился бы вхолостую.
    if settings.miniapp_enabled:
        start_supervised("miniapp-server", serve_miniapp, backoff_base=5.0, backoff_max=120.0)
        start_supervised("tunnel", tunnel_loop, backoff_base=10.0, backoff_max=600.0)
    start_background_task("startup-maintenance", startup_maintenance())
    logger.info("Starting monitoring: %s sessions found", len(state.session_names))
    await record_app_event("INFO", "app", "Application started", {"sessions": len(state.session_names), "version": APP_VERSION})
    for name in state.session_names:
        start_background_task(f"telegram-start:{name}", start_client(name))
    start_supervised("auto-scan", auto_scan_loop, backoff_base=30.0, backoff_max=900.0)
    if settings.watchdog_enabled:
        start_supervised("watchdog", watchdog_loop, backoff_base=15.0, backoff_max=300.0)
    # Launcher: register external services (Discord bot etc.) and autostart any
    # marked autostart=true, so one Pulse Desk process brings up the whole stack.
    try:
        supervisor = get_supervisor()
        for svc in load_services():
            supervisor.register(svc)
        await supervisor.autostart_enabled()
    except Exception:
        logger.warning("Launcher service registration failed", exc_info=True)
    yield
    state.shutting_down = True
    with suppress(Exception):
        await get_supervisor().shutdown()
    for task in list(state.background_tasks.values()):
        if not task.done():
            task.cancel()
    if state.background_tasks:
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*list(state.background_tasks.values()), return_exceptions=True)
    for client in list(state.clients):
        with suppress(Exception):
            await client.disconnect()
    if state.bot_client:
        with suppress(Exception):
            await state.bot_client.disconnect()


app = FastAPI(title="Pulse Desk", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

from routers import health as health_router  # noqa: E402

app.include_router(health_router.router)


if __name__ == "__main__":
    import uvicorn

    # Только health: слушать что-то кроме localhost больше незачем.
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)
