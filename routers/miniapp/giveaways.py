"""Розыгрыши и победы: лента, карточка, статус, разбор, уборка каналов.

Лента фильтруется ровно как ``gw:f:`` в боте: те же ``GiveawayFilter`` и
``visible_accounts``, аккаунт адресуется индексом в том же списке — панель и
кнопки бота разойтись не могут. Статус меняется через ``apply_ping_meta``,
чтобы «забрал» доехал и до заметки Obsidian, а перед сменой снимается снимок
(``bot/undo``) — «Вернуть» в панели и «↩️ Отменить» в боте один и тот же откат.

Разбор, профиль канала, «не участвуем» и выход из каналов — те же функции
``giveaway_ops``, что у кнопок ``gw:an/pr/sk/lv`` бота. Они тратят запросы
Telegram, поэтому только у владельца.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import database
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections.giveaways import visible_accounts
from pulse_desk.bot.undo import remember, snapshot
from pulse_desk.bot.views import ALL_ACCOUNTS, GiveawayFilter
from pulse_desk.bot_permissions import account_mentioned, accounts_allowed
from pulse_desk.giveaway_ops import (
    GiveawayActionError, analyze, cleanup_candidates, leave_channel, refresh_profile_for_ping, skip,
)
from pulse_desk.ping_actions import UnknownStatus, action_for_giveaway, apply_ping_meta

from .common import Caller, admin_caller, audit, current_caller, fresh_admin

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
    after: Optional[int] = Query(None, ge=1),
    loaded: int = Query(0, ge=0, le=PAGE_SIZE * 200),
) -> dict:
    """``after`` / ``loaded`` — как в ленте: следующая порция идёт от последней
    строки на экране. Очередь собирается на Python, так что курсор — это просто
    позиция той строки в списке; если её уже нет (забрана в боте), — счётчик."""
    caller.require(FEATURE)
    filt = GiveawayFilter(sort=sort, wins=wins, account=account, page=page)
    accounts = visible_accounts(caller.perms)
    start = loaded if after is not None else PAGE_SIZE * (page - 1)
    # С запасом на новые строки сверху: курсор должен остаться в окне.
    window = start + (2 * PAGE_SIZE if after is not None else PAGE_SIZE) + 1
    rows, total = await need_action(caller, filt, limit=window * (8 if is_narrowed(caller, filt) else 1))
    if after is not None:
        ids = [int(r["id"]) for r in rows]
        if after in ids:
            start = ids.index(after) + 1
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
    if caller.is_admin:
        card["candidate"] = candidate_view(await database.get_giveaway_candidate(ping_id))
        card["channel"] = await channel_view(row.get("chat_id"))
    return card


def candidate_view(candidate: Optional[dict]) -> Optional[dict]:
    """Что разбор узнал о розыгрыше — то, что карточке есть смысл показать."""
    if not candidate:
        return None
    return {
        "status": candidate.get("status"),
        "score": candidate.get("score"),
        "reasons": [str(r) for r in candidate.get("reasons") or []][:6],
        "required_channels": [str(c) for c in candidate.get("required_channels") or []][:8],
        "join_buttons": len(candidate.get("join_buttons") or []),
        "external": [str(r) for r in candidate.get("external_requirements") or []][:4],
        "blocked": candidate.get("blocked_reason") or "",
        "analyzed_at": candidate.get("analyzed_at"),
    }


async def channel_view(chat_id: Any) -> Optional[dict]:
    if chat_id is None:
        return None
    profile = await database.get_channel_profile(int(chat_id)) or {}
    score = await database.get_source_score(int(chat_id)) or {}
    if not profile and not score:
        return None
    return {
        "username": profile.get("username") or "",
        "description": str(profile.get("description") or "")[:300],
        "fetched_at": profile.get("fetched_at"),
        "error": profile.get("last_error") or "",
        # `source_scores.score` is an unbounded sum (a win is worth 25), so the
        # card shows what it is made of instead of a number nobody can read.
        "wins": int(score.get("wins") or 0),
        "giveaways": int(score.get("giveaways") or 0),
        "noise": int(score.get("noise") or 0),
    }


def action_failed(exc: GiveawayActionError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc)[:200] or "Не получилось")


@router.post("/api/app/giveaways/{ping_id}/analyze")
async def analyze_card(ping_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    try:
        candidate = await analyze(ping_id)
    except GiveawayActionError as exc:
        raise action_failed(exc) from exc
    return {"ok": True, "candidate": candidate_view(candidate)}


@router.post("/api/app/giveaways/{ping_id}/profile")
async def refresh_card_profile(ping_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    try:
        await refresh_profile_for_ping(ping_id)
    except GiveawayActionError as exc:
        raise action_failed(exc) from exc
    row = await database.get_ping_by_id(ping_id) or {}
    return {"ok": True, "channel": await channel_view(row.get("chat_id"))}


@router.post("/api/app/giveaways/{ping_id}/skip")
async def skip_card(ping_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    """«Не участвуем». Без разбора кандидата нет — тогда запись просто закрывается."""
    before = await snapshot([ping_id])
    if not before:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    try:
        await skip(ping_id)
    except GiveawayActionError:
        await apply_ping_meta(ping_id, giveaway_status="closed", action_status="closed")
    return {"ok": True, "id": ping_id, "undo": remember(caller.tg_id, before, "не участвуем")}


class StatusBody(BaseModel):
    status: str
    # Без него действие выводится из статуса. «Вернуть» после ✕ в списке шлёт
    # прежнее: пустой статус сам по себе запись в очередь не возвращает.
    action: Optional[str] = None


@router.post("/api/app/pings/{ping_id}/status")
async def set_status(ping_id: int, body: StatusBody, request: Request,
                     caller: Caller = Depends(fresh_admin)) -> dict:
    """Статус записи. В ответе — ``undo``: токен отката (``bot/undo``, 30 с)."""
    before = await snapshot([ping_id])
    if not before:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    action = body.action if body.action is not None else action_for_giveaway(body.status)
    try:
        await apply_ping_meta(ping_id, giveaway_status=body.status, action_status=action)
    except UnknownStatus as exc:
        raise HTTPException(status_code=422, detail="Неизвестный статус") from exc
    audit(request, status=body.status)
    return {"ok": True, "id": ping_id, "status": body.status,
            "undo": remember(caller.tg_id, before, "статус")}


# ---- уборка: каналы, которые давно молчат ---------------------------------
CLEANUP_FRESH_SECONDS = 10 * 60
# Между выходами: несколько каналов подряд из восьми аккаунтов — это десятки
# LeaveChannel, и Telegram отвечает на такой залп FloodWait.
LEAVE_PAUSE_SECONDS = 2.0
LEAVE_BATCH = 20


def cleanup_row(item: dict) -> dict:
    return {
        "chat_id": int(item["chat_id"]),
        "title": item.get("title") or str(item.get("chat_id")),
        "username": item.get("username") or "",
        "inactive_days": item.get("inactive_days"),
        "accounts": list(item.get("accounts") or []),
        "wins": int(item.get("wins") or 0),
        "last_win_at": item.get("last_win_at"),
    }


@router.get("/api/app/cleanup")
async def cleanup(caller: Caller = Depends(admin_caller), force: bool = Query(False)) -> dict:
    """Кандидаты на выход. Обход диалогов дорог — 10 минут берётся кэш бота."""
    cache = state.bot_cleanup_cache or {}
    at = cache.get("at")
    fresh = bool(at) and (datetime.now() - at).total_seconds() < CLEANUP_FRESH_SECONDS
    warning = ""
    if force or not fresh:
        data = await cleanup_candidates()
        warning = data.get("warning") or ""
        cache = state.bot_cleanup_cache or {}
    items = sorted((cache.get("items") or {}).values(),
                   key=lambda i: (int(i.get("wins") or 0) > 0, -int(i.get("inactive_days") or 0)))
    return {"items": [cleanup_row(i) for i in items], "warning": warning}


class LeaveBody(BaseModel):
    chat_ids: list[int] = Field(..., min_length=1, max_length=LEAVE_BATCH)


@router.post("/api/app/cleanup/leave")
async def cleanup_leave(body: LeaveBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    """Выйти из выбранных каналов всеми аккаунтами, что в них сидят. Необратимо —
    подтверждение на странице. FloodWait останавливает весь проход."""
    ids = list(dict.fromkeys(body.chat_ids))
    audit(request, chat_ids=ids)
    known = (state.bot_cleanup_cache or {}).get("items") or {}
    results = []
    for index, chat_id in enumerate(ids):
        item = known.get(chat_id)
        if item is None:
            results.append({"chat_id": chat_id, "left": 0, "error": "Нет в списке — обновите его"})
            continue
        try:
            left = await leave_channel(chat_id, item.get("accounts") or None)
            results.append({"chat_id": chat_id, "left": left})
            known.pop(chat_id, None)
        except GiveawayActionError as exc:
            results.append({"chat_id": chat_id, "left": 0, "error": str(exc)[:160]})
            if exc.code == "flood_wait":
                break
        if index < len(ids) - 1:
            await asyncio.sleep(LEAVE_PAUSE_SECONDS)
    return {"results": results, "left": sum(1 for r in results if r["left"])}
