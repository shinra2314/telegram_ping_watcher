"""«Система»: здоровье приложения и четыре кнопки обслуживания. Только владелец.

Цифры — ровно те, что отдаёт ``/api/health`` (её же читает watchdog) и
🩺 Диагностика бота: бот на связи или нет, какие джобы молчат, сколько
карточек не доставлено, база, диск, уборка. Сам ``health()`` вызывается как
функция — второго способа посчитать «всё ли в порядке» быть не должно.

Действия — те же, что у кнопок бота: скан (``scan.start_if_idle``), уборка
(``run_maintenance_once``, в фоне — VACUUM дольше жизни HTTP-запроса),
бэкап (``create_db_backup`` в потоке) и досылка должных карточек.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from database import create_db_backup, get_recent_problem_events, list_db_backups, record_event
from pulse_desk.app_ctx import logger, state
from pulse_desk.bot.sections.scan import start_if_idle
from pulse_desk.common import start_background_task
from routers.health import health

from .common import Caller, admin_caller, fresh_admin

router = APIRouter()

EVENTS_SHOWN = 40
MAINTENANCE_JOB = "panel-maintenance"


def _event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "at": row.get("created_at") or row.get("ts"),
        "level": row.get("level"),
        "source": row.get("source"),
        "message": str(row.get("message") or "")[:300],
    }


@router.get("/api/app/system")
async def system(caller: Caller = Depends(admin_caller)) -> dict:
    report, events, backups = await asyncio.gather(
        health(),
        get_recent_problem_events(limit=EVENTS_SHOWN),
        asyncio.to_thread(list_db_backups, 1),
    )
    newest = backups[0] if backups else None
    running_maintenance = MAINTENANCE_JOB in state.background_tasks and not state.background_tasks[MAINTENANCE_JOB].done()
    return {
        "status": report.get("status"),
        "version": report.get("version"),
        "uptime_seconds": report.get("uptime_seconds"),
        "bot": {
            "configured": report.get("bot_configured"),
            "connected": report.get("bot_connected"),
            "offline_seconds": report.get("bot_offline_seconds"),
            "last_update_seconds": report.get("bot_last_update_seconds"),
            "handler_errors": report.get("bot_handler_errors"),
            "handler_slow": report.get("bot_handler_slow"),
        },
        "accounts": {"online": report.get("accounts_online"), "configured": report.get("accounts_configured")},
        "owed_ping_cards": report.get("owed_ping_cards"),
        "pending_sends_backlog": report.get("pending_sends_backlog"),
        "unhealthy_jobs": report.get("unhealthy_jobs") or [],
        "missing_jobs": report.get("missing_background_tasks") or [],
        "jobs": len(report.get("background_tasks") or []),
        "db_size_mb": round((report.get("db_size_bytes") or 0) / 1024 / 1024, 1),
        "db_freelist_pct": report.get("db_freelist_pct"),
        "disk_free_mb": report.get("disk_free_mb"),
        "backups_mb": round((report.get("backups_bytes") or 0) / 1024 / 1024, 1) if report.get("backups_bytes") else None,
        "last_backup": {"name": newest["name"], "at": newest["created_at"], "size": newest["size"]} if newest else None,
        "last_maintenance_at": report.get("last_maintenance_at"),
        "maintenance_running": running_maintenance,
        "scan": {
            "running": bool(state.scan_lock.locked()),
            "last_finished_at": report.get("last_scan_finished_at"),
            "last_status": report.get("last_scan_status"),
            "progress": {k: state.scan_status.get(k) for k in ("processed_accounts", "total_accounts", "current_account", "found")},
            "error": state.scan_status.get("last_error"),
        },
        "events": [_event(e) for e in events],
    }


@router.post("/api/app/system/scan")
async def scan_now(caller: Caller = Depends(fresh_admin)) -> dict:
    return {"ok": True, "started": start_if_idle()}


@router.post("/api/app/system/maintenance")
async def maintenance_now(caller: Caller = Depends(fresh_admin)) -> dict:
    from pulse_desk.loops import run_maintenance_once

    task = state.background_tasks.get(MAINTENANCE_JOB)
    if task is not None and not task.done():
        return {"ok": True, "started": False}
    start_background_task(MAINTENANCE_JOB, run_maintenance_once(force_daily=True))
    return {"ok": True, "started": True}


@router.post("/api/app/system/backup")
async def backup_now(caller: Caller = Depends(fresh_admin)) -> dict:
    # Snapshot + zip of the whole base: seconds, so never on the event loop.
    created = await asyncio.to_thread(create_db_backup)
    if not created:
        raise HTTPException(status_code=409, detail="Базы нет — копировать нечего")
    await record_event("INFO", "backup", "Database backup created from the panel", {"name": created.get("name")})
    return {"ok": True, "name": created.get("name"), "size": created.get("size")}


@router.post("/api/app/system/resend")
async def resend_owed(caller: Caller = Depends(fresh_admin)) -> dict:
    """One pass of notify-retry now instead of within 30 s."""
    from pulse_desk.ping_notify import retry_owed_notifications

    try:
        stats = await retry_owed_notifications()
    except Exception as exc:
        logger.warning("Panel resend of owed cards failed: %s", exc)
        raise HTTPException(status_code=502, detail="Бот не на связи — карточки уйдут, когда он вернётся") from exc
    return {"ok": True, **stats}
