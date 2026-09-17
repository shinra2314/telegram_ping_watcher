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
    FEED_TYPES,
    GIVEAWAY_SORTS,
    FeedFilter,
    GiveawayFilter,
    GIVEAWAY_STATUS_ORDER,
    PING_STATUS_ORDER,
    account_label,
    feed_filter_cb,
    feed_filter_from_legacy,
    feed_state_cb,
    format_expiry,
    giveaway_status_label,
    is_openable_link,
    ping_action_cb,
    ping_open_cb,
    ping_status_label,
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

    The URL is read at send time rather than cached: the tunnel may come up
    after the menu was first drawn, or change hostname on a provider without a
    stable one. With no tunnel the row is empty and the keyboard is one row
    shorter — the inline UI below it stays fully usable.

    A screen inside the panel is addressed by query (``/app?s=accounts``), never
    by ``#``: Telegram appends ``#tgWebAppData=…`` to the URL it opens, and a
    fragment of our own would swallow it.

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


MON_FILTERS = [("all", "Все"), ("important", "Важные"), ("giveaway", "Розыгрыши"), ("win", "Победы")]


def _feed_cb(active: str, page: int) -> bytes:
    """`mon:feed:<filter>` for page 1, `mon:feed:<filter>:<page>` deeper."""
    return (f"mon:feed:{active}" if page <= 1 else f"mon:feed:{active}:{page}").encode()


def _as_feed_state(state, page: int) -> FeedFilter:
    """Принять и новое состояние, и старую строку фильтра из кнопки в чате."""
    if isinstance(state, FeedFilter):
        return state
    return feed_filter_from_legacy(str(state or "all"), page)


# Четыре типа остаются прямо в ленте: это то, ради чего её открывают. Остальные
# измерения выборки живут на экране фильтров, иначе клавиатура перестаёт читаться.
FEED_QUICK_TYPES = ("a", "i", "g", "w")


def feed_keyboard(
    items: list[tuple[int, str]],
    state=FeedFilter(),
    page: int = 1,
    has_more: bool = False,
    is_admin: bool = False,
    can_search: bool = True,
) -> list[list[Button]]:
    """Лента: строка на упоминание, быстрые типы, панель, пагинация, низ.

    🔎 рисуется только ключу с грантом `search` — кнопка без права вела бы в отказ.
    """
    state = _as_feed_state(state, page)
    labels = {code: label for code, _db, label in FEED_TYPES}
    rows: list[list[Button]] = [
        [Button.inline(label, f"mon:open:{pid}".encode())] for pid, label in items
    ]
    rows.append([
        Button.inline(f"▸{labels[code]}" if code == state.type else labels[code],
                      feed_filter_cb(state.with_(type=code)))
        for code in FEED_QUICK_TYPES
    ])
    tools = [Button.inline("⚙️ Фильтры", feed_state_cb("mon:ff", state))]
    if can_search:
        tools.append(Button.inline("🔎 Поиск", b"mon:q"))
    if is_admin:
        tools.append(Button.inline("✅ Прочитать", feed_state_cb("mon:ra", state)))
    rows.append(tools)
    if state.page > 1 or has_more:
        pager: list[Button] = []
        if state.page > 1:
            pager.append(Button.inline("◀️ Новее", state.cb(state.page - 1)))
        pager.append(Button.inline(f"· {state.page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("Старее ▶️", state.cb(state.page + 1)))
        rows.append(pager)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", state.cb()),
    ])
    return rows


