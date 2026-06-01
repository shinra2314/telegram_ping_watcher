"""Database backup endpoints — list / create / download."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from database import create_db_backup, list_db_backups
from pulse_desk.app_ctx import logger, require_admin


router = APIRouter()


async def _record_event(level: str, source: str, message: str, context: dict | None = None) -> None:
    # Local re-import to avoid pulling main.py
    from database import record_event
    try:
        await record_event(level, source, message, context)
    except Exception:
        logger.debug("Could not persist app event", exc_info=True)


@router.get("/api/backups", dependencies=[Depends(require_admin)])
async def read_backups():
    return {"backups": list_db_backups()}


@router.post("/api/backups/create", dependencies=[Depends(require_admin)])
async def create_backup_api():
    backup = create_db_backup()
    if not backup:
        raise HTTPException(404, "Database file not found")
    await _record_event(
        "INFO",
        "backup",
        "Manual database backup created",
        {"name": backup["name"], "size": backup["size"]},
    )
    return {"status": "ok", "backup": backup}


@router.get("/api/backups/{name}/download", dependencies=[Depends(require_admin)])
async def download_backup(name: str):
    backups = {item["name"]: item for item in list_db_backups(limit=500)}
    backup = backups.get(name)
    if not backup:
        raise HTTPException(404, "Backup not found")
    return FileResponse(backup["path"], filename=name, media_type="application/octet-stream")
