"""Главный экран панели: какие плитки открыты и что на них написать.

Владельцу — сводка пульта (``collect_dashboard``, тот же сборщик, что у 🛰 Пульта
бота) с коротким кэшем: она стоит пачку запросов, а главную открывают часто.
Гостю — только счётчики разделов, которые открыл его ключ.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends

from database import get_debt_board
from pulse_desk.app_ctx import logger, state
from pulse_desk.bot.sections.debts import BOARD_LIMIT, total_value
from pulse_desk.bot.sections.market import latest_snapshot
from pulse_desk.bot.sections.salary import visible as salary_visible
from pulse_desk.bot.views import GiveawayFilter
from pulse_desk.converter import usd_value
from pulse_desk.dashboard import collect_dashboard

from .common import Caller, current_caller
from .giveaways import need_action

router = APIRouter()

SUMMARY_TTL_SECONDS = 15.0
_summary_cache: dict[str, Any] = {"at": 0.0, "value": None}

# Монеты в полоске курсов на главной.
TICKER = ("BTC", "TON", "USDT")


async def cached_summary() -> Optional[dict[str, Any]]:
    now = time.monotonic()
    if _summary_cache["value"] is not None and now - _summary_cache["at"] < SUMMARY_TTL_SECONDS:
        return _summary_cache["value"]
    try:
        value = await collect_dashboard()
    except Exception:
        logger.warning("Mini App: dashboard summary failed", exc_info=True)
        return _summary_cache["value"]
    _summary_cache.update(at=now, value=value)
    return value


def ticker(snapshot: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    uah = usd_value(snapshot, "UAH")
    items = []
    for code in TICKER:
        usd = usd_value(snapshot, code)
        if not usd:
            continue
        # Raw numbers: the strip is narrow, so the page picks the precision.
        items.append({"code": code, "usd": usd, "uah": (usd / uah) if uah else None})
    return items


@router.get("/api/app/home")
async def home(caller: Caller = Depends(current_caller)) -> dict:
    sections = {
        "giveaways": caller.may("giveaways"),
        "market": caller.may("market"),
        "debts": caller.is_admin,
        "accounts": caller.is_admin,
        "salary": await salary_visible(caller.tg_id, caller.role),
    }
    payload: dict[str, Any] = {"name": caller.name, "role": caller.role, "sections": sections}

    tasks: dict[str, Any] = {}
    if sections["giveaways"]:
        tasks["giveaways"] = need_action(caller, GiveawayFilter(), limit=400)
    if sections["market"]:
        tasks["market"] = latest_snapshot()
    if caller.is_admin:
        tasks["summary"] = cached_summary()
        tasks["debts"] = get_debt_board(state.ping_usernames, limit=BOARD_LIMIT)
    results = dict(zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True)))
    for key, value in results.items():
        if isinstance(value, BaseException):
            logger.warning("Mini App home: %s failed: %s", key, value)
            results[key] = None

    counters: dict[str, Any] = {}
    if results.get("giveaways") is not None:
        rows, total = results["giveaways"]
        counters["giveaways"] = total
        counters["wins"] = sum(1 for r in rows if r.get("is_win"))
    if results.get("debts") is not None:
        debt_rows = results["debts"].get("rows") or []
        counters["debts"] = len(debt_rows)
        counters["debts_value"] = total_value(debt_rows)
    if caller.is_admin:
        counters["accounts_online"] = sum(
            1 for a in state.accounts_state.values() if a.get("status") == "online")
        counters["accounts_total"] = len(set(state.session_names) | set(state.accounts_state))
    payload["counters"] = counters
    payload["ticker"] = ticker(results.get("market"))

    summary = results.get("summary")
    if summary:
        payload["summary"] = {
            "headline": summary.get("headline"),
            "level": summary.get("health_level"),
            "attention": [
                {k: item.get(k) for k in ("key", "title", "text", "value", "tone")}
                for item in summary.get("attention") or []
            ],
            "counts": summary.get("counts") or {},
            "scan": summary.get("scan_progress") or {},
        }
    return payload