def feed_filters_keyboard(state: FeedFilter, is_admin: bool = False) -> list[list[Button]]:
    """Экран выборки: по кнопке на измерение, плюс пресеты и выгрузка.

    Статус и сортировка переключаются по кругу одной кнопкой — отдельная кнопка
    на каждое из шести значений не влезает в читаемую клавиатуру, а цикл
    показывает текущее значение прямо на себе.
    """
    labels = {code: label for code, _db, label in FEED_TYPES}
    type_chips = [
        Button.inline(f"▸{labels[code]}" if code == state.type else labels[code],
                      feed_state_cb("mon:ff", state.with_(type=code)))
        for code, _db, _label in FEED_TYPES
    ]
    rows = [type_chips[i:i + 4] for i in range(0, len(type_chips), 4)]
    rows.append([
        Button.inline(f"🏷 Статус: {state.status_label}", feed_state_cb("mon:cs", state)),
        Button.inline(f"{'⭐' if state.favorite else '☆'} Избранное",
                      feed_state_cb("mon:fv", state)),
    ])
    rows.append([
        Button.inline(f"⇅ {state.sort_label}", feed_state_cb("mon:ss", state)),
        Button.inline("↑ По возрастанию" if state.ascending else "↓ По убыванию",
                      feed_state_cb("mon:so", state)),
    ])
    if state.query:
        rows.append([Button.inline("✖️ Снять поиск", feed_state_cb("mon:qx", state))])
    rows.append([
        Button.inline("💾 Пресеты", b"mon:pr"),
        Button.inline("♻️ Сбросить", feed_state_cb("mon:ff", FeedFilter())),
    ])
    if is_admin:
        rows.append([
            Button.inline("⬇️ CSV", feed_state_cb("mon:ex:c", state)),
            Button.inline("⬇️ JSON", feed_state_cb("mon:ex:j", state)),
        ])
    rows.append([Button.inline("⬅️ К ленте", state.cb())])
    return rows


def feed_presets_keyboard(names: Sequence[str], state: FeedFilter,
                          can_save: bool = True) -> list[list[Button]]:
    """Сохранённые выборки: применить, удалить, сохранить текущую.

    Пресет адресуется индексом — имя пресета кириллицей не влезает в 64 байта.
    """
    rows: list[list[Button]] = [
        [Button.inline(f"▸ {name[:28]}", f"mon:ps:{i}".encode()),
         Button.inline("🗑", f"mon:pd:{i}".encode())]
        for i, name in enumerate(names)
    ]
    if not rows:
        rows.append([Button.inline("📭 Пресетов нет", b"noop")])
    if can_save:
        rows.append([Button.inline("💾 Сохранить текущую", feed_state_cb("mon:pn", state))])
    rows.append([Button.inline("⬅️ К фильтрам", feed_state_cb("mon:ff", state))])
    return rows


def ping_card_keyboard(ping, is_admin: bool = False,
                       state: Optional[FeedFilter] = None) -> list[list[Button]]:
    """Карточка записи: всё, что веб давал в модалке, плюс возврат в ту же ленту.

    ``ping`` — строка из базы; целое число принимается ради кнопок из старых
    сообщений, где кроме id ничего не было.
    """
    row = {"id": ping} if isinstance(ping, int) else dict(ping or {})
    pid = int(row.get("id") or 0)
    state = state or FeedFilter()
    rows: list[list[Button]] = []
    if is_admin:
        rows.append([
            Button.inline("⭐ В избранное" if not row.get("is_favorite") else "★ Убрать",
                          f"ping:fav:{pid}".encode()),
            Button.inline("✓ Прочитано", f"ping:read:{pid}".encode()),
        ])
        rows.append([
            Button.inline(f"🏷 Статус: {ping_status_label(row.get('status'))}",
                          ping_action_cb("st", pid, state)),
            Button.inline("📝 Заметка", ping_action_cb("nt", pid, state)),
        ])
        if row.get("is_giveaway") or row.get("is_win"):
            rows.append([
                Button.inline(f"🎁 Розыгрыш: {giveaway_status_label(row.get('giveaway_status'))}",
                              ping_action_cb("gw", pid, state)),
            ])
            rows.append([Button.inline("🧾 История действий", ping_action_cb("hs", pid, state))])
        rows.append([Button.inline("🏷 Теги", ping_action_cb("tg", pid, state))])
    if is_openable_link(row.get("link")):
        rows.append([Button.url("Открыть в Telegram", str(row["link"]))])
    rows.append([
        Button.inline("⬅️ Назад", state.cb()),
        Button.inline("🔄 Обновить", ping_open_cb(pid, state)),
    ])
    return rows


