"""Inline-keyboard builders for the bot UI."""
from __future__ import annotations

from typing import Optional, Sequence

from telethon import Button
from telethon.tl.types import KeyboardButtonWebView

from ..app_ctx import state
from ..bot_permissions import (
    ALL_FEATURES,
    ALL_NOTIFY,
    DELAY_PRESETS,
    FEATURES,
    NOTIFY_TYPES,
    format_delay,
    permission_delay_minutes,
)
from .views import (
    ALL_ACCOUNTS,
    ANALYTICS_TABS,
    EXPIRY_PRESETS,
    GIVEAWAY_SORTS,
    GiveawayFilter,
    account_label,
    format_expiry,
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


def webapp_row(label: str, path: str = "/app") -> list[Button]:
    """A one-button row opening the Mini App, or an empty row when it is down.

    The URL is read at send time rather than cached, because a quick tunnel
    hands out a new hostname on every restart. With no tunnel the row is empty
    and the caller's keyboard is simply one row shorter — the inline UI below it
    stays fully usable, which is the whole point of the fallback.

    Custom emoji and custom button shapes are impossible in a Telegram keyboard
    (button text is a plain string with no entities), so this button is the only
    way into a surface that has them.

    Telethon 1.43 counts ``KeyboardButtonWebView`` among its inline button
    types, so the raw TL object goes straight into ``buttons=``.
    """
    base = (state.public_url or "").rstrip("/")
    if not base:
        return []
    return [KeyboardButtonWebView(text=label, url=f"{base}{path}")]


# Converter shortcuts shown under the landing card, as (src, dst) pairs.
CONVERT_PRESETS: tuple[tuple[str, str], ...] = (
    ("USD", "UAH"), ("EUR", "UAH"), ("USDT", "UAH"), ("BTC", "USD"), ("TON", "USD"),
)


def _convert_cb(amount: float, src: str, dst: str) -> bytes:
    """`cv:p:<amount>:<src>:<dst>` — a re-runnable conversion, ≤64 bytes."""
    return f"cv:p:{amount:g}:{src}:{dst}".encode()


def converter_keyboard() -> list[list[Button]]:
    """Landing screen: preset pairs + free-text input."""
    presets = [Button.inline(f"{src}→{dst}", _convert_cb(1, src, dst)) for src, dst in CONVERT_PRESETS]
    rows = [presets[i:i + 3] for i in range(0, len(presets), 3)]
    rows.append([Button.inline("✍️ Своя сумма", b"cv:in")])
    rows.append([Button.inline("⬅️ Домой", b"menu_main"), Button.inline("💹 Курсы", b"menu_market")])
    return rows


def conversion_keyboard(amount: float, src: str, dst: str) -> list[list[Button]]:
    """Result screen: flip the pair, recompute, or ask for another sum."""
    return [
        [Button.inline("🔁 Наоборот", _convert_cb(amount, dst, src)),
         Button.inline("🔄 Обновить", _convert_cb(amount, src, dst))],
        [Button.inline("✍️ Другая сумма", b"cv:in"), Button.inline("💱 Конвертер", b"cv")],
        [Button.inline("⬅️ Домой", b"menu_main")],
    ]


def market_keyboard() -> list[list[Button]]:
    """Rates view footer — the converter is one tap from the prices."""
    return [
        [Button.inline("💱 Конвертер", b"cv")],
        [Button.inline("⬅️ Домой", b"menu_main"), Button.inline("🔄 Обновить", b"menu_market")],
    ]


MON_FILTERS = [("all", "Все"), ("important", "Важные"), ("giveaway", "Розыгрыши"), ("win", "Победы")]


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


def _giveaway_feed_cb(state: GiveawayFilter, page: Optional[int] = None) -> bytes:
    """`menu_giveaways` for an unfiltered page 1, `gw:f:<sort>:<wins>:<acct>:<page>` otherwise.

    The plain form keeps the section's entry point (home button, /giveaways,
    back-from-card) on one stable callback no matter what the feed grows into.
    """
    state = state if page is None else state._replace(page=page)
    if state.page <= 1 and state.sort == "d" and not state.wins and state.account == ALL_ACCOUNTS:
        return b"menu_giveaways"
    return state.cb()


def _giveaway_accounts_cb(state: GiveawayFilter, page: int = 0) -> bytes:
    """`gw:a:<sort>:<wins>:<account>:<picker page>` — the picker keeps the feed's state."""
    return f"gw:a:{state.sort}:{1 if state.wins else 0}:{state.account}:{max(0, page)}".encode()


def _giveaway_open_cb(ping_id: int, state: GiveawayFilter) -> bytes:
    """`gw:open:<id>[:<sort>:<wins>:<acct>:<page>]` — the card remembers the list."""
    tail = f":{state.sort}:{1 if state.wins else 0}:{state.account}:{max(1, state.page)}"
    return f"gw:open:{ping_id}{tail}".encode()


def giveaway_feed_keyboard(
    items: list[tuple[int, str]],
    page: int = 1,
    has_more: bool = False,
    state: Optional[GiveawayFilter] = None,
    accounts: Sequence[str] = (),
) -> list[list[Button]]:
    """Giveaways section: candidates, sort/wins/account filters, pager, home/refresh."""
    state = (state or GiveawayFilter())._replace(page=max(1, page))
    rows: list[list[Button]] = [
        [Button.inline(label, _giveaway_open_cb(pid, state))] for pid, label in items
    ]
    rows.append([
        Button.inline(f"▸{label}" if code == state.sort else label, state.with_(sort=code).cb())
        for code, _db, label in GIVEAWAY_SORTS
    ])
    rows.append([
        Button.inline(
            ("▸🏆 Победы" if state.wins else "🏆 Победы"),
            state.with_(wins=not state.wins).cb(),
        ),
        Button.inline(f"👤 {account_label(accounts, state.account)}"[:28], _giveaway_accounts_cb(state)),
    ])
    if page > 1 or has_more:
        pager: list[Button] = []
        if page > 1:
            pager.append(Button.inline("◀️ Новее", _giveaway_feed_cb(state, page - 1)))
        pager.append(Button.inline(f"· {page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("Старее ▶️", _giveaway_feed_cb(state, page + 1)))
        rows.append(pager)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", _giveaway_feed_cb(state)),
    ])
    return rows


GIVEAWAY_ACCOUNTS_PAGE = 8


def giveaway_accounts_keyboard(
    accounts: Sequence[str],
    counts: dict,
    state: Optional[GiveawayFilter] = None,
    page: int = 0,
) -> list[list[Button]]:
    """Account picker for the giveaways feed — one row per tracked username.

    Accounts travel as an index into `accounts`, rebuilt identically by the
    caller on both render and click (callback data is capped at 64 bytes).
    """
    state = state or GiveawayFilter()
    pages = max(1, (len(accounts) + GIVEAWAY_ACCOUNTS_PAGE - 1) // GIVEAWAY_ACCOUNTS_PAGE)
    page = max(0, min(pages - 1, page))
    start = page * GIVEAWAY_ACCOUNTS_PAGE
    rows: list[list[Button]] = [
        [Button.inline(
            f"🏆 {int((counts.get(name.lower()) or {}).get('wins', 0))}"
            f" · 🎁 {int((counts.get(name.lower()) or {}).get('giveaways', 0))}  @{name}"[:48],
            state.with_(account=start + i).cb(),
        )]
        for i, name in enumerate(accounts[start:start + GIVEAWAY_ACCOUNTS_PAGE])
    ]
    if pages > 1:
        nav: list[Button] = []
        if page > 0:
            nav.append(Button.inline("◀️", _giveaway_accounts_cb(state, page - 1)))
        nav.append(Button.inline(f"{page + 1}/{pages}", b"noop"))
        if page < pages - 1:
            nav.append(Button.inline("▶️", _giveaway_accounts_cb(state, page + 1)))
        rows.append(nav)
    rows.append([Button.inline("👥 Все аккаунты", state.with_(account=ALL_ACCOUNTS).cb())])
    rows.append([Button.inline("⬅️ Назад", _giveaway_feed_cb(state, 1))])
    return rows


def giveaway_card_keyboard(ping_id: int, state: Optional[GiveawayFilter] = None) -> list[list[Button]]:
    """Read-only candidate card: back to the list it came from + refresh."""
    state = state or GiveawayFilter()
    return [[
        Button.inline("⬅️ Назад", _giveaway_feed_cb(state)),
        Button.inline("🔄 Обновить", _giveaway_open_cb(ping_id, state)),
    ]]


def analytics_keyboard(active: str) -> list[list[Button]]:
    """Tab strip for the analytics report + home/refresh footer."""
    tabs = [
        Button.inline(f"▸{label}" if code == active else label, f"an:{code}".encode())
        for code, label in ANALYTICS_TABS
    ]
    rows = [tabs[i:i + 3] for i in range(0, len(tabs), 3)]
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", f"an:{active}".encode()),
    ])
    return rows


