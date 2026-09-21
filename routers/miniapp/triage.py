"""«Разбор»: всё, что ждёт решения владельца, одной очередью.

Порядок — по тому, что дороже упустить: незабранные победы (горячие первыми,
дальше по сумме), затем розыгрыши, в которых стоит участвовать, затем
аккаунты с проблемой. Сами действия идут через обычные эндпоинты
(``/pings/{id}/status``, ``/giveaways/{id}/skip``, ``/accounts/{name}/reconnect``),
так что у «Разбора» нет своей правды о статусах — только порядок.

Очередь собирается из тех же досок, что у бота: ``get_debt_board`` (одна
строка на пост — копии склеены ``dedupe``) и корзина ``need_action``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends

from database import get_debt_board, get_giveaway_board
from pulse_desk.account_health import account_problem
from pulse_desk.app_ctx import logger, state
from pulse_desk.bot.sections.debts import BOARD_LIMIT, score_of
from pulse_desk.bot.sections.market import latest_snapshot

from .common import Caller, admin_caller
from .debts import row_value

router = APIRouter()

# The whole debts board: the home counter counts it all, so the queue must too.
WIN_LIMIT = BOARD_LIMIT
GIVEAWAY_LIMIT = 60
TEXT_CAP = 700


def mention_names(raw: Any) -> list[str]:
    from .feed import mention_list

    return mention_list(raw)


def win_card(row: dict, value: Optional[float]) -> dict:
    return {
        "kind": "win",
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "text": str(row.get("text") or "")[:TEXT_CAP],
        "link": row.get("link"),
        "score": score_of(row),
        "value": value,
        "accounts": mention_names(row.get("mentions")),
    }


def giveaway_card(row: dict) -> dict:
    return {
        "kind": "giveaway",
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "text": str(row.get("text") or "")[:TEXT_CAP],
        "link": row.get("link"),
        "score": row.get("candidate_score"),
        "priority": row.get("priority_label"),
        "accounts": mention_names(row.get("mentions")),
    }


def account_cards(now: datetime) -> list[dict]:
    cards = []
    for name, account in sorted(state.accounts_state.items()):
        problem = account_problem(account, now, app_started_at=state.started_at)
        if problem is None:
            continue
        cards.append({
            "kind": "account",
            "id": name,
            "session_name": name,
            "username": account.get("username") or "",
            "status": account.get("status") or "",
            "problem": problem[1],
            "error": str(account.get("last_error") or "")[:200],
        })
    return cards


def order_wins(rows: list[dict], values: dict[int, Optional[float]]) -> list[dict]:
    """Горячие (высокий приоритет) первыми, внутри — дороже и свежее.

    Два устойчивых прохода: сначала свежие сверху, потом приоритет и сумма —
    равные по ним остаются в порядке свежести."""
    newest = sorted(rows, key=lambda r: str(r.get("detected_at") or ""), reverse=True)
    return sorted(newest, key=lambda r: (-score_of(r), -(values.get(int(r["id"])) or 0)))


@router.get("/api/app/triage")
async def triage(caller: Caller = Depends(admin_caller)) -> dict:
    debts, board, snap = await asyncio.gather(
        get_debt_board(state.ping_usernames, limit=BOARD_LIMIT),
        get_giveaway_board(limit=GIVEAWAY_LIMIT * 2, include_outbox=False),
        latest_snapshot(),
        return_exceptions=True,
    )
    items: list[dict] = []
    if isinstance(debts, dict):
        rows = debts.get("rows") or []
        values = {int(r["id"]): row_value(r, snap if isinstance(snap, dict) else None) for r in rows}
        items += [win_card(r, values[int(r["id"])]) for r in order_wins(rows, values)[:WIN_LIMIT]]
    else:
        logger.warning("Mini App triage: debt board failed: %s", debts)
    if isinstance(board, dict):
        rows = [r for r in (board.get("buckets") or {}).get("need_action") or [] if not r.get("is_win")]
        items += [giveaway_card(r) for r in rows[:GIVEAWAY_LIMIT]]
    else:
        logger.warning("Mini App triage: giveaway board failed: %s", board)
    items += account_cards(datetime.now())
    counts: dict[str, int] = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"items": items, "counts": counts}
