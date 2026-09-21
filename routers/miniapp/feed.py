"""Лента упоминаний и поиск: список и карточка записи. Только чтение.

Выборка — ``bot/sections/feed.fetch``, та же, что у ``mon:f:`` в боте: FTS для
голого поиска, ``mention_any`` по белому списку аккаунтов ключа. Лента открыта
грантом ``recent``, поиск — грантом ``search``: панель и бот отказывают одному
ключу в одном и том же.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

import database
from pulse_desk.bot.sections.feed import SEARCH_FEATURE, fetch, tags_of
from pulse_desk.bot.views import (
    FEED_SORTS, FEED_STATUSES, FEED_TYPES, FeedFilter, giveaway_status_label, ping_status_label,
)
from pulse_desk.bot_permissions import accounts_allowed

from .common import Caller, current_caller

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
) -> dict:
    """``after`` — id последней строки, что уже на экране: следующая порция
    идёт от неё, а не от номера страницы. ``loaded`` — сколько строк на экране,
    на случай, если той строки уже нет."""
    query = q.strip()
    caller.require(SEARCH_FEATURE if query else FEATURE)
    filt = FeedFilter(type=kind, status=status, sort=sort, ascending=asc, query=bool(query), page=page)
    if after is None:
        rows, has_more = await fetch(filt, caller.perms, query, page_size=PAGE_SIZE)
    else:
        last = await database.get_ping_by_id(after)
        rows, has_more = await fetch(filt, caller.perms, query, page_size=PAGE_SIZE,
                                     after=last, offset=loaded)
    return {
        "items": [feed_row(r) for r in rows],
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
    return card
