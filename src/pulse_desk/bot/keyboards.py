"""Inline-keyboard builders for the bot UI."""
from __future__ import annotations

from telethon import Button


def section_nav(refresh_cb: bytes) -> list[list[Button]]:
    """Footer for a section view: back to home + refresh.

    `refresh_cb` is the view's own `menu_<x>` callback — those handlers already
    re-render the view, so refresh needs no new dispatch.
    """
    return [[Button.inline("⬅️ Домой", b"menu_main"), Button.inline("🔄 Обновить", refresh_cb)]]


def back_home() -> list[list[Button]]:
    """Footer for views with no refresh target (e.g. search results)."""
    return [[Button.inline("⬅️ Домой", b"menu_main")]]


MON_FILTERS = [("all", "Все"), ("important", "Важные"), ("check", "Чеки"), ("win", "Победы")]


def feed_keyboard(items: list[tuple[int, str]], active: str) -> list[list[Button]]:
    """Monitoring feed: one row per ping, a filter row, then home/refresh."""
    rows: list[list[Button]] = [
        [Button.inline(label, f"mon:open:{pid}".encode())] for pid, label in items
    ]
    filt = [
        Button.inline(f"▸{lbl}" if code == active else lbl, f"mon:feed:{code}".encode())
        for code, lbl in MON_FILTERS
    ]
    rows.append(filt)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", f"mon:feed:{active}".encode()),
    ])
    return rows


def ping_card_keyboard(ping_id: int, is_admin: bool) -> list[list[Button]]:
    """Drilldown actions: owner gets ⭐/read; everyone gets back/refresh."""
    rows: list[list[Button]] = []
    if is_admin:
        rows.append([
            Button.inline("⭐ В избранное", f"ping:fav:{ping_id}".encode()),
            Button.inline("✓ Прочитано", f"ping:read:{ping_id}".encode()),
        ])
    rows.append([
        Button.inline("⬅️ Назад", b"mon:feed:all"),
        Button.inline("🔄 Обновить", f"mon:open:{ping_id}".encode()),
    ])
    return rows


def giveaway_feed_keyboard(items: list[tuple[int, str]]) -> list[list[Button]]:
    """Giveaways section: one row per candidate, then home/refresh."""
    rows: list[list[Button]] = [
        [Button.inline(label, f"gw:open:{pid}".encode())] for pid, label in items
    ]
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", b"menu_giveaways"),
    ])
    return rows


def giveaway_card_keyboard(ping_id: int) -> list[list[Button]]:
    """Read-only candidate card: back to the section + refresh."""
    return [[
        Button.inline("⬅️ Назад", b"menu_giveaways"),
        Button.inline("🔄 Обновить", f"gw:open:{ping_id}".encode()),
    ]]


def management_grid() -> list[list[Button]]:
    return [
        [Button.inline("⚙️ Настройки", b"st"), Button.inline("🔑 Ключи", b"menu_keys")],
        [Button.inline("👥 Люди", b"adm:members"), Button.inline("⏰ Доступ", b"adm:access")],
        [Button.inline("🔄 Скан", b"menu_scan"), Button.inline("📜 Логи", b"menu_logs")],
        [Button.inline("♻️ Рестарт", b"menu_restart"), Button.inline("⬅️ Домой", b"menu_main")],
    ]


def members_list_keyboard(items: list[tuple[int, str]]) -> list[list[Button]]:
    rows: list[list[Button]] = [
        [Button.inline(label, f"mem:open:{tg}".encode())] for tg, label in items
    ]
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"adm:members"),
    ])
    return rows


def member_card_keyboard(tg: int, blocked: bool) -> list[list[Button]]:
    toggle = (
        Button.inline("✅ Разблокировать", f"mem:unblock:{tg}".encode())
        if blocked else
        Button.inline("🚫 Заблокировать", f"mem:block:{tg}".encode())
    )
    return [
        [toggle, Button.inline("⏰ Доступ", f"mem:access:{tg}".encode())],
        [Button.inline("⬅️ Назад", b"adm:members"), Button.inline("🔄 Обновить", f"mem:open:{tg}".encode())],
    ]


def member_access_keyboard(tg: int) -> list[list[Button]]:
    return [
        [Button.inline("🔴 Закрыть", f"acc:close:{tg}".encode()),
         Button.inline("🔴 На 2ч", f"acc:close2h:{tg}".encode()),
         Button.inline("🟢 Открыть", f"acc:open:{tg}".encode())],
        [Button.inline("🔴 До утра", f"acc:morning:{tg}".encode()),
         Button.inline("↩️ Отмена", f"acc:undo:{tg}".encode()),
         Button.inline("🧾 История", f"acc:log:{tg}".encode())],
        [Button.inline("⬅️ Назад", f"mem:open:{tg}".encode()),
         Button.inline("🔄 Обновить", f"mem:access:{tg}".encode())],
    ]


def keys_keyboard() -> list[list[Button]]:
    return [
        [Button.inline("➕ Создать ключ", b"adm:newkey")],
        [Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_keys")],
    ]
