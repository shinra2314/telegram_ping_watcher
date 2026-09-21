"""Главный экран панели: какие плитки открыты и что на них написать.

Владельцу — сводка пульта (``collect_dashboard``, тот же сборщик, что у 🛰 Пульта
бота) с коротким кэшем: она стоит пачку запросов, а главную открывают часто.
Гостю — его собственная главная: что открыл ключ (``me``: роль, аккаунты,
уведомления, задержка, расписание доступа) и его цифры (``mine``: победы его
аккаунтов, «участвую / пропустил», последние победы). Всё срезано по аккаунтам
ключа, как лента и розыгрыши.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends

from database import (
    account_win_stats, get_bot_member, get_debt_board, get_pings, list_access_windows,
    member_engagement_since,
)
from pulse_desk.account_health import account_problem
from pulse_desk.app_ctx import logger, state
from pulse_desk.bot.sections.giveaways import visible_accounts
from pulse_desk.bot.sections.debts import BOARD_LIMIT, total_value
from pulse_desk.bot.sections.market import latest_snapshot
from pulse_desk.bot.sections.salary import visible as salary_visible
from pulse_desk.bot.views import GiveawayFilter
from pulse_desk.bot_membership import access_decision
from pulse_desk.bot_permissions import NOTIFY_TYPES, allowed_pref_keys, format_delay, permission_delay_minutes
from pulse_desk.bot_prefs import parse_member_prefs
from pulse_desk.converter import usd_value
from pulse_desk.dashboard import collect_dashboard

from .common import Caller, current_caller
from .giveaways import need_action

router = APIRouter()

SUMMARY_TTL_SECONDS = 15.0
_summary_cache: dict[str, Any] = {"at": 0.0, "value": None}

# Монеты в полоске курсов на главной.
TICKER = ("BTC", "TON", "USDT")
# Окно «моих» цифр гостя и сколько последних побед показать.
MINE_DAYS = 30
RECENT_WINS = 3
# Граница расписания дальше этого — «не меняется в обозримое время», не показываем.
ACCESS_HORIZON = timedelta(hours=47)


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


async def member_profile(caller: Caller) -> Optional[dict[str, Any]]:
    """What the key opened, as the guest should see it. None for the owner."""
    if caller.is_admin:
        return None
    member = await get_bot_member(caller.tg_id)
    if not member:
        return None
    prefs = parse_member_prefs(member.get("notification_prefs"))
    access: dict[str, Any] = {"scheduled": False, "until": None}
    if await list_access_windows(caller.tg_id):
        access["scheduled"] = True
        _allowed, _reason, until = await access_decision(caller.tg_id, member)
        now = datetime.now(until.tzinfo) if until and until.tzinfo else datetime.now()
        if until and until - now < ACCESS_HORIZON:
            # Local wall-clock time: the page prints it as is.
            access["until"] = until.astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    return {
        "role": caller.role,
        "accounts": list(caller.perms.get("accounts") or []),
        "notify": [NOTIFY_TYPES[code][0].split(" ", 1)[-1] for code in allowed_pref_keys(caller.perms)],
        "delay_minutes": permission_delay_minutes(caller.perms),
        "delay_text": format_delay(permission_delay_minutes(caller.perms)),
        "muted": bool(prefs.get("muted")),
        "access": access,
    }


async def member_numbers(caller: Caller) -> dict[str, Any]:
    """The guest's own figures: their accounts' wins, their engagement, last wins."""
    since = (datetime.now() - timedelta(days=MINE_DAYS)).replace(microsecond=0).isoformat()
    names = visible_accounts(caller.perms)
    jobs: dict[str, Any] = {
        "engagement": member_engagement_since(caller.tg_id, since),
        "wins": account_win_stats(names, since),
    }
    if caller.may("giveaways"):
        # Over-fetch: copies of one winners post (``duplicate_of``) are dropped below.
        jobs["recent"] = get_pings(limit=RECENT_WINS * 4, chat_type="win",
                                   mention_any=caller.perms.get("accounts") or None)
    done = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
    for key, value in done.items():
        if isinstance(value, BaseException):
            logger.warning("Mini App home: mine.%s failed: %s", key, value)
            done[key] = None
    return {
        "days": MINE_DAYS,
        "engagement": done.get("engagement") or {"joined": 0, "skipped": 0},
        "wins": done.get("wins") or {"wins": 0, "claimed": 0},
        "recent_wins": [
            {"id": int(r["id"]), "chat": r.get("chat") or "?", "detected_at": r.get("detected_at"),
             "status": r.get("giveaway_status") or ""}
            for r in [row for row in done.get("recent") or [] if not row.get("duplicate_of")][:RECENT_WINS]
        ],
    }


@router.get("/api/app/home")
async def home(caller: Caller = Depends(current_caller)) -> dict:
    sections = {
        "giveaways": caller.may("giveaways"),
        "market": caller.may("market"),
        "debts": caller.is_admin,
        "accounts": caller.is_admin,
        "salary": await salary_visible(caller.tg_id, caller.role),
        "feed": caller.may("recent") or caller.may("search"),
        "search": caller.may("search"),
        "analytics": caller.may("stats") or caller.may("analytics"),
        "prefs": not caller.is_admin,
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
    else:
        tasks["me"] = member_profile(caller)
        tasks["mine"] = member_numbers(caller)
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
        # «Разбор»: the same three sources /api/app/triage lists, counted from
        # what this request already fetched.
        now = datetime.now()
        problems = sum(1 for a in state.accounts_state.values()
                       if account_problem(a, now, app_started_at=state.started_at) is not None)
        giveaway_rows = results["giveaways"][0] if results.get("giveaways") is not None else []
        counters["triage"] = (int(counters.get("debts") or 0) + problems
                              + sum(1 for r in giveaway_rows if not r.get("is_win")))
    payload["counters"] = counters
    if results.get("me"):
        payload["me"] = results["me"]
    if results.get("mine"):
        payload["mine"] = results["mine"]
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