def management_grid() -> list[list[Button]]:
    return [
        [Button.inline("⚙️ Настройки", b"st"), Button.inline("🔑 Ключи", b"menu_keys")],
        [Button.inline("👥 Люди", b"adm:members"), Button.inline("⏰ Доступ", b"adm:access")],
        [Button.inline("🔄 Скан", b"menu_scan"), Button.inline("📜 Логи", b"menu_logs")],
        [Button.inline("🎰 Рулетка", b"rl"), Button.inline("♻️ Рестарт", b"menu_restart")],
        [Button.inline("⬅️ Домой", b"menu_main")],
    ]


def roulette_reminder_keyboard() -> list[list[Button]]:
    """Buttons under the daily nudge: check in now, type a time, or skip."""
    return [
        [Button.inline("✅ Прокликал (сейчас)", b"rl:now")],
        [Button.inline("⏱ Указать время", b"rl:in"), Button.inline("⏭ Не сегодня", b"rl:skip")],
    ]


def roulette_panel_keyboard(cfg: dict) -> list[list[Button]]:
    """Owner panel for the roulette reminder."""
    toggle = "🔕 Выключить" if cfg.get("enabled") else "🔔 Включить"
    return [
        [Button.inline("✅ Прокликал (сейчас)", b"rl:now")],
        [Button.inline("⏱ Указать время", b"rl:in"), Button.inline(toggle, b"rl:tog")],
        [Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"rl")],
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