def ping_status_keyboard(ping_id: int, current: Optional[str],
                         state: Optional[FeedFilter] = None) -> list[list[Button]]:
    """Выбор статуса записи — пять значений словаря `PING_STATUSES`."""
    state = state or FeedFilter()
    chips = [
        Button.inline(f"▸{ping_status_label(code)}" if code == (current or "new")
                      else ping_status_label(code),
                      ping_action_cb("sts", ping_id, state, code))
        for code in PING_STATUS_ORDER
    ]
    rows = [chips[i:i + 3] for i in range(0, len(chips), 3)]
    rows.append([Button.inline("⬅️ К карточке", ping_open_cb(ping_id, state))])
    return rows


def ping_giveaway_keyboard(ping_id: int, current: Optional[str],
                           state: Optional[FeedFilter] = None) -> list[list[Button]]:
    """Исход розыгрыша: те же семь значений, что были сегментом в вебе."""
    state = state or FeedFilter()
    chips = [
        Button.inline(f"▸{giveaway_status_label(code)}" if code == (current or "")
                      else giveaway_status_label(code),
                      ping_action_cb("gws", ping_id, state, code))
        for code in GIVEAWAY_STATUS_ORDER
    ]
    rows = [chips[i:i + 2] for i in range(0, len(chips), 2)]
    rows.append([Button.inline("⬅️ К карточке", ping_open_cb(ping_id, state))])
    return rows


def ping_tags_keyboard(ping_id: int, tags: Sequence[str],
                       state: Optional[FeedFilter] = None) -> list[list[Button]]:
    """Теги записи: снять по индексу, добавить текстом.

    Индекс, а не сам тег: тег кириллицей съедает callback-бюджет, а список на
    обеих сторонах один и тот же — он пришёл из этой же строки базы.
    """
    state = state or FeedFilter()
    rows: list[list[Button]] = [
        [Button.inline(f"🗑 {tag[:24]}", ping_action_cb("tgd", ping_id, state, str(i)))]
        for i, tag in enumerate(tags)
    ]
    if not rows:
        rows.append([Button.inline("📭 Тегов нет", b"noop")])
    rows.append([Button.inline("➕ Добавить тег", ping_action_cb("tga", ping_id, state))])
    rows.append([Button.inline("⬅️ К карточке", ping_open_cb(ping_id, state))])
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


def _giveaway_tail(state: GiveawayFilter) -> str:
    """`:<sort>:<wins>:<acct>:<page>` — the list state every feed callback carries."""
    return f":{state.sort}:{1 if state.wins else 0}:{state.account}:{max(1, state.page)}"


def _giveaway_open_cb(ping_id: int, state: GiveawayFilter) -> bytes:
    """`gw:open:<id>[:<sort>:<wins>:<acct>:<page>]` — the card remembers the list."""
    return f"gw:open:{ping_id}{_giveaway_tail(state)}".encode()


def giveaway_tidy_cb(state: GiveawayFilter, page: Optional[int] = None) -> bytes:
    """`gw:x:<sort>:<wins>:<acct>:<page>` — the same feed in «убрать» mode."""
    state = state if page is None else state._replace(page=page)
    return f"gw:x{_giveaway_tail(state)}".encode()


def _giveaway_remove_cb(ping_id: int, state: GiveawayFilter) -> bytes:
    """`gw:rm:<id>:<sort>:<wins>:<acct>:<page>` — close one row, stay on the page."""
    return f"gw:rm:{ping_id}{_giveaway_tail(state)}".encode()


