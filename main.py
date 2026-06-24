"""Pulse Desk application entry point.

Builds the FastAPI app, registers routers, and drives startup/shutdown.
All business logic lives in ``src/pulse_desk`` (see CLAUDE.md for the map);
HTTP endpoints live in ``routers/``.
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from database import get_setting, init_db, interrupt_stale_scan_runs, set_setting

from pulse_desk import APP_VERSION
from pulse_desk import watch_settings as ws
from pulse_desk.app_ctx import (
    ADMIN_TOKEN,
    VIEWER_TOKEN,
    logger,
    settings,
    state,
)
from pulse_desk.bot_service import init_bot
from pulse_desk.common import record_app_event, start_background_task, start_supervised
from pulse_desk.loops import (
    access_scheduler_loop,
    auto_scan_loop,
    digest_loop,
    fetch_market_data,
    obsidian_sync_loop,
    reminder_loop,
    source_score_loop,
    startup_maintenance,
    watchdog_loop,
)
from pulse_desk.process_supervisor import get_supervisor
from pulse_desk.push import generate_vapid_keys
from pulse_desk.security import is_weak_token, mask_secret
from pulse_desk.service_registry import load_services
from pulse_desk.telegram_accounts import start_client

state.session_names = settings.discover_sessions()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # VAPID key lifecycle for web push
    try:
        _vapid_pem = await get_setting("vapid_private_pem", "")
        if not _vapid_pem:
            _vapid_keys = generate_vapid_keys()
            await set_setting("vapid_private_pem", _vapid_keys["private_key"])
            _vapid_pem = _vapid_keys["private_key"]
            logger.info("Generated new VAPID key pair for web push")
        state.vapid_private_pem = _vapid_pem
    except Exception:
        logger.warning("Could not initialise VAPID keys — web push will be disabled", exc_info=True)
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
    try:
        from pulse_desk.obsidian_debts import apply_prefs as _apply_obsidian_prefs, load_prefs as _load_obsidian_prefs

        _apply_obsidian_prefs(settings, await _load_obsidian_prefs())
    except Exception:
        logger.warning("Could not apply Obsidian sync prefs", exc_info=True)
    if is_weak_token(ADMIN_TOKEN):
        logger.warning("ADMIN_TOKEN looks weak: %s", mask_secret(ADMIN_TOKEN))
        await record_app_event("WARNING", "auth", "ADMIN_TOKEN looks weak", {"token": mask_secret(ADMIN_TOKEN)})
    if is_weak_token(VIEWER_TOKEN):
        logger.warning("VIEWER_TOKEN looks weak: %s", mask_secret(VIEWER_TOKEN))
        await record_app_event("WARNING", "auth", "VIEWER_TOKEN looks weak", {"token": mask_secret(VIEWER_TOKEN)})
    await init_bot()
    start_supervised("market-fetch", fetch_market_data, backoff_base=30.0, backoff_max=1800.0)
    start_supervised("reminders", reminder_loop, backoff_base=10.0, backoff_max=600.0)
    start_supervised("daily-digest", digest_loop, backoff_base=60.0, backoff_max=3600.0)
    start_supervised("source-scores", source_score_loop, backoff_base=30.0, backoff_max=600.0)
    start_supervised("obsidian-sync", obsidian_sync_loop, backoff_base=30.0, backoff_max=600.0)
    start_supervised("access-scheduler", access_scheduler_loop, backoff_base=15.0, backoff_max=600.0)
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


app = FastAPI(title="Pulse Desk Multi-Account", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

from routers import access as access_router  # noqa: E402
from routers import analytics as analytics_router  # noqa: E402
from routers import auth as auth_router  # noqa: E402
from routers import backups as backups_router  # noqa: E402
from routers import boards as boards_router  # noqa: E402
from routers import bot_access as bot_access_router  # noqa: E402
from routers import export as export_router  # noqa: E402
from routers import giveaways as giveaways_router  # noqa: E402
from routers import launcher as launcher_router  # noqa: E402
from routers import live as live_router  # noqa: E402
from routers import lookups as lookups_router  # noqa: E402
from routers import market as market_router  # noqa: E402
from routers import obsidian as obsidian_router  # noqa: E402
from routers import pings as pings_router  # noqa: E402
from routers import push as push_router  # noqa: E402
from routers import scan as scan_router  # noqa: E402
from routers import settings as settings_router  # noqa: E402
from routers import system as system_router  # noqa: E402

app.include_router(access_router.router)
app.include_router(analytics_router.router)
app.include_router(auth_router.router)
app.include_router(backups_router.router)
app.include_router(boards_router.router)
app.include_router(bot_access_router.router)
app.include_router(export_router.router)
app.include_router(giveaways_router.router)
app.include_router(launcher_router.router)
app.include_router(live_router.router)
app.include_router(lookups_router.router)
app.include_router(market_router.router)
app.include_router(obsidian_router.router)
app.include_router(pings_router.router)
app.include_router(push_router.router)
app.include_router(scan_router.router)
app.include_router(settings_router.router)
app.include_router(system_router.router)


if __name__ == "__main__":
    import uvicorn

    host = settings.host
    port = settings.port
    if host not in {"127.0.0.1", "localhost"} and not ADMIN_TOKEN:
        logger.warning("Server is exposed on %s without ADMIN_TOKEN/WEB_AUTH_TOKEN.", host)
    uvicorn.run(app, host=host, port=port, access_log=False)