KEYS_LIST_LIMIT = 12


def keys_keyboard(items: list[tuple[int, str]] = ()) -> list[list[Button]]:
    """Keys panel: one row per key opening its control panel, then create + nav.

    Every lifecycle action (rename, expiry, revoke, delete) lives inside that
    panel, so the list stays a list no matter how many keys exist.
    """
    rows: list[list[Button]] = [
        [Button.inline(f"⚙️ {label}"[:48], f"key:{kid}".encode())]
        for kid, label in list(items)[:KEYS_LIST_LIMIT]
    ]
    rows.append([Button.inline("➕ Создать ключ", b"adm:newkey"),
                 Button.inline("⚡ Премиум-ключ", b"adm:newkeyp")])
    rows.append([Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_keys")])
    return rows


KEY_PANEL_ACCOUNTS_PAGE = 8


def key_panel_keyboard(key: dict, perms: dict, account_total: int) -> list[list[Button]]:
    """Root of the per-key control panel: what a guest sees, gets, and when."""
    key_id = int(key.get("id") or 0)
    granted_accounts = perms.get("accounts") or []
    scope = f"{len(granted_accounts)}/{account_total}" if granted_accounts else f"все ({account_total})"
    premium = (key.get("role") or "viewer") == "premium"
    revoked = bool(key.get("revoked"))
    toggle = (
        Button.inline("♻️ Вернуть", f"key:on:{key_id}".encode())
        if revoked else
        Button.inline("🚫 Отозвать", f"key:rm:{key_id}".encode())
    )
    return [
        [
            Button.inline(f"📂 Разделы ({len(perms.get('features') or [])}/{len(ALL_FEATURES)})", f"key:f:{key_id}".encode()),
            Button.inline(f"🔔 Уведомления ({len(perms.get('notify') or [])}/{len(ALL_NOTIFY)})", f"key:n:{key_id}".encode()),
        ],
        [Button.inline(f"👤 Аккаунты · {scope}", f"key:a:{key_id}".encode())],
        [
            Button.inline(f"⏱ Задержка · {format_delay(permission_delay_minutes(perms))}", f"key:d:{key_id}".encode()),
            Button.inline(f"⏳ Срок · {format_expiry(key.get('expires_at'))}"[:40], f"key:e:{key_id}".encode()),
        ],
        [
            Button.inline("🏷 Метка", f"key:name:{key_id}".encode()),
            Button.inline("👁 Сделать обычным" if premium else "⚡ Сделать премиум", f"key:role:{key_id}".encode()),
        ],
        [
            Button.inline("🔗 Ссылка", f"key:link:{key_id}".encode()),
            Button.inline(f"👥 Вошли ({int(key.get('member_count') or 0)})", f"key:m:{key_id}".encode()),
        ],
        [toggle, Button.inline("🗑 Удалить", f"key:del:{key_id}".encode())],
        [Button.inline("⬅️ Ключи", b"menu_keys"), Button.inline("🔄 Обновить", f"key:{key_id}".encode())],
    ]


def key_expiry_keyboard(key_id: int, key: dict) -> list[list[Button]]:
    """Lifetime presets: forever, N days from now, or a typed-in day count."""
    current = key.get("expires_at")
    presets = [
        Button.inline(("🔘 " if not current else "") + "бессрочно", f"key:e:{key_id}:_off".encode())
    ]
    presets += [
        Button.inline(f"{days} дн", f"key:e:{key_id}:{days}".encode())
        for days in EXPIRY_PRESETS
    ]
    rows = [presets[i:i + 3] for i in range(0, len(presets), 3)]
    rows.append([Button.inline("✏️ Своё значение…", f"key:e:{key_id}:_x".encode())])
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
    return rows


def key_members_keyboard(key_id: int, items: list[tuple[int, str]] = ()) -> list[list[Button]]:
    """Holders of one key; each opens the existing member card.

    Capped like the keys list — the card above lists everyone, the buttons are
    a shortcut, and a keyboard of 50 rows is unusable on a phone.
    """
    rows: list[list[Button]] = [
        [Button.inline(label[:48], f"mem:open:{tg}".encode())] for tg, label in list(items)[:KEYS_LIST_LIMIT]
    ]
    rows.append([Button.inline("⬅️ Назад", f"key:{key_id}".encode())])
    return rows


def key_delete_keyboard(key_id: int) -> list[list[Button]]:
    """Two-step delete: deleting a key is the one action here with no undo."""
    return [
        [Button.inline("🗑 Да, удалить", f"key:delgo:{key_id}".encode())],
        [Button.inline("✖️ Отмена", f"key:{key_id}".encode())],
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
