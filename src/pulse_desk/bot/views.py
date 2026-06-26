"""Pure presentation helpers for the Telegram bot UI.

No Telethon events, no I/O — strings and keyboard structures only, so these
are unit-testable without a running bot.
"""
from __future__ import annotations

from typing import Optional

from telethon import Button

from .. import APP_VERSION  # noqa: F401  (re-exported for later phases)

DIV = "━━━━━━━━━━━━━━━"


def fmt_dt(value: Optional[str]) -> str:
    """Trim an ISO timestamp to `MM-DD HH:MM` for compact display."""
    if not value:
        return "—"
    text = str(value).replace("T", " ")
    return text[5:16] if len(text) >= 16 else text


def main_menu_buttons(role: str) -> list[list[Button]]:
    rows = [
        [Button.inline("📊 Статистика", b"menu_stats"), Button.inline("🎁 Розыгрыши", b"menu_giveaways")],
        [Button.inline("💸 Чеки", b"menu_checks"), Button.inline("🕐 Последние", b"menu_recent")],
        [Button.inline("💹 Курсы", b"menu_market"), Button.inline("🛰 Статус", b"menu_status")],
        [Button.inline("❓ Помощь", b"menu_help")],
    ]
    if role == "admin":
        rows.append([
            Button.inline("🔑 Ключи", b"menu_keys"),
            Button.inline("🔄 Скан", b"menu_scan"),
            Button.inline("📜 Логи", b"menu_logs"),
        ])
        rows.append([Button.inline("⚙️ Настройки", b"st"), Button.inline("♻️ Рестарт", b"menu_restart")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
    return rows


def help_text(role: str) -> str:
    lines = [
        "🛰 **PULSE DESK**",
        "__Мониторинг каналов и розыгрышей__",
        DIV,
        "📋 **Команды**",
        "• /menu — главное меню",
        "• /stats — статистика",
        "• /status — состояние аккаунтов",
        "• /giveaways — розыгрыши",
        "• /recent `[N]` — последние упоминания",
        "• /checks — найденные чеки",
        "• /search `<текст>` — поиск",
        "• /market — курсы",
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
        lines += ["", "👁 __Режим: только просмотр__"]
    return "\n".join(lines)


def menu_caption(role: str) -> str:
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    return f"🛰 **PULSE DESK** · __{badge}__\nВыберите раздел 👇"
