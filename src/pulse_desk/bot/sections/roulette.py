"""Yobo-roulette reminder: panel, check-in, skip, on/off.

The alarm time *is* the previous day's last click, reported back by the owner —
so this section has three ways in (the ``rl:in`` button, ``/roulette 21:47``,
and a bare ``21:47`` while a nudge is waiting) that all converge on
``checkin``. Owner-only: a guest key never opens it.
"""
from __future__ import annotations

from datetime import datetime

from ... import watch_settings as ws
from ...roulette import apply_checkin, checkin_moment, skip_today
from ..cards import roulette_card
from ..keyboards import roulette_panel_keyboard
from ..pending import prompt_pending, register_prompt
from ..persist import save_roulette
from ..reply import safe_edit
from ..router import Click, CallbackRouter



async def render_panel() -> tuple[str, list]:
    cfg = await ws.load_roulette_settings()
    return roulette_card(cfg, datetime.now()), roulette_panel_keyboard(cfg)


async def checkin(event, when: datetime) -> None:
    """Record the reported click time and show the refreshed panel."""
    cfg = apply_checkin(await ws.load_roulette_settings(), when)
    await save_roulette(cfg)
    text, kb = await render_panel()
    await event.respond(f"✅ Проклик в {cfg['time']} записан.\n\n{text}", buttons=kb)


async def _consume_time(event, pending: dict, raw: str) -> None:
    moment = checkin_moment(datetime.now(), raw)
    if moment is None:
        await event.respond("❌ Формат: `21:47`. Попробуйте ещё раз через меню.")
        return
    await checkin(event, moment)


TIME_INPUT = register_prompt(
    "roulette_time", "Пришлите время проклика последнего аккаунта: `21:47`.", _consume_time)


async def handle(click: Click) -> None:
    action = click.data.partition(":")[2]
    if action == "in":
        await prompt_pending(click.event, TIME_INPUT)
        return
    now = datetime.now()
    cfg = await ws.load_roulette_settings()
    if action == "now":
        cfg = apply_checkin(cfg, now)
        await save_roulette(cfg)
        await click.event.answer(f"Записал: {cfg['time']}")
    elif action == "skip":
        cfg = skip_today(cfg, now)
        await save_roulette(cfg)
        await click.event.answer("Сегодня больше не напомню")
    elif action == "tog":
        cfg = dict(cfg)
        cfg["enabled"] = not cfg.get("enabled")
        await save_roulette(cfg)
        await click.event.answer(
            "Напоминание включено" if cfg["enabled"] else "Напоминание выключено")
    await safe_edit(click.event, roulette_card(cfg, now), buttons=roulette_panel_keyboard(cfg))


def register(router: CallbackRouter) -> None:
    router.group("rl", admin=True)(handle)
