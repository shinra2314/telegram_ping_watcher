"""Розыгрыши и победы: лента, карточка, статус.

Лента фильтруется ровно как ``gw:f:`` в боте: те же ``GiveawayFilter`` и
``visible_accounts``, аккаунт адресуется индексом в том же списке — панель и
кнопки бота разойтись не могут. Статус меняется через ``apply_ping_meta``,
чтобы «забрал» доехал и до заметки Obsidian.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import database
from pulse_desk.bot.sections.giveaways import visible_accounts
from pulse_desk.bot.views import ALL_ACCOUNTS, GiveawayFilter
from pulse_desk.bot_permissions import account_mentioned, accounts_allowed
from pulse_desk.ping_actions import UnknownStatus, action_for_giveaway, apply_ping_meta

from .common import Caller, current_caller, fresh_admin

router = APIRouter()

FEATURE = "giveaways"
PAGE_SIZE = 12
# Статусы, которые панель умеет ставить одной кнопкой.
QUICK_STATUSES = ("claimed", "missed", "scam", "closed")


def is_narrowed(caller: Caller, filt: GiveawayFilter) -> bool:
    """Любой фильтр ест строки окна, и тогда глубину очереди знает только выборка."""
    accounts = visible_accounts(caller.perms)
    return bool(caller.perms.get("accounts")) or filt.wins or 0 <= filt.account < len(accounts)


async def need_action(caller: Caller, filt: GiveawayFilter, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Корзина «требует действия», сужённая до того, что этому ключу видно.

    Возвращает строки окна и настоящую глубину очереди: без фильтров это
    ``bucket_totals`` доски, а не длина окна — иначе заголовок врёт «13 в очереди»
    при двух сотнях (так же считает ``render_feed`` бота).
    """
    board = await database.get_giveaway_board(limit=limit, sort=filt.db_sort, include_outbox=False)
    rows = [
        row for row in (board.get("buckets") or {}).get("need_action") or []
        if accounts_allowed(caller.perms, row.get("mentions"))
    ]
    if filt.wins:
        rows = [r for r in rows if r.get("is_win")]
    accounts = visible_accounts(caller.perms)
    if 0 <= filt.account < len(accounts):
        picked = accounts[filt.account]
        rows = [r for r in rows if account_mentioned(r.get("mentions"), picked)]
    if is_narrowed(caller, filt):
        return rows, len(rows)
    return rows, int((board.get("bucket_totals") or {}).get("need_action") or len(rows))


def feed_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "date": row.get("date"),
        "is_win": bool(row.get("is_win")),
        "deleted": bool(row.get("deleted_at")),
        "priority": row.get("priority_label"),
        "status": row.get("giveaway_status"),
        "action": row.get("action_status"),
        "link": row.get("link"),
    }


@router.get("/api/app/giveaways")
async def giveaways(
    caller: Caller = Depends(current_caller),
    sort: str = Query("d", pattern="^[dp]$"),
    wins: bool = Query(False),
    account: int = Query(ALL_ACCOUNTS),
    page: int = Query(1, ge=1, le=200),
) -> dict:
    caller.require(FEATURE)
    filt = GiveawayFilter(sort=sort, wins=wins, account=account, page=page)
    accounts = visible_accounts(caller.perms)
    window = PAGE_SIZE * page + 1
    rows, total = await need_action(caller, filt, limit=window * (8 if is_narrowed(caller, filt) else 1))
    start = PAGE_SIZE * (page - 1)
    chunk = rows[start:start + PAGE_SIZE + 1]
    return {
        "items": [feed_row(r) for r in chunk[:PAGE_SIZE]],
        "has_more": len(chunk) > PAGE_SIZE,
        "page": page,
        "total": total,
        "accounts": accounts,
        "account": account,
        "sort": sort,
        "wins": wins,
        "can_edit": caller.is_admin,
    }


@router.get("/api/app/giveaways/{ping_id}")
async def giveaway_card(ping_id: int, caller: Caller = Depends(current_caller)) -> dict:
    caller.require(FEATURE)
    row = await database.get_ping_by_id(ping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if not accounts_allowed(caller.perms, row.get("mentions")):
        raise HTTPException(status_code=403, detail="Эта запись не про ваш аккаунт")
    card = feed_row(row)
    card.update({
        "text": row.get("text") or "",
        "sender": row.get("sender"),
        "mentions": row.get("mentions"),
        "note": row.get("note") or "",
        "estimated_value": row.get("estimated_value"),
        "can_edit": caller.is_admin,
        "quick_statuses": list(QUICK_STATUSES) if caller.is_admin else [],
    })
    return card


class StatusBody(BaseModel):
    status: str
    # Без него действие выводится из статуса. «Вернуть» после ✕ в списке шлёт
    # прежнее: пустой статус сам по себе запись в очередь не возвращает.
    action: Optional[str] = None


@router.post("/api/app/pings/{ping_id}/status")
async def set_status(ping_id: int, body: StatusBody, caller: Caller = Depends(fresh_admin)) -> dict:
    if await database.get_ping_by_id(ping_id) is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    action = body.action if body.action is not None else action_for_giveaway(body.status)
    try:
        await apply_ping_meta(ping_id, giveaway_status=body.status, action_status=action)
    except UnknownStatus as exc:
        raise HTTPException(status_code=422, detail="Неизвестный статус") from exc
    return {"ok": True, "id": ping_id, "status": body.status}
