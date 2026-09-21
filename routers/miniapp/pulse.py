"""«Пульс»: дешёвый ответ «появилось ли что-то новое» для открытой панели.

Страница спрашивает его раз в 30 секунд, пока она на экране, и по разнице
решает, где зажечь метку «новое». Экран сам не перерисовывается — это
украло бы фокус и прокрутку, — человек нажимает «Обновить» сам.

Каждое число считается в пределах ключа, как и всё остальное в панели:
самая свежая запись про аккаунты гостя, а не про всю базу. Ответ кэшируется
на 10 секунд на человека (у владельца — один на всех владельцев), так что
пять открытых панелей стоят базе пары запросов в минуту.
"""
from __future__ import annotations

import time
from typing import Any, Hashable

from fastapi import APIRouter, Depends

from database import get_pings, giveaway_bucket_total
from pulse_desk.app_ctx import logger

from .common import Caller, current_caller
from .home import _summary_cache

router = APIRouter()

CACHE_SECONDS = 10.0
_cache: dict[Hashable, tuple[float, dict[str, Any]]] = {}


async def _latest(chat_type: str, scope: list[str]) -> int:
    rows = await get_pings(limit=1, chat_type=chat_type, mention_any=scope or None)
    return int(rows[0]["id"]) if rows else 0


async def measure(caller: Caller) -> dict[str, Any]:
    scope = list(caller.perms.get("accounts") or [])
    body: dict[str, Any] = {}
    if caller.may("recent") or caller.may("search"):
        body["latest"] = await _latest("all", scope)
    if caller.may("giveaways"):
        body["latest_giveaway"] = await _latest("giveaway", scope)
        body["latest_win"] = await _latest("win", scope)
    if caller.is_admin:
        body["queue"] = await giveaway_bucket_total("need_action")
        summary = _summary_cache.get("value") or {}
        body["level"] = summary.get("health_level")
    return body


@router.get("/api/app/pulse")
async def pulse(caller: Caller = Depends(current_caller)) -> dict:
    key: Hashable = "admin" if caller.is_admin else caller.tg_id
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        body = await measure(caller)
    except Exception:
        logger.warning("Mini App pulse failed", exc_info=True)
        return hit[1] if hit else {}
    _cache[key] = (now, body)
    return body
