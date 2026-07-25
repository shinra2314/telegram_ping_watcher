"""Inline-keyboard builders for the bot UI."""
from __future__ import annotations

from telethon import Button

from ..bot_permissions import (
    ALL_FEATURES,
    ALL_NOTIFY,
    DELAY_PRESETS,
    FEATURES,
    NOTIFY_TYPES,
    format_delay,
    permission_delay_minutes,
)


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
    """Keys panel: revoke + delete per key, then create + nav.

    Revoke keeps the row (the link stops working); delete erases it. Neither
    touches people who already joined — block those in the members panel.
    """
    rows: list[list[Button]] = [
        [
            Button.inline(f"🚫 {label}", f"key:rm:{kid}".encode()),
            Button.inline("🗑", f"key:del:{kid}".encode()),
        ]
        for kid, label in items
    ]
    rows.append([Button.inline("➕ Создать ключ", b"adm:newkey"),
                 Button.inline("⚡ Премиум-ключ", b"adm:newkeyp")])
    if items:
        rows.append([Button.inline(f"⚙️ #{kid}", f"key:{kid}".encode()) for kid, _ in items[:4]])
    rows.append([Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_keys")])
    return rows


KEY_PANEL_ACCOUNTS_PAGE = 8


def key_panel_keyboard(key_id: int, perms: dict, account_total: int) -> list[list[Button]]:
    """Root of the per-key control panel: what a guest sees, gets, and when."""
    granted_accounts = perms.get("accounts") or []
    scope = f"{len(granted_accounts)}/{account_total}" if granted_accounts else f"все ({account_total})"
    return [
        [
            Button.inline(f"📂 Разделы ({len(perms.get('features') or [])}/{len(ALL_FEATURES)})", f"key:f:{key_id}".encode()),
            Button.inline(f"🔔 Уведомления ({len(perms.get('notify') or [])}/{len(ALL_NOTIFY)})", f"key:n:{key_id}".encode()),
        ],
        [Button.inline(f"👤 Аккаунты · {scope}", f"key:a:{key_id}".encode())],
        [Button.inline(f"⏱ Задержка · {format_delay(permission_delay_minutes(perms))}", f"key:d:{key_id}".encode())],
        [
            Button.inline("🔗 Ссылка", f"key:link:{key_id}".encode()),
            Button.inline("🗑 Удалить", f"key:del:{key_id}".encode()),
        ],
        [Button.inline("⬅️ Ключи", b"menu_keys")],
    ]


def key_features_keyboard(key_id: int, perms: dict) -> list[list[Button]]:
    granted = set(perms.get("features") or [])
    toggles = [
        Button.inline(f"{'✅' if code in granted else '🔒'} {label}", f"key:f:{key_id}:{code}".encode())
        for code, (label, _hint) in FEATURES.items()
    ]
    rows = [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    rows.append([
        Button.inline("☑️ Все", f"key:f:{key_id}:_all".encode()),
        Button.inline("🔒 Никакие", f"key:f:{key_id}:_none".encode()),
    ])
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
    return rows


def key_notify_keyboard(key_id: int, perms: dict) -> list[list[Button]]:
    granted = set(perms.get("notify") or [])
    toggles = [
        Button.inline(f"{'✅' if code in granted else '🔕'} {label}", f"key:n:{key_id}:{code}".encode())
        for code, (label, _hint) in NOTIFY_TYPES.items()
    ]
    rows = [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    rows.append([
        Button.inline("☑️ Все", f"key:n:{key_id}:_all".encode()),
        Button.inline("🔕 Никакие", f"key:n:{key_id}:_none".encode()),
    ])
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
    return rows


def key_accounts_keyboard(key_id: int, perms: dict, accounts: list[str], page: int = 0) -> list[list[Button]]:
    """One toggle per tracked username; accounts are addressed by index.

    Callback data caps at 64 bytes, so the index into `accounts` travels instead
    of the name itself. The caller rebuilds the same list on both sides.
    """
    granted = {name.lower() for name in (perms.get("accounts") or [])}
    pages = max(1, (len(accounts) + KEY_PANEL_ACCOUNTS_PAGE - 1) // KEY_PANEL_ACCOUNTS_PAGE)
    page = max(0, min(pages - 1, page))
    start = page * KEY_PANEL_ACCOUNTS_PAGE
    chunk = accounts[start:start + KEY_PANEL_ACCOUNTS_PAGE]
    toggles = [
        Button.inline(
            f"{'✅' if (not granted or name.lower() in granted) else '⬜'} @{name}"[:28],
            f"key:a:{key_id}:{start + i}".encode(),
        )
        for i, name in enumerate(chunk)
    ]
    rows = [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    if pages > 1:
        nav: list[Button] = []
        if page > 0:
            nav.append(Button.inline("◀️", f"key:a:{key_id}:_p{page - 1}".encode()))
        nav.append(Button.inline(f"{page + 1}/{pages}", b"noop"))
        if page < pages - 1:
            nav.append(Button.inline("▶️", f"key:a:{key_id}:_p{page + 1}".encode()))
        rows.append(nav)
    rows.append([Button.inline("☑️ Все аккаунты", f"key:a:{key_id}:_all".encode())])
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
    return rows


def key_delay_keyboard(key_id: int, perms: dict) -> list[list[Button]]:
    current = permission_delay_minutes(perms)
    presets = [
        Button.inline(
            ("🔘 " if minutes == current else "") + ("мгновенно" if minutes == 0 else f"{minutes} мин"),
            f"key:d:{key_id}:{minutes}".encode(),
        )
        for minutes in DELAY_PRESETS
    ]
    rows = [presets[i:i + 3] for i in range(0, len(presets), 3)]
    rows.append([Button.inline("✏️ Своё значение…", f"key:d:{key_id}:_x".encode())])
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
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
