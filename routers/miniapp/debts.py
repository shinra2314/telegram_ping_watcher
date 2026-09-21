"""Долги: выигранное, но не забранное. Только владелец.

Сегменты, оценка и «горячий» — те же функции, что у раздела бота. Массовое
«забрал» здесь — список id в теле запроса, без серверных отметок; перед ним
снимается снимок, и ответ несёт токен «Вернуть» (``/api/app/undo``).

Сумма строки — оценка разбора, а без неё приз из текста поста по свежему
снимку курсов (``prize_value``), как «💵 Незабрано» в боте. Разрез по
аккаунтам — ``value_by_account``, та же сумма, что у кнопки ``db:v``.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from database import get_debt_board
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections.debts import BOARD_LIMIT, SEGMENTS, hottest, score_of, segment_rows
from pulse_desk.bot.sections.market import latest_snapshot
from pulse_desk.bot.undo import remember, snapshot
from pulse_desk.bot_permissions import account_mentioned
from pulse_desk.ping_actions import apply_ping_meta
from pulse_desk.prize_value import prize_usd, value_by_account

from .common import Caller, admin_caller, audit, fresh_admin

router = APIRouter()

SORTS = ("d", "v")


def row_value(row: dict, snap: Optional[dict[str, Any]]) -> Optional[float]:
    """Оценка разбора, иначе приз из текста; None — оценить нечем."""
    try:
        estimated = float(row.get("estimated_value") or 0)
    except (TypeError, ValueError):
        estimated = 0.0
    if estimated > 0:
        return estimated
    return prize_usd(str(row.get("text") or ""), snap)


def debt_row(row: dict, value: Optional[float]) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "score": score_of(row),
        "value": value,
        "status": row.get("status"),
        "text": str(row.get("text") or "")[:400],
        "link": row.get("link"),
        "mentions": row.get("mentions"),
    }


def tracked() -> list[str]:
    return [str(u).lstrip("@") for u in (state.ping_usernames or []) if u]


@router.get("/api/app/debts")
async def debts(caller: Caller = Depends(admin_caller),
                seg: str = Query("a", pattern="^[achn]$"),
                sort: str = Query("d", pattern="^[dv]$"),
                account: str = Query("", max_length=64)) -> dict:
    board = await get_debt_board(state.ping_usernames, limit=BOARD_LIMIT)
    snap = await latest_snapshot()
    all_rows = board.get("rows") or []
    by_account = value_by_account(all_rows, tracked(), snap)
    rows = segment_rows(all_rows, seg)
    if account:
        if account not in tracked():
            raise HTTPException(status_code=422, detail="Такой аккаунт не отслеживается")
        rows = [r for r in rows if account_mentioned(r.get("mentions"), account)]
    values = {int(r["id"]): row_value(r, snap) for r in rows}
    if sort == "v":
        rows = sorted(rows, key=lambda r: -(values[int(r["id"])] or 0))
    top = hottest(rows)
    return {
        "segment": seg,
        "sort": sort,
        "account": account,
        "segments": [{"code": code, "label": label} for code, label in SEGMENTS],
        "stats": board.get("stats") or {},
        "count": len(rows),
        "value": sum(v for v in values.values() if v),
        "unpriced": sum(1 for v in values.values() if not v),
        "hottest": top["id"] if top else None,
        "accounts": [
            {"name": name, "usd": round(bucket["usd"], 2), "count": int(bucket["priced"] + bucket["unpriced"])}
            for name, bucket in sorted(by_account.items(), key=lambda kv: (-kv[1]["usd"], kv[0]))
        ],
        "items": [debt_row(r, values[int(r["id"])]) for r in rows],
    }


class ClaimBody(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=BOARD_LIMIT)
    status: str = Field("claimed", pattern="^(claimed|scam|missed)$")


@router.post("/api/app/debts/claim")
async def claim(body: ClaimBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    ids = list(dict.fromkeys(body.ids))
    audit(request, ids=ids, status=body.status)
    before = await snapshot(ids)
    for ping_id in ids:
        await apply_ping_meta(ping_id, giveaway_status=body.status, action_status=body.status)
    label = {"claimed": "забрал", "scam": "скам", "missed": "пропустил"}[body.status]
    return {"ok": True, "count": len(ids), "status": body.status,
            "undo": remember(caller.tg_id, before, label)}
