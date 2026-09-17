"""Личные уведомления участника: что ему приходит и с каким порогом.

Доступно любому участнику, но только в пределах того, что открыл ключ:
``allowed_pref_keys`` вычитает типы, которых в гранте нет, и выкидывает
агрегаты (дайджест) у ключа, привязанного к одному аккаунту — иначе одна сводка
показала бы чужие.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from telethon import Button

from database import (
    account_win_stats, get_bot_member, member_engagement_since, set_bot_member_prefs,
)

from ...autoclean import label as autoclean_label, next_choice
from ...bot_membership import resolve_member_access
from ...bot_permissions import allowed_pref_keys, full_permissions
from ...bot_prefs import parse_member_prefs, render_member_prefs_text, toggle_member_pref
from ..cards import member_stats_card
from ..pending import InputRejected, prompt_pending, register_prompt
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
    buttons.append([Button.inline(f"🧹 Удалять упоминания: {autoclean_label(prefs.get('autoclean_hours'))}",
                                  b"pf_ac")])
    buttons.append([Button.inline("📊 Моя статистика", b"pf_st")])
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
        raise InputRejected("❌ Нужно число 0–100 (0 — показывать все розыгрыши).")
    prefs = parse_member_prefs(member.get("notification_prefs"))
    prefs["min_score"] = value
    await set_bot_member_prefs(event.sender_id, prefs)
    # The redrawn menu must stay cut to the key's grants, like every pf_* screen.
    _role, perms = await resolve_member_access(event.sender_id)
    text, buttons = menu(prefs, perms)
    await event.respond(f"✅ Мин. score: {value if value else 'любой'}\n\n{text}", buttons=buttons)


MIN_SCORE_INPUT = register_prompt(
    "min_score",
    "Пришлите минимальный score розыгрышей (0–100, 0 — показывать все).",
    _consume_min_score,
    admin=False,
)


async def show_my_stats(click: Click) -> None:
    """«📊 Моя статистика» — свои цифры участника, без чужих аккаунтов."""
    since = (datetime.now() - timedelta(days=30)).replace(microsecond=0).isoformat()
    total = await member_engagement_since(click.sender_id)
    month = await member_engagement_since(click.sender_id, since)
    accounts = list((click.perms or {}).get("accounts") or [])
    wins = await account_win_stats(accounts, since) if accounts else None
    await safe_edit(click.event, member_stats_card(total, month, wins, accounts),
                    buttons=[[Button.inline("⬅️ Мои уведомления", b"pf_back")]])


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
    if click.data == "pf_st":
        await show_my_stats(click)
        return
    if click.data == "pf_ac":
        prefs = dict(prefs, autoclean_hours=next_choice(prefs.get("autoclean_hours")))
        await set_bot_member_prefs(click.sender_id, prefs)
        text, buttons = menu(prefs, click.perms)
        await safe_edit(event, text, buttons=buttons)
        await event.answer(f"Упоминания: {autoclean_label(prefs['autoclean_hours'])}")
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
