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
]

# home-screen button -> feature code its key must grant
SECTION_FEATURES = {
    "menu_giveaways": "giveaways",
    "mon:feed:all": "recent",
    "menu_summary": "stats",
    "an:sum": "analytics",
}


def main_menu_buttons(role: str, perms: Optional[dict] = None) -> list[list[Button]]:
    """Home screen. A guest only sees the sections their access key granted."""
    # Imported here, not at module scope: keyboards.py imports from this module,
    # so a top-level import back would close the cycle.
    from .keyboards import webapp_row

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
    if role == "admin":
        rows.append([Button.inline("⚙️ Управление", b"adm:home"),
                     Button.inline("🔄 Скан", b"menu_scan")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
    rows.append([Button.inline("❓ Помощь", b"menu_help")])
    # The panel sits on top when a tunnel is up. With none, `webapp_row` returns
    # an empty list and the menu is exactly what it has always been — the whole
    # point of keeping the inline sections below it.
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


def help_text(role: str, perms: Optional[dict] = None) -> str:
    perms = perms or full_permissions()
    lines = [
        "🛰 **PULSE DESK**",
        "__Мониторинг каналов и розыгрышей__",
        DIV,
        "📋 **Команды**",
        "• /menu — главное меню",
    ]
    lines += [line for line, code in COMMAND_FEATURES if role == "admin" or has_feature(perms, code)]
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
            "• /export — CSV выгрузка",
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
