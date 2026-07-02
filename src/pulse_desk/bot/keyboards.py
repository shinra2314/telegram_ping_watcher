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


def _feed_cb(active: str, page: int) -> bytes:
    """`mon:feed:<filter>` for page 1, `mon:feed:<filter>:<page>` deeper."""
    return (f"mon:feed:{active}" if page <= 1 else f"mon:feed:{active}:{page}").encode()


def feed_keyboard(
    items: list[tuple[int, str]],
    active: str,
    page: int = 1,
    has_more: bool = False,
) -> list[list[Button]]:
    """Monitoring feed: one row per ping, filters, optional pager, home/refresh."""
    rows: list[list[Button]] = [
        [Button.inline(label, f"mon:open:{pid}".encode())] for pid, label in items
    ]
    filt = [
        Button.inline(f"▸{lbl}" if code == active else lbl, f"mon:feed:{code}".encode())
        for code, lbl in MON_FILTERS
    ]
    rows.append(filt)
    if page > 1 or has_more:
        pager: list[Button] = []
        if page > 1:
            pager.append(Button.inline("◀️ Новее", _feed_cb(active, page - 1)))
        pager.append(Button.inline(f"· {page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("Старее ▶️", _feed_cb(active, page + 1)))
        rows.append(pager)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", _feed_cb(active, page)),
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


def keys_keyboard(items: list[tuple[int, str]] = ()) -> list[list[Button]]:
    """Keys panel: one revoke row per key, then create + nav."""
    rows: list[list[Button]] = [
        [Button.inline(f"🗑 {label}", f"key:rm:{kid}".encode())] for kid, label in items
    ]
    rows.append([Button.inline("➕ Создать ключ", b"adm:newkey")])
    rows.append([Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_keys")])
    return rows


def scan_panel_keyboard(running: bool) -> list[list[Button]]:
    """Scan panel: start when idle, live refresh while running."""
    rows: list[list[Button]] = []
    if not running:
        rows.append([Button.inline("▶️ Запустить скан", b"scan:start")])
    rows.append([Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_scan")])
    return rows


def restart_confirm_keyboard() -> list[list[Button]]:
    """Two-step restart: explicit confirm or bail back to the hub."""
    return [
        [Button.inline("✅ Да, перезапустить", b"adm:restart:go")],
        [Button.inline("✖️ Отмена", b"adm:home")],
    ]


def logs_keyboard() -> list[list[Button]]:
    return [[Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_logs")]]
