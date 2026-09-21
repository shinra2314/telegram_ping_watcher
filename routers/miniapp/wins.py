"""«Мои победы»: история побед аккаунтов ключа, а не очередь.

Ссылка «Все ›» на главной гостя вела в очередь ``need_action`` — оттуда
забранная победа исчезает, и человек не видел, что уже было. Здесь — все
победы за 30 или 90 дней, с тем, чем они кончились, и суммой приза.

Копии одного поста с итогами (``duplicate_of``) не показываются: это одна
победа. Листается курсором, как лента (``get_pings(after=…)``). Сумма — приз
из текста по свежему снимку курсов (``prize_value``), как у «💵 Незабрано».
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

import database
from pulse_desk.bot.sections.giveaways import visible_accounts
from pulse_desk.bot.sections.market import latest_snapshot
from pulse_desk.prize_value import prize_usd

from .common import Caller, current_caller
from .feed import mention_list

router = APIRouter()

FEATURE = "giveaways"
PAGE_SIZE = 20
PERIODS = (30, 90)
# Копии склеенного поста выпадают после выборки, поэтому берём с запасом.
OVERFETCH = 2


def outcome(row: dict) -> str:
    """claimed / scam / missed / open — чем кончилась победа."""
    codes = {row.get("giveaway_status") or "", row.get("action_status") or ""}
    for code in ("claimed", "scam", "missed", "closed"):
        if code in codes:
            return code
    if "missed_unsubscribe" in codes or "missed_reply" in codes:
        return "missed"
    return "open"


@router.get("/api/app/wins")
async def wins(caller: Caller = Depends(current_caller),
               days: int = Query(30),
               after: Optional[int] = Query(None, ge=1)) -> dict:
    caller.require(FEATURE)
    days = days if days in PERIODS else PERIODS[0]
    since = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    scope = caller.perms.get("accounts") or None
    keyset = None
    if after is not None:
        last = await database.get_ping_by_id(after)
        if last:
            keyset = (last.get("detected_at"), int(last["id"]))
    rows = await database.get_pings(limit=PAGE_SIZE * OVERFETCH + 1, chat_type="win", mention_any=scope,
                                    date_from=since, after=keyset)
    has_more = len(rows) > PAGE_SIZE * OVERFETCH
    rows = rows[:PAGE_SIZE * OVERFETCH]
    snap = await latest_snapshot()
    # A win shared with other tracked accounts names only the key's own here.
    own = {a.lower() for a in scope or []}

    def accounts_of(raw) -> list[str]:
        names = mention_list(raw)
        return [n for n in names if n.lower() in own] if own else names

    items = [
        {
            "id": int(r["id"]),
            "chat": r.get("chat") or "?",
            "detected_at": r.get("detected_at"),
            "accounts": accounts_of(r.get("mentions")),
            "outcome": outcome(r),
            "value": prize_usd(str(r.get("text") or ""), snap),
            "link": r.get("link"),
        }
        for r in rows if not r.get("duplicate_of")
    ]
    summary = None
    if after is None:
        stats = await database.account_win_stats(visible_accounts(caller.perms), since)
        summary = {"wins": int(stats.get("wins") or 0), "claimed": int(stats.get("claimed") or 0)}
    return {
        "days": days,
        "items": items,
        "has_more": has_more,
        # The cursor is the last row read, copies included, so a page of
        # copies is not read twice.
        "next": int(rows[-1]["id"]) if rows and has_more else None,
        "summary": summary,
        "scope": list(caller.perms.get("accounts") or []),
    }
