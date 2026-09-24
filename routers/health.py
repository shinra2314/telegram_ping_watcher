"""Единственный HTTP-эндпоинт, переживший переезд в бота: `/api/health`.

Веб-приложение удалено (2026-09-12): всё, что оно показывало, живёт в боте.
HTTP остался ровно ради этой ручки — её опрашивают внешний сторож
``scripts/pulse_watchdog.ps1`` и гейт запуска ``scripts/start_dashboard.ps1``,
а сам uvicorn остаётся хостом процесса, чтобы не переписывать обе задачи
планировщика и точку входа.

Форма ответа не менялась вместе с переездом: сторож читает ``status``, а
человек — остальное.
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime

from fastapi import APIRouter

import database
from pulse_desk import APP_VERSION
from pulse_desk import watch_settings as ws
from pulse_desk import check_claimer
from pulse_desk.app_ctx import handler_errors, loop_watch, settings, state
from pulse_desk.common import now_iso
from pulse_desk.jobs import expected_jobs, feature_job_polls, runtime_health
from pulse_desk.watchdog import classify_job, default_thresholds

router = APIRouter()

# Бот обычно переподключается за пару шагов backoff; дольше — это уже простой.
BOT_OFFLINE_DEGRADED_SECONDS = 600
# Сколько ручка ждёт каждую SQL-цифру. Сторож даёт на ответ 5 с; 23.09 шторм
# блокировок растянул две выборки дольше — и сторож перезапустил живой процесс.
DB_FIELD_TIMEOUT_SECONDS = 2.0


async def _db_field(query) -> tuple[object, bool]:
    """(значение, успело ли) — занятая база даёт None, а не зависший ответ."""
    try:
        return await asyncio.wait_for(query(), DB_FIELD_TIMEOUT_SECONDS), True
    except asyncio.TimeoutError:
        return None, False
    except Exception:
        return None, True


@router.get("/api/health")
async def health():
    accounts_configured = len(state.session_names) or len(state.accounts_state)
    accounts_online = sum(
        1 for acc in list(state.accounts_state.values()) if acc.get("status") == "online"
    )
    bot_configured = state.bot_client is not None
    info = runtime_health(
        state,
        accounts_online=accounts_online,
        accounts_configured=accounts_configured,
        expected_tasks=expected_jobs(bot_configured=bot_configured),
    )
    try:
        db_size_bytes = database.DB_PATH.stat().st_size if database.DB_PATH.exists() else 0
    except Exception:
        db_size_bytes = 0
    try:
        disk_free_mb = shutil.disk_usage(settings.base_dir).free // (1024 * 1024)
    except OSError:
        disk_free_mb = None
    # Engine health: which critical jobs have gone silent or died (same logic the
    # watchdog uses to page the admin).
    thresholds = default_thresholds(
        scan_interval_seconds=ws.SCAN_INTERVAL_SECONDS,
        market_poll_seconds=ws.MARKET_POLL_SECONDS,
        bot_configured=bot_configured,
        enabled_features=feature_job_polls(settings),
    )
    now = datetime.now()
    unhealthy_jobs = []
    for name, threshold in thresholds.items():
        task = state.background_tasks.get(name)
        running = bool(task) and not task.done()
        ref = state.job_last_ok_at.get(name) or state.job_started_at.get(name)
        age = int((now - ref).total_seconds()) if ref else None
        h = classify_job(name, running=running, age_seconds=age, threshold_seconds=threshold)
        if not h.healthy:
            unhealthy_jobs.append({"job": name, "reason": h.reason, "age_seconds": h.age_seconds})
    # Bot transport: while the client is disconnected it receives no updates, so
    # every command and button press queues up on Telegram's side. The
    # bot-connection supervisor reconnects it; only a *prolonged* outage counts
    # as degraded, so a routine network blip doesn't flap the status.
    bot_connected = bool(bot_configured and state.bot_client.is_connected())
    bot_offline_seconds = (
        int((now - state.bot_offline_since).total_seconds()) if state.bot_offline_since else 0
    )
    # `bot_connected` only proves the socket is up. A client that is connected
    # but no longer receiving updates answers nothing, and used to report
    # healthy — this is the "all bot messages are delayed on random days"
    # symptom seen from the outside.
    bot_last_update_seconds = (
        int((now - state.bot_last_update_at).total_seconds()) if state.bot_last_update_at else None
    )
    pending_backlog, backlog_in_time = await _db_field(database.pending_sends_backlog)
    # Owner's ping cards not delivered yet (ping_notify). A number that stays
    # up means mentions are being found but not reaching the owner.
    owed_ping_cards, owed_in_time = await _db_field(database.count_owed_ping_notifications)
    bot_ok = (
        not bot_configured
        or (
            (bot_connected or bot_offline_seconds <= BOT_OFFLINE_DEGRADED_SECONDS)
            and not state.bot_init_failed
        )
    )
    healthy = (
        not info.get("missing_background_tasks")
        and info.get("accounts_ok")
        and not unhealthy_jobs
        and bot_ok
    )
    info.update(
        {
            "status": "ok" if healthy else "degraded",
            "bot_configured": bot_configured,
            "bot_connected": bot_connected,
            "bot_offline_seconds": bot_offline_seconds,
            "bot_init_failed": state.bot_init_failed,
            "bot_last_update_seconds": bot_last_update_seconds,
            "bot_handler_calls": state.bot_handler_calls,
            "bot_handler_errors": state.bot_handler_errors,
            "bot_handler_slow": state.bot_handler_slow,
            "account_handler_errors": handler_errors.total(),
            "pending_sends_backlog": pending_backlog,
            "owed_ping_cards": owed_ping_cards,
            # The two numbers above did not come back in time: the database is
            # busy, the process is not dead — the watchdog must not restart it.
            "db_slow": not (backlog_in_time and owed_in_time),
            **loop_watch.stats(),
            # Presses, hand-offs, timeouts, outcomes, accounts sitting out a FloodWait.
            "checks": check_claimer.stats_snapshot(),
            "unhealthy_jobs": unhealthy_jobs,
            "time": now_iso(),
            "version": APP_VERSION,
            "db_size_bytes": db_size_bytes,
            "disk_free_mb": disk_free_mb,
            # Filled by the hourly `maintenance` pass; None until its first run.
            "db_freelist_pct": state.maintenance_stats.get("freelist_pct"),
            "backups_bytes": state.maintenance_stats.get("backups_bytes"),
            "last_maintenance_at": state.maintenance_stats.get("finished_at"),
            "last_scan_finished_at": state.last_scan_finished_at.isoformat(timespec="seconds")
            if state.last_scan_finished_at
            else None,
            "last_scan_status": state.last_scan_status,
            "scan_running": bool(state.scan_status.get("running")),
            "shutting_down": state.shutting_down,
        }
    )
    return info
