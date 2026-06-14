"""Scan control endpoints."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from database import update_scan_run
from pulse_desk.app_ctx import get_current_role, require_admin, state
from pulse_desk.common import record_app_event
from pulse_desk.scan import normalize_scan_history_limit
from pulse_desk.scan_engine import backfill_name_mention_scan, full_history_scan

router = APIRouter()


@router.post("/api/scan-history", dependencies=[Depends(require_admin)])
async def start_scan(background_tasks: BackgroundTasks):
    if not state.clients:
        return {"status": "error", "message": "Нет подключенных аккаунтов"}
    if state.scan_lock.locked():
        return {"status": "running", "message": "Сканирование уже идет"}
    background_tasks.add_task(full_history_scan)
    return {"status": "ok", "message": "Сканирование запущено"}


@router.post("/api/backfill-mentions", dependencies=[Depends(require_admin)])
async def start_mention_backfill(background_tasks: BackgroundTasks, limit: int = 1000):
    if not state.clients:
        return {"status": "error", "message": "Нет подключенных аккаунтов"}
    if state.scan_lock.locked():
        return {"status": "running", "message": "Сканирование уже идет"}
    per_channel_limit = normalize_scan_history_limit(limit)
    background_tasks.add_task(backfill_name_mention_scan, per_channel_limit)
    scope = "вся история" if per_channel_limit <= 0 else f"до {per_channel_limit} сообщений на канал"
    return {"status": "ok", "message": f"Backfill упоминаний по имени запущен ({scope})"}


@router.post("/api/scan-history/cancel", dependencies=[Depends(require_admin)])
async def cancel_scan():
    if not state.scan_lock.locked():
        return {"status": "idle", "message": "Сканирование не идет"}
    state.scan_status["cancel_requested"] = True
    state.scan_cancel_event.set()
    if state.scan_status.get("scan_run_id"):
        await update_scan_run(int(state.scan_status["scan_run_id"]), cancel_requested=1)
    await record_app_event("WARNING", "scan", "Scan cancellation requested", {"scan_run_id": state.scan_status.get("scan_run_id")})
    return {"status": "ok", "message": "Остановка сканирования запрошена"}


@router.get("/api/scan-status")
async def get_scan_status(role: str = Depends(get_current_role)):
    return dict(state.scan_status)
