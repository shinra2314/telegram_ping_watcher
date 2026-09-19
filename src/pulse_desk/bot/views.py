"""Pure presentation helpers for the Telegram bot UI.

No Telethon events, no I/O — strings and keyboard structures only, so these
are unit-testable without a running bot.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import NamedTuple, Optional, Sequence

from telethon import Button

from .. import APP_VERSION  # noqa: F401  (re-exported for later phases)
from ..bot_permissions import full_permissions, has_feature, render_permissions_summary

DIV = "━━━━━━━━━━━━━━━"


def paginate(rows: list, page: int = 1, size: int = 8) -> tuple[list, bool]:
    """Slice ``rows`` into one page and report whether another one follows.

    Shared by every paged bot view so a section can never silently stop at its
    first page: the caller must fetch at least ``page * size + 1`` rows for the
    ``has_more`` flag to be meaningful.
    """
    page = max(1, int(page or 1))
    start = (page - 1) * size
    window = rows[start:start + size]
    return window, len(rows) > start + size


def fmt_dt(value: Optional[str]) -> str:
    """Trim an ISO timestamp to `MM-DD HH:MM` for compact display."""
    if not value:
        return "—"
    text = str(value).replace("T", " ")
    return text[5:16] if len(text) >= 16 else text


# ---- giveaway feed filters --------------------------------------------------
# The whole view state rides in one callback — `gw:f:<sort>:<wins>:<acct>:<page>`
# — because Telegram caps callback data at 64 bytes. The account travels as an
# index into the tracked-usernames list, which both sides rebuild identically
# (same trick as the per-key account toggles).
GIVEAWAY_SORTS: tuple[tuple[str, str, str], ...] = (
    ("d", "detected", "🕐 Обнаружено"),
    ("p", "posted", "📅 Дата поста"),
)
GIVEAWAY_SORT_DB = {code: db_value for code, db_value, _label in GIVEAWAY_SORTS}
GIVEAWAY_SORT_LABEL = {code: label for code, _db, label in GIVEAWAY_SORTS}
ALL_ACCOUNTS = -1


class GiveawayFilter(NamedTuple):
    """What the giveaways feed is currently showing."""

    sort: str = "d"
    wins: bool = False
    account: int = ALL_ACCOUNTS
    page: int = 1

    @property
    def db_sort(self) -> str:
        """`sort` translated to the value `get_giveaway_board` understands."""
        return GIVEAWAY_SORT_DB.get(self.sort, "detected")

    @property
    def sort_label(self) -> str:
        return GIVEAWAY_SORT_LABEL.get(self.sort, GIVEAWAY_SORT_LABEL["d"])

    def with_(self, **changes) -> "GiveawayFilter":
        """Copy with fields replaced; any change but paging restarts at page 1."""
        if "page" not in changes:
            changes["page"] = 1
        return self._replace(**changes)

    def cb(self, page: Optional[int] = None) -> bytes:
        return giveaway_filter_cb(self if page is None else self._replace(page=page))


def giveaway_filter_cb(state: GiveawayFilter) -> bytes:
    """Encode feed state as `gw:f:<sort>:<wins>:<account>:<page>`."""
    return f"gw:f:{state.sort}:{1 if state.wins else 0}:{state.account}:{max(1, state.page)}".encode()


def parse_giveaway_filter(seg: Sequence[str]) -> GiveawayFilter:
    """Decode the segments after `gw:f:`; anything malformed falls back to a default."""
    seg = list(seg)

    def num(index: int, default: int) -> int:
        try:
            return int(seg[index])
        except (IndexError, ValueError, TypeError):
            return default

    sort = seg[0] if seg and seg[0] in GIVEAWAY_SORT_DB else "d"
    account = num(2, ALL_ACCOUNTS)
    return GiveawayFilter(
        sort=sort,
        wins=bool(num(1, 0)),
        account=account if account >= 0 else ALL_ACCOUNTS,
        page=max(1, num(3, 1)),
    )


# ---- mentions feed filters --------------------------------------------------
# Веб держал фильтры ленты в localStorage и слал их запросом; в боте вся выборка
# обязана уместиться в 64 байта callback-данных, поэтому каждое измерение — один
# символ: `mon:f:<тип>:<статус>:<избранное>:<сортировка>:<порядок>:<поиск>:<стр>`.
#
# Текст поиска в эти 64 байта не влезает и потому в кнопке едет только флагом:
# сама строка лежит на сервере (state.bot_feed_queries), а флаг говорит, что её
# надо применить. После рестарта строка теряется и лента честно показывается без
# поиска — осознанный размен: иначе пришлось бы хранить состояние экрана в базе.
FEED_TYPES: tuple[tuple[str, str, str], ...] = (
    ("a", "all", "Все"),
    ("i", "important", "Важные"),
    ("g", "giveaway", "Розыгрыши"),
    ("w", "win", "Победы"),
    ("p", "private", "Личные"),
    ("c", "channel", "Каналы"),
    ("r", "group", "Группы"),
)
FEED_TYPE_DB = {code: value for code, value, _label in FEED_TYPES}
FEED_TYPE_LABEL = {code: label for code, _value, label in FEED_TYPES}
FEED_TYPE_CODE = {value: code for code, value, _label in FEED_TYPES}

FEED_STATUSES: tuple[tuple[str, Optional[str], str], ...] = (
    ("a", None, "Любой"),
    ("n", "new", "Новые"),
    ("r", "read", "Прочитанные"),
    ("i", "important", "Важные"),
    ("g", "ignored", "Игнор"),
    ("s", "resolved", "Решённые"),
)
FEED_STATUS_DB = {code: value for code, value, _label in FEED_STATUSES}
FEED_STATUS_LABEL = {code: label for code, _value, label in FEED_STATUSES}

FEED_SORTS: tuple[tuple[str, str, str], ...] = (
    ("d", "detected_at", "Обнаружено"),
    ("m", "date", "Дата поста"),
    ("p", "priority_score", "Приоритет"),
    ("c", "chat", "Чат"),
    ("s", "sender", "Автор"),
)
FEED_SORT_DB = {code: value for code, value, _label in FEED_SORTS}
FEED_SORT_LABEL = {code: label for code, _value, label in FEED_SORTS}


class FeedFilter(NamedTuple):
    """Что лента упоминаний показывает прямо сейчас."""

    type: str = "a"
    status: str = "a"
    favorite: bool = False
    sort: str = "d"
    ascending: bool = False
    query: bool = False
    page: int = 1

    @property
    def is_default(self) -> bool:
        """Ничего не выбрано — такой экран кодируется короткой формой."""
        return self == FeedFilter(page=self.page)

    @property
    def db_type(self) -> str:
        return FEED_TYPE_DB.get(self.type, "all")

    @property
    def db_status(self) -> Optional[str]:
        return FEED_STATUS_DB.get(self.status)

    @property
    def db_sort(self) -> str:
        return FEED_SORT_DB.get(self.sort, "detected_at")

    @property
    def db_order(self) -> str:
        return "ASC" if self.ascending else "DESC"

    @property
    def type_label(self) -> str:
        return FEED_TYPE_LABEL.get(self.type, "Все")

    @property
    def status_label(self) -> str:
        return FEED_STATUS_LABEL.get(self.status, "Любой")

    @property
    def sort_label(self) -> str:
        return FEED_SORT_LABEL.get(self.sort, "Обнаружено")

    def with_(self, **changes) -> "FeedFilter":
        """Копия с изменениями; любое изменение кроме страницы — снова на первую."""
        if "page" not in changes:
            changes["page"] = 1
        return self._replace(**changes)

    def next_sort(self) -> "FeedFilter":
        """Сортировка по кругу — кнопок на пять отдельных нет."""
        codes = [code for code, _db, _label in FEED_SORTS]
        return self.with_(sort=codes[(codes.index(self.sort) + 1) % len(codes)])

    def next_status(self) -> "FeedFilter":
        codes = [code for code, _db, _label in FEED_STATUSES]
        return self.with_(status=codes[(codes.index(self.status) + 1) % len(codes)])

    def cb(self, page: Optional[int] = None) -> bytes:
        return feed_filter_cb(self if page is None else self._replace(page=page))


def feed_state_cb(prefix: str, state: FeedFilter) -> bytes:
    """Состояние ленты под любым префиксом — экран фильтров и экспорт несут то же.

    Одна кодировка на все кнопки: любой экран, куда человек уходит из ленты,
    умеет вернуть его ровно в ту же выборку.
    """
    return (
        f"{prefix}:{state.type}:{state.status}:{1 if state.favorite else 0}:"
        f"{state.sort}:{1 if state.ascending else 0}:{1 if state.query else 0}:"
        f"{max(1, state.page)}"
    ).encode()


def feed_filter_cb(state: FeedFilter) -> bytes:
    """`mon:f:<тип>:<статус>:<изб>:<сорт>:<порядок>:<поиск>:<стр>`."""
    return feed_state_cb("mon:f", state)


def parse_feed_filter(seg: Sequence[str]) -> FeedFilter:
    """Разобрать сегменты после `mon:f:`; любой мусор — значение по умолчанию."""
    seg = list(seg)

    def pick(index: int, table: dict, default: str) -> str:
        value = seg[index] if len(seg) > index else default
        return value if value in table else default

    def flag(index: int) -> bool:
        return (seg[index] if len(seg) > index else "0") == "1"

    def num(index: int, default: int) -> int:
        try:
            return int(seg[index])
        except (IndexError, ValueError, TypeError):
            return default

    return FeedFilter(
        type=pick(0, FEED_TYPE_DB, "a"),
        status=pick(1, FEED_STATUS_DB, "a"),
        favorite=flag(2),
        sort=pick(3, FEED_SORT_DB, "d"),
        ascending=flag(4),
        query=flag(5),
        page=max(1, num(6, 1)),
    )


def feed_filter_from_legacy(code: str, page: int = 1) -> FeedFilter:
    """Кнопка старого вида `mon:feed:<фильтр>[:<стр>]` — она ещё живёт в чатах."""
    return FeedFilter(type=FEED_TYPE_CODE.get(code, "a"), page=max(1, page))


def describe_feed_filter(state: FeedFilter, query: str = "") -> str:
    """Однострочное описание выборки для шапки — что именно человек видит."""
    parts = []
    if state.type != "a":
        parts.append(state.type_label.lower())
    if state.status != "a":
        parts.append(state.status_label.lower())
    if state.favorite:
        parts.append("избранное")
    if state.query and query:
        parts.append(f"«{query[:24]}»")
    order = "↑" if state.ascending else "↓"
    parts.append(f"{state.sort_label.lower()} {order}")
    return " · ".join(parts)


# ---- ping card ---------------------------------------------------------------
# Подписи статусов: в базе лежат коды, человеку нужны слова. Словари здесь, а не
# в keyboards, потому что те же подписи печатает карточка.
PING_STATUS_LABELS: dict[str, str] = {
    "new": "Новое",
    "read": "Прочитано",
    "important": "Важное",
    "ignored": "Игнор",
    "resolved": "Решено",
}
GIVEAWAY_STATUS_LABELS: dict[str, str] = {
    "": "не задан",
    "pending": "жду итогов",
    "claimed": "забрал",
    "missed": "не успел",
    "missed_unsubscribe": "отписался",
    "missed_reply": "не ответил",
    "scam": "скам",
    "closed": "закрыт",
}
# Порядок кнопок в подменю — от самого частого исхода к редкому.
GIVEAWAY_STATUS_ORDER: tuple[str, ...] = (
    "pending", "claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed",
)
PING_STATUS_ORDER: tuple[str, ...] = ("new", "read", "important", "resolved", "ignored")


def ping_status_label(code: Optional[str]) -> str:
    return PING_STATUS_LABELS.get(str(code or "new"), str(code or "—"))


def giveaway_status_label(code: Optional[str]) -> str:
    return GIVEAWAY_STATUS_LABELS.get(str(code or ""), str(code))


def ping_action_cb(action: str, ping_id: int, state: Optional["FeedFilter"] = None,
                   arg: str = "") -> bytes:
    """`pg:<действие>:<id>[:<арг>]` плюс состояние ленты, чтобы ⬅️ вернулась куда была."""
    head = f"pg:{action}:{ping_id}"
    if arg:
        head += f":{arg}"
    return feed_state_cb(head, state or FeedFilter())


def ping_open_cb(ping_id: int, state: Optional["FeedFilter"] = None) -> bytes:
    """Открыть карточку, не потеряв выборку ленты под ней."""
    if state is None or state.is_default:
        # Короткая форма — она же стоит на кнопках в старых сообщениях.
        return f"mon:open:{ping_id}".encode()
    return feed_state_cb(f"mon:open:{ping_id}", state)


def is_openable_link(link: Optional[str]) -> bool:
    """У пинга бывает не ссылка, а объяснение, почему её нет."""
    text = str(link or "")
    return text.startswith("http") or text.startswith("tg://")


def account_label(names: Sequence[str], index: int) -> str:
    """Button/header label for the selected account (`Все` when unset or stale)."""
    if index is None or index < 0 or index >= len(names):
        return "Все"
    return f"@{names[index]}"


# ---- access-key expiry (owner panel) ---------------------------------------
# Lifetime presets in days; 0 is spelled "бессрочно" and stores NULL.
EXPIRY_PRESETS: tuple[int, ...] = (1, 3, 7, 30)
MAX_EXPIRY_DAYS = 365


def expiry_from_days(days: int) -> Optional[str]:
    """ISO timestamp `days` from now, or None for a key that never expires."""
    days = max(0, min(MAX_EXPIRY_DAYS, int(days or 0)))
    if not days:
        return None
    return (datetime.now() + timedelta(days=days)).replace(microsecond=0).isoformat()


def parse_expiry_days(text: str) -> Optional[int]:
    """Validate free-text days from the key panel; None if unusable. 0 — forever."""
    cleaned = (text or "").strip()
    match = re.fullmatch(r"(\d+)\s*(?:д|дн|дня|дней|d|day|days)?", cleaned, flags=re.IGNORECASE)
    if not match:
        return None
    value = int(match.group(1))
    return value if value <= MAX_EXPIRY_DAYS else None


def key_expired(expires_at: Optional[str]) -> bool:
    return bool(expires_at) and str(expires_at) <= datetime.now().replace(microsecond=0).isoformat()


def format_expiry(expires_at: Optional[str]) -> str:
    """`бессрочно` / `07-10 00:00 · через 5 дн` / `07-10 00:00 · истёк`."""
    if not expires_at:
        return "бессрочно"
    stamp = fmt_dt(expires_at)
    try:
        left = datetime.fromisoformat(str(expires_at)) - datetime.now()
    except ValueError:
        return stamp
    if left.total_seconds() <= 0:
        return f"{stamp} · истёк"
    if left.days >= 1:
        return f"{stamp} · через {left.days} дн"
    hours = int(left.total_seconds() // 3600)
    return f"{stamp} · через {hours} ч" if hours else f"{stamp} · меньше часа"


# Analytics report pages, in tab-strip order. Shared by the card renderer and
# the keyboard so a page can never exist without a button (or the reverse).
ANALYTICS_TABS: list[tuple[str, str]] = [
    ("sum", "Обзор"),
    ("src", "Источники"),
    ("who", "Люди"),
    ("time", "Время"),
    ("flow", "Качество"),
    ("lat", "Задержка"),
]

# Те же страницы для держателя ключа. «Качество» здесь нет: его считает
# `build_detailed_analytics`, отчёт по всей базе, а гостю показывается
# `build_panel_report` — только его аккаунты. Вкладка без данных хуже, чем её
# отсутствие, поэтому список короче, а не с пустой страницей.
MEMBER_ANALYTICS_TABS: list[tuple[str, str]] = [
    ("sum", "Обзор"),
    ("src", "Источники"),
    ("who", "Люди"),
    ("time", "Время"),
    ("lat", "Задержка"),
]

# home-screen button -> feature code its key must grant
SECTION_FEATURES = {
    "menu_giveaways": "giveaways",
    "mon:feed:all": "recent",
    "menu_summary": "stats",
    "an:sum": "analytics",
}


def main_menu_buttons(role: str, perms: Optional[dict] = None, salary: bool = False) -> list[list[Button]]:
    """Home screen. A guest only sees the sections their access key granted.

    ``salary`` is decided by the caller, not by a grant code: the section opens
    only for a key whose label names an account in the salary workbook, and a
    grant code would have opened it for every legacy key instead (a missing code
    in ``permissions`` means "grant everything").
    """
    perms = perms or full_permissions()
    granted = [
        Button.inline(label, cb.encode())
        for label, cb in (
            ("🎁 Розыгрыши", "menu_giveaways"),
            ("🕐 Последние", "mon:feed:all"),
            ("📊 Сводка", "menu_summary"),
            ("📈 Аналитика", "an:sum"),
        )
        if role == "admin" or has_feature(perms, SECTION_FEATURES[cb])
    ]
    rows = [granted[i:i + 2] for i in range(0, len(granted), 2)]
    if salary:
        rows.append([Button.inline("💵 Зарплата", b"sal")])
    if role == "admin":
        # Долги — учёт денег владельца; гостю раздел не открывается и грант-кодом
        # (свои выигрыши держатель ключа видит в ленте розыгрышей).
        # Курсы на главную намеренно не выносятся: их место — «Сводка» и
        # /market (см. test_summary_replaces_split_views).
        rows.append([Button.inline("💰 Долги", b"menu_debts"),
                     Button.inline("⚙️ Управление", b"adm:home")])
        rows.append([Button.inline("🔄 Скан", b"menu_scan")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
    rows.append([Button.inline("❓ Помощь", b"menu_help")])
    # Панель — сверху, когда туннель поднят. Без него `webapp_row` отдаёт пустой
    # список, и меню ровно такое же, как без Mini App: inline-разделы остаются.
    from .keyboards import webapp_row

    panel = webapp_row("🛰 Панель")
    return [panel] + rows if panel else rows


# slash-command help line -> feature code it needs
COMMAND_FEATURES = [
    ("• /stats — статистика", "stats"),
    ("• /analytics — отчёт: источники, авторы, часы", "analytics"),
    ("• /status — состояние аккаунтов", "status"),
    ("• /giveaways — розыгрыши", "giveaways"),
    ("• /recent `[N]` — последние упоминания", "recent"),
    ("• /search `<текст>` — поиск", "search"),
    ("• /market — курсы", "market"),
    ("• /convert `100 usd uah` — конвертер валют и крипты", "market"),
]


def help_text(role: str, perms: Optional[dict] = None, salary: bool = False) -> str:
    perms = perms or full_permissions()
    lines = [
        "🛰 **PULSE DESK**",
        "__Мониторинг каналов и розыгрышей__",
        DIV,
        "📋 **Команды**",
        "• /menu — главное меню",
    ]
    lines += [line for line, code in COMMAND_FEATURES if role == "admin" or has_feature(perms, code)]
    if salary:
        lines.append("• /salary `[09.2026]` — зарплата за месяц")
    lines += [
        "• /settings — настройки и уведомления",
        "• /ping — проверка связи",
    ]
    if role == "admin":
        lines += [
            "",
            "👑 **Владелец**",
            "• /scan — скан истории",
            "• /logs — последние логи",
            "• /export — CSV выгрузка (`/export json` — JSON)",
            "• /win <ссылка> [@аккаунт] — добавить пропущенную победу",
            "• /report [месяц] — отчёт картинкой за 7 дней / месяц",
            "• /invite [метка] — одноразовое приглашение",
            "• /newkey `[метка]` — создать ключ",
            "• /keys — список ключей",
            "• /members `[запрос]` — пользователи / поиск",
            "• /access `<user>` — доступ по расписанию",
            "• /actions — действия по розыгрышам",
            "• /roulette `[21:47]` — напоминание про рулетку йобо",
            "• /settings — настройки мониторинга",
        ]
    else:
        lines += ["", "👁 __Режим: только просмотр__", "", render_permissions_summary(perms)]
    return "\n".join(lines)


def menu_caption(role: str) -> str:
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    return f"🛰 **PULSE DESK** · __{badge}__\nВыберите раздел 👇"
