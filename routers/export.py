"""Export endpoints: stream CSV / JSON / Telegram digest for pings.

Moved out of main.py as the first slice of the routers/ refactor.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from database import get_pings
from pulse_desk.app_ctx import get_current_role, logger, require_admin, settings, state
from pulse_desk.digest import format_digest


router = APIRouter()


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


EXPORT_CSV_COLUMNS = [
    ("detected_at", "Обнаружено"),
    ("date", "Дата сообщения"),
    ("chat", "Чат"),
    ("chat_type", "Тип"),
    ("sender", "Отправитель"),
    ("mentions", "Упоминания"),
    ("status", "Статус"),
    ("giveaway_status", "Статус розыгрыша"),
    ("action_status", "Статус действия"),
    ("deadline_at", "Дедлайн"),
    ("deadline_source", "Источник дедлайна"),
    ("reminder_at", "Напоминание"),
    ("priority_score", "Приоритет"),
    ("note", "Заметка"),
    ("text", "Текст"),
    ("link", "Ссылка"),
]


async def _iter_pings_paginated(page_size: int = 1000, **filters):
    offset = 0
    while True:
        page = await get_pings(limit=page_size, offset=offset, **filters)
        if not page:
            return
        for row in page:
            yield row
        if len(page) < page_size:
            return
        offset += page_size


@router.get("/api/export-csv", dependencies=[Depends(require_admin)])
async def export_csv(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    favorite: Optional[bool] = None,
    search: Optional[str] = None,
):
    async def generate():
        buffer = StringIO()
        buffer.write("﻿")
        writer = csv.writer(buffer)
        writer.writerow([title for _, title in EXPORT_CSV_COLUMNS])
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        yield chunk
        async for row in _iter_pings_paginated(
            status=status_filter, favorite=favorite, search=search
        ):
            writer.writerow([row.get(key) for key, _ in EXPORT_CSV_COLUMNS])
            chunk = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=pulse_pings_export.csv"},
    )


@router.get("/api/export-json", dependencies=[Depends(require_admin)])
async def export_json(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    favorite: Optional[bool] = None,
    search: Optional[str] = None,
    mention: Optional[str] = None,
    chat_type: str = "all",
):
    filters = {
        "status": status_filter,
        "favorite": favorite,
        "search": search,
        "mention": mention,
        "chat_type": chat_type,
    }
    exported_at = _now_iso()

    async def generate():
        yield '{"exported_at":' + json.dumps(exported_at)
        yield ',"filters":' + json.dumps(filters, ensure_ascii=False)
        yield ',"rows":['
        first = True
        async for row in _iter_pings_paginated(
            status=status_filter,
            favorite=favorite,
            search=search,
            mention=mention,
            chat_type=chat_type,
        ):
            if first:
                first = False
            else:
                yield ","
            yield json.dumps(row, ensure_ascii=False, default=str)
        yield "]}"

    return StreamingResponse(
        generate(),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=pulse_pings_export.json"},
    )


@router.post("/api/export/telegram-digest")
async def export_telegram_digest(
    hours: int = Query(24, ge=1, le=168),
    _: str = Depends(require_admin),
):
    if not state.bot_client or not settings.admin_id:
        raise HTTPException(
            status_code=503,
            detail="Бот не настроен: нужны TELEGRAM_BOT_TOKEN и ADMIN_ID в .env",
        )
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    pings = await get_pings(limit=200, date_from=since)
    text = format_digest(pings, period_label=f"за {hours} ч")
    try:
        await state.bot_client.send_message(settings.admin_id, text, parse_mode="md")
    except Exception as exc:
        logger.exception("Failed to send digest")
        raise HTTPException(status_code=500, detail=f"Ошибка отправки: {exc}") from exc
    return {"ok": True, "pings_count": len(pings)}
