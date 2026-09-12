"""Личные уведомления участника: что ему приходит и с каким порогом.

Доступно любому участнику, но только в пределах того, что открыл ключ:
``allowed_pref_keys`` вычитает типы, которых в гранте нет, и выкидывает
агрегаты (дайджест) у ключа, привязанного к одному аккаунту — иначе одна сводка
показала бы чужие.
"""
from __future__ import annotations

from typing import Optional

from telethon import Button

from database import get_bot_member, set_bot_member_prefs

from ...bot_permissions import allowed_pref_keys, full_permissions
from ...bot_prefs import parse_member_prefs, render_member_prefs_text, toggle_member_pref
from ..pending import prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click

LABELS = {
    "mentions": ("Упоминания", b"pf_me"),
    "giveaways": ("Розыгрыши", b"pf_gw"),
    "wins": ("Победы", b"pf_wn"),
    "digest": ("Дайджест", b"pf_dg"),
}
TOGGLES = {
    "pf_mu": "muted",
    "pf_me": "mentions",
    "pf_gw": "giveaways",
    "pf_wn": "wins",
    "pf_dg": "digest",
}
OWNER_HAS_NO_PREFS = "Вы получаете уведомления как владелец — личные настройки не нужны"


def menu(prefs: dict, perms: Optional[dict] = None) -> tuple[str, list[list[Button]]]:
    """Personal toggles, limited to the notification types the key granted."""
    perms = perms or full_permissions()
    allowed = allowed_pref_keys(perms)
    toggles = [
        Button.inline(f"{'✅' if prefs.get(code) else '🔕'} {LABELS[code][0]}", LABELS[code][1])
        for code in allowed
    ]
    buttons = [[Button.inline("🔔 Включить всё" if prefs.get("muted") else "🔕 Отключить всё", b"pf_mu")]]
    buttons += [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    if "giveaways" in allowed:
        buttons.append([Button.inline("🎯 Мин. score розыгрышей…", b"pf_sc")])
    buttons.append([Button.inline("⬅️ Меню", b"menu_main")])
    return render_member_prefs_text(prefs, allowed, perms.get("accounts")), buttons


async def _consume_min_score(event, pending: dict, raw: str) -> None:
    # Personal member setting — the only pending input open to non-admins.
    member = await get_bot_member(event.sender_id)
    if not member:
        await event.respond("Личные настройки недоступны.")
        return
    try:
        value = max(0, min(100, int(raw)))
    except ValueError:
        await event.respond("❌ Нужно число 0–100 (0 — показывать все розыгрыши).")
        return
    prefs = parse_member_prefs(member.get("notification_prefs"))
    prefs["min_score"] = value
    await set_bot_member_prefs(event.sender_id, prefs)
    text, buttons = menu(prefs)
    await event.respond(f"✅ Мин. score: {value if value else 'любой'}\n\n{text}", buttons=buttons)


MIN_SCORE_INPUT = register_prompt(
    "min_score",
    "Пришлите минимальный score розыгрышей (0–100, 0 — показывать все).",
    _consume_min_score,
    admin=False,
)


async def handle(click: Click) -> None:
    event = click.event
    member = await get_bot_member(click.sender_id)
    if not member:
        await event.answer(OWNER_HAS_NO_PREFS, alert=True)
        return
    prefs = parse_member_prefs(member.get("notification_prefs"))
    if click.data == "pf_sc":
        await prompt_pending(event, MIN_SCORE_INPUT)
        return
    toggled = TOGGLES.get(click.data)
    if toggled:
        if toggled != "muted" and toggled not in allowed_pref_keys(click.perms):
            await event.answer("Этот тип уведомлений закрыт владельцем", alert=True)
            return
        prefs = toggle_member_pref(prefs, toggled)
        await set_bot_member_prefs(click.sender_id, prefs)
    text, buttons = menu(prefs, click.perms)
    await safe_edit(event, text, buttons=buttons)
    if toggled:
        await event.answer("Сохранено")


def register(router: CallbackRouter) -> None:
    router.group("pf", sep="_")(handle)
