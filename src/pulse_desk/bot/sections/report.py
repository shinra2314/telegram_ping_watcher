"""🖼 Отчёт: неделя или месяц одной картинкой — победы, доля забранного, деньги.

Владельческий раздел: в отчёте суммы незабранного и зарплаты по книге.
`rp:w` / `rp:m` — текущие 7 дней / месяц до сегодня, `rp:pw` / `rp:pm` — прошлая
полная неделя / прошлый месяц (то же, что уходит по расписанию).
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from telethon import Button

from database import get_debt_board, get_pings

from ...app_ctx import state
from ...prize_value import totals, value_by_account
from ...report import ReportData, build_report, period, report_text
from ..media import cards_dir, show_screen
from ..render.screens import build_report_card
from ..router import CallbackRouter, Click
from .market import latest_snapshot

VIEWS = {"w": ("week", False), "m": ("month", False), "pw": ("week", True), "pm": ("month", True)}
PINGS_LIMIT = 20000


async def collect(kind: str, previous: bool, now: Optional[datetime] = None) -> ReportData:
    now = now or datetime.now()
    start, end, label = period(kind, now, previous=previous)
    length = end - start
    pings = await get_pings(limit=PINGS_LIMIT, date_from=start.isoformat(), date_to=end.isoformat())
    prev = await get_pings(limit=PINGS_LIMIT, date_from=(start - length).isoformat(), date_to=start.isoformat())
    board = await get_debt_board(state.ping_usernames, limit=500)
    unclaimed = totals(value_by_account(board.get("rows") or [], state.ping_usernames, await latest_snapshot()))
    return build_report(kind, start, end, label, pings, prev, unclaimed=unclaimed, book=state.salary_book)


async def render(kind: str, previous: bool) -> tuple[str, Optional[str]]:
    """(caption, card path or None)."""
    data = await collect(kind, previous)
    path = cards_dir() / f"report_{kind}_{'prev' if previous else 'now'}_{datetime.now():%Y%m%d%H%M%S}.png"
    card = await asyncio.to_thread(build_report_card, data, path)
    return report_text(data), card


def keyboard(active: str) -> list[list[Button]]:
    def mark(code: str, label: str) -> Button:
        return Button.inline(f"▸{label}" if code == active else label, f"rp:{code}".encode())

    return [
        [mark("w", "7 дней"), mark("m", "Месяц")],
        [mark("pw", "Прошлая неделя"), mark("pm", "Прошлый месяц")],
        [Button.inline("⬅️ Управление", b"adm:home")],
    ]


async def handle(click: Click) -> None:
    code = click.arg(1, "w")
    kind, previous = VIEWS.get(code, VIEWS["w"])
    await click.event.answer("Рисую отчёт…")
    caption, card = await render(kind, previous)
    await show_screen(click.event, caption, buttons=keyboard(code if code in VIEWS else "w"), image=card)


def register(router: CallbackRouter) -> None:
    router.group("rp", admin=True)(handle)
