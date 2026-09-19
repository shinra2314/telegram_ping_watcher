"""Read-only reporting: the stats card, the 5-tab analytics report, the status card.

All three are aggregates over data the app already keeps, so nothing here
writes or touches Telegram — which is why they are the cheapest screens in the
bot and the safest to hand to a guest key.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import database

from ... import APP_VERSION
from ...analytics import build_analytics, build_detailed_analytics, build_panel_report
from ...app_ctx import state
from ..cards import analytics_card, member_analytics_card, member_summary_card
from ..chrome import empty
from ..keyboards import analytics_keyboard, section_nav
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, MEMBER_ANALYTICS_TABS, fmt_dt
from .giveaways import visible_accounts


async def member_report(perms: dict) -> dict:
    """Отчёт держателя ключа — только по аккаунтам его ключа.

    Тот же ``build_panel_report``, по которому считает Mini App: у бота и панели
    один ответ на вопрос «сколько у меня», а числа по всей базе остаются
    владельцу (правило владельца, 18.09).
    """
    return await build_panel_report(list(perms.get("accounts") or []), visible_accounts(perms))


async def render_stats(perms: Optional[dict] = None, is_admin: bool = True) -> str:
    """Счётчики: гостю — по его аккаунтам, владельцу — по всей базе."""
    if not is_admin:
        return member_summary_card(await member_report(perms or {}), visible_accounts(perms or {}))
    analytics = await build_analytics()
    return (
        "📊 **Статистика**\n"
        f"{DIV}\n"
        f"📨 Всего записей: `{analytics['total_pings']}`\n"
        f"🆕 Новых: `{analytics['new_pings']}`\n"
        f"⭐ Избранных: `{analytics['favorites']}`\n"
        f"🛰 Аккаунтов онлайн: `{analytics['accounts_online']}`"
    )


async def render_report(tab: str = "sum", perms: Optional[dict] = None, is_admin: bool = True):
    """Analytics report page. Both queries are read-only aggregates."""
    if not is_admin:
        report = await member_report(perms or {})
        tab = tab if tab in dict(MEMBER_ANALYTICS_TABS) else "sum"
        return (member_analytics_card(tab, report, visible_accounts(perms or {})),
                analytics_keyboard(tab, MEMBER_ANALYTICS_TABS))
    analytics, detailed = await asyncio.gather(build_analytics(), build_detailed_analytics())
    return analytics_card(tab, analytics=analytics, detailed=detailed), analytics_keyboard(tab)


def member_status(perms: dict) -> str:
    """Состояние только тех аккаунтов, которые открыл ключ.

    Версия, uptime, размер базы и список job'ов — хозяйство владельца: гостю
    они ничего не решают, а про инфраструктуру рассказывают всё.
    """
    allowed = {name.lower() for name in visible_accounts(perms)}
    whitelist = {str(name).lower().lstrip("@") for name in (perms.get("accounts") or [])}
    rows = [
        acc for acc in list(state.accounts_state.values())
        if not whitelist or str(acc.get("username") or "").lower() in allowed
    ]
    lines = [
        f"  {'🟢' if a.get('status') == 'online' else '🔴'} "
        f"`@{a.get('username') or a.get('session_name', '?')}` — {a.get('status', 'unknown')}"
        for a in rows
    ]
    online = sum(1 for a in rows if a.get("status") == "online")
    return "\n".join([
        "🛰 **Статус**",
        DIV,
        f"🛰 Аккаунты: `{online}/{len(rows)}` online",
        "",
        *(lines or [empty("Ваших аккаунтов в списке нет.")]),
    ])


async def render_status(perms: Optional[dict] = None, is_admin: bool = True) -> str:
    if not is_admin:
        return member_status(perms or {})
    accounts_online = sum(1 for acc in list(state.accounts_state.values())
                          if acc.get("status") == "online")
    account_lines = [
        f"  {'🟢' if a.get('status') == 'online' else '🔴'} `{a.get('session_name', '?')}` — {a.get('status', 'unknown')}"
        for a in list(state.accounts_state.values())
    ]
    try:
        db_size_mb = database.DB_PATH.stat().st_size / 1024 / 1024 if database.DB_PATH.exists() else 0
    except Exception:
        db_size_mb = 0
    uptime_sec = int((datetime.now() - state.started_at).total_seconds())
    uptime_str = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
    running_jobs = sorted(name for name, task in state.background_tasks.items() if not task.done())
    last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
    return (
        f"🛰 **Статус** · `v{APP_VERSION}`\n"
        f"{DIV}\n"
        f"⏱ Uptime: `{uptime_str}`\n"
        f"💾 База: `{db_size_mb:.1f} MB`\n"
        f"🛰 Аккаунты: `{accounts_online}/{len(state.accounts_state)}` online\n"
        f"🔄 Скан: `{last_scan}` · {state.last_scan_status or '—'}\n"
        f"⚙️ Jobs ({len(running_jobs)}): `{', '.join(running_jobs) or '—'}`\n\n"
        "👤 **Аккаунты**\n" + ("\n".join(account_lines) if account_lines else "  __нет аккаунтов__")
    )


async def handle_report(click: Click) -> None:
    text, kb = await render_report(click.arg(1, "sum"), click.perms, click.role == "admin")
    await safe_edit(click.event, text, buttons=kb, link_preview=False)


async def handle_stats(click: Click) -> None:
    text = await render_stats(click.perms, click.role == "admin")
    await safe_edit(click.event, text, buttons=section_nav(b"menu_stats"))


async def handle_status(click: Click) -> None:
    text = await render_status(click.perms, click.role == "admin")
    await safe_edit(click.event, text, buttons=section_nav(b"menu_status"))


def register(router: CallbackRouter) -> None:
    router.group("an", feature="analytics")(handle_report)
    router.exact("menu_stats", feature="stats")(handle_stats)
    router.exact("menu_status", feature="status")(handle_status)