def giveaway_feed_keyboard(
    items: list[tuple[int, str]],
    page: int = 1,
    has_more: bool = False,
    state: Optional[GiveawayFilter] = None,
    accounts: Sequence[str] = (),
    is_admin: bool = False,
    removing: bool = False,
) -> list[list[Button]]:
    """Giveaways section: candidates, sort/wins/account filters, pager, home/refresh.

    ``removing`` is the owner's «убрать» mode: a tap closes the row instead of
    opening its card. A mode and not a ✖ beside every row, because Telegram
    splits a keyboard row evenly and the chat name would be cut in half.
    """
    state = (state or GiveawayFilter())._replace(page=max(1, page))
    row_cb = _giveaway_remove_cb if removing else _giveaway_open_cb
    page_cb = giveaway_tidy_cb if removing else _giveaway_feed_cb
    rows: list[list[Button]] = [
        [Button.inline(f"✖ {label}" if removing else label, row_cb(pid, state))]
        for pid, label in items
    ]
    if not removing:
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
        if is_admin and items:
            rows.append([Button.inline("✖ Убрать из очереди…", giveaway_tidy_cb(state))])
    if page > 1 or has_more:
        pager: list[Button] = []
        if page > 1:
            pager.append(Button.inline("◀️ Новее", page_cb(state, page - 1)))
        pager.append(Button.inline(f"· {page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("Старее ▶️", page_cb(state, page + 1)))
        rows.append(pager)
    if removing:
        rows.append([Button.inline("✅ Готово", _giveaway_feed_cb(state))])
        return rows
    footer = [Button.inline("⬅️ Домой", b"menu_main")]
    if is_admin:
        # Чистка каналов тратит запросы Telegram — только для владельца.
        footer.append(Button.inline("🧹 Каналы", b"gw:cl:0"))
    footer.append(Button.inline("🔄 Обновить", _giveaway_feed_cb(state)))
    rows.append(footer)
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


def giveaway_card_keyboard(ping_id: int, state: Optional[GiveawayFilter] = None,
                           is_admin: bool = False,
                           link: Optional[str] = None) -> list[list[Button]]:
    """Карточка розыгрыша: у владельца — те же действия, что были в вебе.

    Разбор и профиль канала тратят запросы Telegram, поэтому кнопки видит
    только владелец; гостю карточка остаётся только чтением.
    """
    state = state or GiveawayFilter()
    rows: list[list[Button]] = []
    if is_admin:
        rows.append([
            Button.inline("🔍 Разобрать", f"gw:an:{ping_id}".encode()),
            Button.inline("📡 Профиль канала", f"gw:pr:{ping_id}".encode()),
        ])
        rows.append([
            Button.inline("✅ Забрал", f"gw:sv:{ping_id}:claimed".encode()),
            Button.inline("⏭ Пропустить", f"gw:sk:{ping_id}".encode()),
        ])
        # Статус ставится своим колбэком, а не общим `pg:`: иначе с доски
        # розыгрышей человек уезжает в ленту упоминаний и теряет список.
        rows.append([Button.inline("🎁 Статус…", f"gw:ss:{ping_id}".encode())])
    if is_openable_link(link):
        rows.append([Button.url("Открыть в Telegram", str(link))])
    rows.append([
        Button.inline("⬅️ Назад", _giveaway_feed_cb(state)),
        Button.inline("🔄 Обновить", _giveaway_open_cb(ping_id, state)),
    ])
    return rows


def giveaway_status_keyboard(ping_id: int,
                             current: Optional[str] = None) -> list[list[Button]]:
    """Исход розыгрыша, не покидая доску: те же семь значений, что в ленте."""
    chips = [
        Button.inline(f"▸{giveaway_status_label(code)}" if code == (current or "")
                      else giveaway_status_label(code),
                      f"gw:sv:{ping_id}:{code}".encode())
        for code in GIVEAWAY_STATUS_ORDER
    ]
    rows = [chips[i:i + 2] for i in range(0, len(chips), 2)]
    rows.append([Button.inline("⬅️ К карточке", f"gw:open:{ping_id}".encode())])
    return rows


def cleanup_keyboard(candidates: Sequence[dict], page: int = 0,
                     page_size: int = 6) -> list[list[Button]]:
    """Мёртвые каналы: выход адресуется chat_id, он и так число.

    Кнопка ведёт на подтверждение, а не на выход: назад в приватный канал без
    новой ссылки не вернуться.
    """
    start = page * page_size
    window = list(candidates)[start:start + page_size]
    def label(item: dict) -> str:
        wins = int(item.get("wins") or 0)
        mark = f"🏆{wins} " if wins else "🚪 "
        title = str(item.get("title") or item.get("chat_id"))
        seats = len(item.get("accounts") or [])
        suffix = f" ×{seats}" if seats > 1 else ""
        return f"{mark}{title[:30]}{suffix} · {item.get('inactive_days', '?')}д"[:60]

    rows: list[list[Button]] = [
        [Button.inline(label(item), f"gw:lv:{item.get('chat_id')}".encode())]
        for item in window
    ]
    if not rows:
        rows.append([Button.inline("📭 Пусто", b"noop")])
    pager: list[Button] = []
    if page > 0:
        pager.append(Button.inline("◀️", f"gw:cl:{page - 1}".encode()))
    if len(candidates) > start + page_size:
        pager.append(Button.inline("▶️", f"gw:cl:{page + 1}".encode()))
    if pager:
        rows.append(pager)
    rows.append([Button.inline("⬅️ Розыгрыши", b"menu_giveaways")])
    return rows


def leave_confirm_keyboard(chat_id: int) -> list[list[Button]]:
    return [
        [Button.inline("🚪 Да, выйти", f"gw:lvgo:{chat_id}".encode())],
        [Button.inline("⬅️ Отмена", b"gw:cl:0")],
    ]


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
    """Панель владельца. Ровно те разделы, которые были админскими вкладками
    веба: настройки, ключи, люди, доступ, аккаунты, сервисы, бэкапы, здоровье."""
    return [
        [Button.inline("⚙️ Настройки", b"st"), Button.inline("🔑 Ключи", b"menu_keys")],
        [Button.inline("👥 Люди", b"adm:members"), Button.inline("⏰ Доступ", b"adm:access")],
        [Button.inline("🛰 Аккаунты", b"ac"), Button.inline("🧩 Сервисы", b"sv")],
        [Button.inline("💾 Бэкапы", b"bk"), Button.inline("🩺 Здоровье", b"dg")],
        [Button.inline("🔄 Скан", b"menu_scan"), Button.inline("📜 Логи", b"menu_logs")],
        [Button.inline("🎰 Рулетка", b"rl"), Button.inline("♻️ Рестарт", b"menu_restart")],
        [Button.inline("🖼 Отчёт", b"rp:w"), Button.inline("🏖 Отпуск", b"vc")],
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
    """Shortcut buttons for the members list.

    Capped like every other list keyboard here: the card above names everyone,
    and past Telegram's row limit an uncapped keyboard makes the whole reply
    fail — which broke /members permanently once enough members had joined.
    """
    rows: list[list[Button]] = [
        [Button.inline(label[:48], f"mem:open:{tg}".encode())] for tg, label in list(items)[:KEYS_LIST_LIMIT]
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
        [Button.inline("🎟 Одноразовый: да" if int(key.get("max_uses") or 0) == 1
                       else "🎟 Одноразовый: нет", f"key:once:{key_id}".encode())],
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
    return [
        [Button.inline("⚠️ Только проблемы", b"lg:err"), Button.inline("📎 Файлом", b"lg:file")],
        [Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Последние", b"menu_logs")],
    ]


# --- зарплаты ------------------------------------------------------------
# Грамматика колбэков: `sal:<вид>:<месяц>[:<аргумент>]`. Месяц ("2026-09") ездит
# в самом колбэке, поэтому любая кнопка воспроизводит свой экран без памяти о
# предыдущем нажатии, а вся строка укладывается в 64 байта Telegram.
SALARY_ACCOUNTS_PAGE = 8


def _salary_cb(view: str, month: str, arg: str = "") -> bytes:
    parts = ["sal", view, month] + ([arg] if arg else [])
    return ":".join(parts).encode()


def _salary_month_row(view: str, month: str, months: Sequence[str]) -> list[Button]:
    """Стрелки по месяцам книги плюс подпись текущего.

    Стрелка за границей книги не рисуется вовсе: неактивных кнопок в Telegram
    нет, а кнопка, которая молча ничего не делает, читается как поломка.
    """
    from ..salary import month_label, shift_month

    row: list[Button] = []
    previous = shift_month(month, -1)
    following = shift_month(month, 1)
    if previous in months:
        row.append(Button.inline("⬅️", _salary_cb(view, previous)))
    row.append(Button.inline(month_label(month), b"noop"))
    if following in months:
        row.append(Button.inline("➡️", _salary_cb(view, following)))
    return row


def salary_keyboard(month: str, months: Sequence[str] = (), *, is_owner: bool = False) -> list[list[Button]]:
    """Личная карточка зарплаты (у владельца — обзор по всем)."""
    rows = [_salary_month_row("m", month, months)]
    second = [Button.inline("🏆 Топ", _salary_cb("top", month)),
              Button.inline("📈 Аналитика", _salary_cb("an", month))]
    if is_owner:
        second = [Button.inline("🏆 Топ", _salary_cb("top", month)),
                  Button.inline("👥 По аккаунтам", _salary_cb("list", month))]
    rows.append(second)
    rows.append([Button.inline("⬅️ Домой", b"menu_main"), Button.inline("🔄 Обновить", _salary_cb("m", month))])
    return rows


def salary_top_keyboard(month: str, months: Sequence[str] = ()) -> list[list[Button]]:
    return [
        _salary_month_row("top", month, months),
        [Button.inline("💵 Зарплата", _salary_cb("m", month)),
         Button.inline("🔄 Обновить", _salary_cb("top", month))],
        [Button.inline("⬅️ Домой", b"menu_main")],
    ]


def salary_analytics_keyboard(month: str, months: Sequence[str] = ()) -> list[list[Button]]:
    return [
        _salary_month_row("an", month, months),
        [Button.inline("💵 Зарплата", _salary_cb("m", month)),
         Button.inline("🏆 Топ", _salary_cb("top", month))],
        [Button.inline("⬅️ Домой", b"menu_main")],
    ]


def salary_accounts_keyboard(month: str, accounts: Sequence[str], page: int = 0) -> list[list[Button]]:
    """Владелец выбирает парня. Аккаунт адресуется индексом в списке книги —
    имена кириллицей съели бы весь лимит колбэка."""
    total = len(accounts)
    pages = max(1, (total + SALARY_ACCOUNTS_PAGE - 1) // SALARY_ACCOUNTS_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * SALARY_ACCOUNTS_PAGE
    rows: list[list[Button]] = []
    chunk = list(enumerate(accounts))[start:start + SALARY_ACCOUNTS_PAGE]
    for index in range(0, len(chunk), 2):
        rows.append([
            Button.inline(name[:24], _salary_cb("who", month, str(idx)))
            for idx, name in chunk[index:index + 2]
        ])
    if pages > 1:
        pager = []
        if page > 0:
            pager.append(Button.inline("◀️", _salary_cb("list", month, str(page - 1))))
        pager.append(Button.inline(f"{page + 1}/{pages}", b"noop"))
        if page < pages - 1:
            pager.append(Button.inline("▶️", _salary_cb("list", month, str(page + 1))))
        rows.append(pager)
    rows.append([Button.inline("⬅️ Назад", _salary_cb("m", month)),
                 Button.inline("🏆 Топ", _salary_cb("top", month))])
    return rows


def salary_member_keyboard(month: str, index: int, *, analytics: bool = False) -> list[list[Button]]:
    """Карточка одного парня глазами владельца.

    ``analytics`` переворачивает первую кнопку: с разбора месяца возвращаться
    надо к сумме, а не открывать разбор повторно.
    """
    first = (
        Button.inline("💵 Зарплата", _salary_cb("who", month, str(index)))
        if analytics
        else Button.inline("📈 Аналитика", _salary_cb("wan", month, str(index)))
    )
    return [
        [first, Button.inline("👥 Другие", _salary_cb("list", month))],
        [Button.inline("⬅️ Назад", _salary_cb("m", month)),
         Button.inline("🔄 Обновить", _salary_cb("wan" if analytics else "who", month, str(index)))],
    ]
