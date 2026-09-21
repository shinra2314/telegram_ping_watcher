"""Лента упоминаний и поиск: список, карточка, а у владельца — разбор записи.

Выборка — ``bot/sections/feed.fetch``, та же, что у ``mon:f:`` в боте: FTS для
голого поиска, ``mention_any`` по белому списку аккаунтов ключа. Лента открыта
грантом ``recent``, поиск — грантом ``search``: панель и бот отказывают одному
ключу в одном и том же.

Гостю лента — только чтение. Владелец ставит статус, звезду, заметку и теги
(``apply_ping_meta`` и теговые функции — как карточка упоминания в боте),
отмечает пачку прочитанной и выключает чат (``ignored_chats``, как 🔇).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

import database
from pulse_desk import ignored_chats
from pulse_desk.bot.sections.feed import SEARCH_FEATURE, fetch, tags_of
from pulse_desk.bot.views import (
    FEED_SORTS, FEED_STATUSES, FEED_TYPES, FeedFilter, giveaway_status_label, ping_status_label,
)
from pulse_desk.bot_permissions import accounts_allowed
from pulse_desk.common import record_app_event
from pulse_desk.ping_actions import UnknownStatus, apply_ping_meta
from pulse_desk.statuses import PING_STATUSES

from .common import Caller, audit, current_caller, fresh_admin

router = APIRouter()

FEATURE = "recent"
PAGE_SIZE = 20
SNIPPET = 160


def _codes(table) -> str:
    return "^[" + "".join(row[0] for row in table) + "]$"


def mention_list(raw) -> list[str]:
    """``mentions`` as a list of names, whatever the column held."""
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw or "[]")
        except (TypeError, ValueError):
            items = [raw]
        if not isinstance(items, list):
            items = [items]
    return [str(i).strip().lstrip("@") for i in items if str(i or "").strip()]


def snippet(text: Optional[str], limit: int = SNIPPET) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def outcome_label(row: dict) -> Optional[str]:
    """Giveaway outcome in words; None for a plain mention.

    A win still "pending" is not waiting for results any more — it is a prize
    nobody has taken yet, and "жду итогов" on a win card reads as a bug.
    """
    if not (row.get("is_giveaway") or row.get("is_win")):
        return None
    code = row.get("giveaway_status") or ""
    if row.get("is_win") and code in ("", "pending"):
        return "не забрано"
    return giveaway_status_label(code)


def feed_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "chat_type": row.get("chat_type") or "",
        "sender": row.get("sender") or "",
        "detected_at": row.get("detected_at"),
        "date": row.get("date"),
        "priority": row.get("priority_label"),
        "is_win": bool(row.get("is_win")),
        "is_giveaway": bool(row.get("is_giveaway")),
        "deleted": bool(row.get("deleted_at")),
        "snippet": snippet(row.get("text")),
        "mentions": mention_list(row.get("mentions")),
    }


@router.get("/api/app/feed")
async def feed(
    caller: Caller = Depends(current_caller),
    kind: str = Query("a", alias="type", pattern=_codes(FEED_TYPES)),
    status: str = Query("a", pattern=_codes(FEED_STATUSES)),
    sort: str = Query("d", pattern=_codes(FEED_SORTS)),
    asc: bool = Query(False),
    q: str = Query("", max_length=64),
    page: int = Query(1, ge=1, le=200),
    after: Optional[int] = Query(None, ge=1),
    loaded: int = Query(0, ge=0, le=PAGE_SIZE * 200),
    fav: bool = Query(False),
) -> dict:
    """``after`` — id последней строки, что уже на экране: следующая порция
    идёт от неё, а не от номера страницы. ``loaded`` — сколько строк на экране,
    на случай, если той строки уже нет. ``fav`` — звёзды владельца, только ему."""
    query = q.strip()
    caller.require(SEARCH_FEATURE if query else FEATURE)
    filt = FeedFilter(type=kind, status=status, sort=sort, ascending=asc, query=bool(query), page=page,
                      favorite=fav and caller.is_admin)
    if after is None:
        rows, has_more = await fetch(filt, caller.perms, query, page_size=PAGE_SIZE)
    else:
        last = await database.get_ping_by_id(after)
        rows, has_more = await fetch(filt, caller.perms, query, page_size=PAGE_SIZE,
                                     after=last, offset=loaded)
    items = [feed_row(r) for r in rows]
    if caller.is_admin:
        for item, row in zip(items, rows):
            item["status"] = row.get("status") or "new"
            item["favorite"] = bool(row.get("is_favorite"))
    return {
        "items": items,
        "has_more": has_more,
        "page": page,
        "can_search": caller.may(SEARCH_FEATURE),
        "can_status": caller.is_admin,
        "accounts": list(caller.perms.get("accounts") or []),
    }


@router.get("/api/app/feed/{ping_id}")
async def feed_card(ping_id: int, caller: Caller = Depends(current_caller)) -> dict:
    if not (caller.may(FEATURE) or caller.may(SEARCH_FEATURE)):
        raise HTTPException(status_code=403, detail="Этот раздел вам не открыт")
    row = await database.get_ping_by_id(ping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if not accounts_allowed(caller.perms, row.get("mentions")):
        raise HTTPException(status_code=403, detail="Эта запись не про ваш аккаунт")
    card = feed_row(row)
    card.update({
        "text": row.get("text") or "",
        "link": row.get("link"),
        # New / read / resolved is the owner's inbox state — to a guest "Новое"
        # only says the owner has not opened it yet.
        "status": ping_status_label(row.get("status")) if caller.is_admin else None,
        "giveaway_status": outcome_label(row),
        "tags": tags_of(row),
        "note": row.get("note") or "",
    })
    if caller.is_admin:
        card.update({
            "status_code": row.get("status") or "new",
            "favorite": bool(row.get("is_favorite")),
            "can_ignore": row.get("chat_type") == "group" and row.get("chat_id") is not None,
            "ignored": ignored_chats.is_ignored(row.get("chat_id")),
            "statuses": [{"code": code, "label": ping_status_label(code)} for code in STATUS_ORDER],
        })
    return card


# The owner's inbox states in the order a card offers them.
STATUS_ORDER = ("new", "read", "important", "resolved", "ignored")


class MetaBody(BaseModel):
    status: Optional[str] = Field(None, max_length=16)
    favorite: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=1000)
    tag_add: Optional[str] = Field(None, max_length=32)
    tag_remove: Optional[str] = Field(None, max_length=32)


@router.post("/api/app/feed/{ping_id}/meta")
async def ping_meta(ping_id: int, body: MetaBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    """Status, star, note, tags — what the mention card's buttons do in the bot."""
    if await database.get_ping_by_id(ping_id) is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    changed = body.model_dump(exclude_none=True)
    audit(request, fields=sorted(changed))
    if body.status is not None and body.status not in PING_STATUSES:
        raise HTTPException(status_code=422, detail="Неизвестный статус")
    try:
        if body.status is not None or body.favorite is not None or body.note is not None:
            await apply_ping_meta(ping_id, status=body.status, is_favorite=body.favorite,
                                  note=body.note.strip() if body.note is not None else None)
    except UnknownStatus as exc:
        raise HTTPException(status_code=422, detail="Неизвестный статус") from exc
    if body.tag_add:
        await database.add_ping_tag(ping_id, body.tag_add.strip().lstrip("#"))
    if body.tag_remove:
        await database.remove_ping_tag(ping_id, body.tag_remove.strip().lstrip("#"))
    return await feed_card(ping_id, caller)


class ReadBody(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=100)


@router.post("/api/app/feed/read")
async def mark_read(body: ReadBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    """«Прочитано · N» — one request for the selection, not one per row."""
    ids = list(dict.fromkeys(body.ids))
    audit(request, ids=ids)
    for ping_id in ids:
        await apply_ping_meta(ping_id, status="read")
    return {"ok": True, "count": len(ids)}


@router.post("/api/app/feed/{ping_id}/ignore")
async def ignore_chat(ping_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    """🔇 — stop watching the chat this message came from (``igc:add``)."""
    row = await database.get_ping_by_id(ping_id)
    if not row or row.get("chat_id") is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    chat_id, title = int(row["chat_id"]), str(row.get("chat") or row["chat_id"])
    await ignored_chats.save(ignored_chats.add(await ignored_chats.load(), chat_id, title))
    await record_app_event("INFO", "notifications", "Chat ignored", {"chat_id": chat_id, "chat": title, "via": "panel"})
    return {"ok": True, "chat_id": chat_id, "title": title}
