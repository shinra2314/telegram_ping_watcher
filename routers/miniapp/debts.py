"""Долги: выигранное, но не забранное. Только владелец.

Сегменты, оценка и «горячий» — те же функции, что у раздела бота. Массовое
«забрал» здесь — список id в теле запроса, без серверных отметок.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from database import get_debt_board
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections.debts import (
    BOARD_LIMIT, SEGMENTS, hottest, score_of, segment_rows, total_value,
)
from pulse_desk.ping_actions import apply_ping_meta

from .common import Caller, admin_caller, fresh_admin

router = APIRouter()


def debt_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "score": score_of(row),
        "value": row.get("estimated_value"),
        "status": row.get("status"),
        "text": str(row.get("text") or "")[:400],
        "link": row.get("link"),
    }


@router.get("/api/app/debts")
async def debts(caller: Caller = Depends(admin_caller),
                seg: str = Query("a", pattern="^[achn]$")) -> dict:
    board = await get_debt_board(state.ping_usernames, limit=BOARD_LIMIT)
    rows = segment_rows(board.get("rows") or [], seg)
    top = hottest(rows)
    return {
        "segment": seg,
        "segments": [{"code": code, "label": label} for code, label in SEGMENTS],
        "stats": board.get("stats") or {},
        "count": len(rows),
        "value": total_value(rows),
        "hottest": top["id"] if top else None,
        "items": [debt_row(r) for r in rows],
    }


class ClaimBody(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=BOARD_LIMIT)
    status: str = Field("claimed", pattern="^(claimed|scam)$")


@router.post("/api/app/debts/claim")
async def claim(body: ClaimBody, caller: Caller = Depends(fresh_admin)) -> dict:
    ids = list(dict.fromkeys(body.ids))
    for ping_id in ids:
        await apply_ping_meta(ping_id, giveaway_status=body.status, action_status=body.status)
    return {"ok": True, "count": len(ids), "status": body.status}
