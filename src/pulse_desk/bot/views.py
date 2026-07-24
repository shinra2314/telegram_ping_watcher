"""Pure presentation helpers for the Telegram bot UI.

No Telethon events, no I/O — strings and keyboard structures only, so these
are unit-testable without a running bot.
"""
from __future__ import annotations

from typing import Optional

from telethon import Button

from .. import APP_VERSION  # noqa: F401  (re-exported for later phases)
from ..bot_permissions import full_permissions, has_feature, render_permissions_summary

DIV = "━━━━━━━━━━━━━━━"


def fmt_dt(value: Optional[str]) -> str:
    """Trim an ISO timestamp to `MM-DD HH:MM` for compact display."""
    if not value:
        return "—"
    text = str(value).replace("T", " ")
    return text[5:16] if len(text) >= 16 else text


# home-screen button -> feature code its key must grant
SECTION_FEATURES = {
    "menu_giveaways": "giveaways",
    "mon:feed:check": "checks",
    "mon:feed:all": "recent",
    "menu_summary": "stats",
}


def main_menu_buttons(role: str, perms: Optional[dict] = None) -> list[list[Button]]:
    """Home screen. A guest only sees the sections their access key granted."""
    perms = perms or full_permissions()
    granted = [
        Button.inline(label, cb.encode())
        for label, cb in (
            ("🎁 Розыгрыши", "menu_giveaways"),
            ("💸 Чеки", "mon:feed:check"),
            ("🕐 Последние", "mon:feed:all"),
            ("📊 Сводка", "menu_summary"),
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
    return rows


# slash-command help line -> feature code it needs
COMMAND_FEATURES = [
    ("• /stats — статистика", "stats"),
    ("• /status — состояние аккаунтов", "status"),
    ("• /giveaways — розыгрыши", "giveaways"),
    ("• /recent `[N]` — последние упоминания", "recent"),
    ("• /checks — найденные чеки", "checks"),
    ("• /search `<текст>` — поиск", "search"),
    ("• /market — курсы", "market"),
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
            "• /settings — настройки мониторинга",
        ]
    else:
        lines += ["", "👁 __Режим: только просмотр__", "", render_permissions_summary(perms)]
    return "\n".join(lines)


def menu_caption(role: str) -> str:
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    return f"🛰 **PULSE DESK** · __{badge}__\nВыберите раздел 👇"
