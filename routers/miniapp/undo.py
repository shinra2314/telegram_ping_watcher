"""«Вернуть»: откат последней смены статуса, та же ``bot/undo``, что у бота.

Действие со статусом отдаёт в ответе токен; этот запрос по токену кладёт
строки обратно. Снимок живёт на сервере 30 секунд и один на человека — новое
действие вытесняет старое, как «↩️ Отменить» в чате. Значения статусов с
телефона не принимаются: откат ставит только то, что сервер сам снял.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from pulse_desk.bot.undo import UNDO_TTL_SECONDS, undo

from .common import Caller, fresh_admin

router = APIRouter()


class UndoBody(BaseModel):
    token: str = Field(..., min_length=1, max_length=16)


@router.post("/api/app/undo")
async def undo_last(body: UndoBody, caller: Caller = Depends(fresh_admin)) -> dict:
    result = await undo(caller.tg_id, body.token)
    if result is None:
        raise HTTPException(status_code=410,
                            detail=f"Вернуть уже нельзя — прошло больше {UNDO_TTL_SECONDS} с или было новое действие")
    restored, _back = result
    return {"ok": True, "restored": restored}
