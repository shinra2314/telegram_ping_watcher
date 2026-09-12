"""Аккаунты Telegram: состояние, здоровье, отключение, перезапуск мониторинга.

Чего здесь **нет** и не будет — входа в аккаунт. Telegram аннулирует код
подтверждения, отправленный в Telegram-чат, да и вводить учётные данные в бота
незачем. Вход остаётся консольным: ``auth_accounts.py``, запускается руками.
"""
from __future__ import annotations

from typing import Any, Optional

from telethon import Button

from database import get_account_ping_stats

from ...app_ctx import state
from ...telegram_accounts import disconnect_account
from ..chrome import dot, empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, fmt_dt

AUTH_HINT = "__Вход в аккаунт — только из консоли: `python auth_accounts.py`.__"


async def collect() -> list[dict[str, Any]]:
    """Аккаунты с их здоровьем — та же сборка, что отдавала /api/accounts/health."""
    ping_stats = await get_account_ping_stats()
    by_sender = {str(row.get("sender_id")): row
                 for row in ping_stats if row.get("sender_id") is not None}
    known = {name: {"session_name": name, "status": "known"} for name in state.session_names}
    known.update(state.accounts_state)
    items = []
    for account in known.values():
        stats = by_sender.get(str(account.get("user_id")), {})
        status_value = account.get("status", "unknown")
        healthy = status_value == "online" and not account.get("last_error")
        items.append({
            **account,
            "healthy": healthy,
            "pings_total": stats.get("total", 0),
            "wins": stats.get("wins", 0),
            "last_ping_at": stats.get("last_ping_at"),
        })
    return sorted(items, key=lambda a: str(a.get("session_name") or ""))


def card(accounts: list[dict[str, Any]]) -> str:
    online = sum(1 for a in accounts if a.get("status") == "online")
    lines = [
        header("🛰", "Аккаунты", "Управление › Аккаунты"),
        f"{kv('🟢', 'Онлайн', f'{online}/{len(accounts)}')}",
        DIV,
    ]
    if not accounts:
        lines.append(empty("Сессий не найдено."))
    for account in accounts:
        name = account.get("session_name") or "?"
        lines.append(f"{dot(bool(account.get('healthy')))} `{name}` · {account.get('status', '?')}"
                     f" · упоминаний `{account.get('pings_total', 0)}`"
                     f" · побед `{account.get('wins', 0)}`")
        if account.get("last_error"):
            lines.append(f"   ⚠️ __{str(account['last_error'])[:120]}__")
        if account.get("last_ping_at"):
            lines.append(f"   🕐 __последнее: {fmt_dt(account['last_ping_at'])}__")
    lines.append("\n" + AUTH_HINT)
    return "\n".join(lines)


def keyboard(accounts: list[dict[str, Any]]) -> list[list[Button]]:
    rows: list[list[Button]] = [
        [Button.inline(f"⏏️ {account.get('session_name')}"[:40],
                       f"ac:off:{i}".encode())]
        for i, account in enumerate(accounts)
        if account.get("status") == "online"
    ]
    rows.append([Button.inline("♻️ Перезапустить мониторинг", b"menu_restart")])
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"ac"),
    ])
    return rows


async def handle(click: Click) -> None:
    if click.arg(1) == "off":
        await _disconnect(click)
        return
    accounts = await collect()
    await safe_edit(click.event, card(accounts), buttons=keyboard(accounts))


async def _disconnect(click: Click) -> None:
    """Отключить один аккаунт. Адресуется индексом в том же порядке показа."""
    index = click.int_arg(2)
    accounts = await collect()
    online = [a for a in accounts if a.get("status") == "online"]
    if index is None or not 0 <= index < len(online):
        await click.event.answer("Список изменился — откройте заново", alert=True)
        return
    name = str(online[index].get("session_name") or "")
    await disconnect_account(name)
    await click.event.answer(f"Отключён: {name}")
    accounts = await collect()
    await safe_edit(click.event, card(accounts), buttons=keyboard(accounts))


def register(router: CallbackRouter) -> None:
    router.group("ac", admin=True)(handle)
