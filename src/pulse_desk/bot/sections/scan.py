"""Скан истории: панель и запуск.

Запуск — фоновая задача под надзором (``start_background_task``), поэтому скан,
начатый из бота и умерший на полпути, виден в ``/api/health``, а не теряется.
"""
from __future__ import annotations

from ...app_ctx import state
from ...common import start_background_task
from ...scan_engine import full_history_scan
from ..cards import scan_card
from ..keyboards import scan_panel_keyboard
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import fmt_dt


def render_panel():
    last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
    if state.last_scan_status:
        last_scan = f"{last_scan} · {state.last_scan_status}"
    running = state.scan_lock.locked()
    status = dict(state.scan_status)
    status["running"] = running
    return scan_card(status, last_scan), scan_panel_keyboard(running)


def start_if_idle() -> bool:
    if state.scan_lock.locked():
        return False
    # Tracked, so a scan started from the bot that dies is logged and visible
    # in /api/health instead of vanishing into a stray task.
    start_background_task("bot-scan", full_history_scan())
    return True


async def handle(click: Click) -> None:
    if click.arg(1) == "start":
        started = start_if_idle()
        await click.event.answer("🔄 Скан запущен" if started else "Скан уже идёт")
    text, kb = render_panel()
    await safe_edit(click.event, text, buttons=kb)


async def handle_menu(click: Click) -> None:
    text, kb = render_panel()
    await safe_edit(click.event, text, buttons=kb)


def register(router: CallbackRouter) -> None:
    router.group("scan", admin=True)(handle)
    router.exact("menu_scan", admin=True)(handle_menu)
