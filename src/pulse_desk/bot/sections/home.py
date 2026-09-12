"""Главный экран: домашняя карточка, сводка, помощь и сборка меню.

``menu_buttons`` живёт здесь, потому что состав главного меню — это уже
решение: какие разделы открыл ключ и виден ли раздел зарплат (он решается
меткой ключа, а не грантом — см. :mod:`.salary`).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import database
from database import get_market_history, giveaway_bucket_total

from ... import APP_VERSION
from ...analytics import build_analytics, build_home_counters
from ...app_ctx import state
from ..cards import home_card, summary_card
from ..keyboards import section_nav
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import fmt_dt, help_text, main_menu_buttons
from . import salary as salary_section


async def menu_buttons(sender_id: int, role: str, perms: Optional[dict] = None):
    """Главное меню с уже решённым вопросом про раздел зарплат."""
    return main_menu_buttons(role, perms, salary=await salary_section.visible(sender_id, role))


async def render_home(role: str) -> str:
    # Home prints three numbers. It used to pay for 16 analytics aggregates
    # plus a whole giveaway board (~33 queries over 3 connections) to get them.
    counters, urgent = await asyncio.gather(
        build_home_counters(),
        giveaway_bucket_total("need_action"),
    )
    last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
    if state.last_scan_status:
        last_scan = f"{last_scan} · {state.last_scan_status}"
    return home_card(
        role=role,
        new_pings=counters["new_pings"],
        urgent=urgent,
        accounts_online=counters["accounts_online"],
        accounts_total=len(state.accounts_state),
        last_scan=last_scan,
    )


async def render_summary() -> str:
    analytics = await build_analytics()
    market_rows = await get_market_history(limit=1)
    market = None
    if market_rows:
        m = market_rows[0]
        market = {
            "btc": m.get("bitcoin", {}).get("usd", 0),
            "eth": m.get("ethereum", {}).get("usd", 0),
            "ton": m.get("the-open-network", {}).get("usd", 0),
            "sol": m.get("solana", {}).get("usd", 0),
        }
    accounts_online = sum(1 for a in list(state.accounts_state.values()) if a.get("status") == "online")
    try:
        db_mb = database.DB_PATH.stat().st_size / 1024 / 1024 if database.DB_PATH.exists() else 0
    except Exception:
        db_mb = 0
    uptime_sec = int((datetime.now() - state.started_at).total_seconds())
    uptime = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
    last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
    system = {
        "version": APP_VERSION,
        "uptime": uptime,
        "db_mb": db_mb,
        "accounts_online": accounts_online,
        "accounts_total": len(state.accounts_state),
        "last_scan": f"{last_scan} · {state.last_scan_status or '—'}",
    }
    return summary_card(analytics=analytics, market=market, system=system)


async def handle_main(click: Click) -> None:
    await safe_edit(click.event, await render_home(click.role),
                    buttons=await menu_buttons(click.sender_id, click.role, click.perms))


async def handle_help(click: Click) -> None:
    salary = await salary_section.visible(click.sender_id, click.role)
    await safe_edit(click.event, help_text(click.role, click.perms, salary=salary),
                    buttons=section_nav(b"menu_help"))


async def handle_noop(click: Click) -> None:
    """Счётчик страниц и прочие неактивные кнопки: Telegram всё равно ждёт ответ."""
    await click.event.answer()


def register(router: CallbackRouter) -> None:
    router.exact("menu_main")(handle_main)
    router.exact("menu_help")(handle_help)
    router.exact("noop")(handle_noop)
