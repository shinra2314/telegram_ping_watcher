"""Аккаунты Telegram: состояние, здоровье, отключение, перезапуск мониторинга.

Чего здесь **нет** и не будет — входа в аккаунт в самом чате: Telegram аннулирует
код подтверждения, отправленный в Telegram-чат. Вход живёт в панели (Mini App,
поле формы — не сообщение), кнопка ведёт прямо на мастер; без туннеля остаётся
консольный ``auth_accounts.py``.

Здоровье считает :mod:`pulse_desk.account_health` — те же правила, по которым
джоб ``account-health`` пишет владельцу. Кнопка «🛡 Спам-блок» спрашивает
@SpamBot от имени аккаунта: один `/start`, ответ показывается как есть.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from telethon import Button

from database import get_account_ping_stats

from ...account_health import account_problem, spam_verdict
from ...app_ctx import logger, state
from ...common import record_app_event
from ...telegram_accounts import disconnect_account
from ..chrome import dot, empty, header, kv
from ..keyboards import webapp_row
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, fmt_dt

AUTH_HINT = "__Вход в аккаунт — в панели (кнопка ниже) или из консоли: `python auth_accounts.py`.__"
SPAMBOT = "SpamBot"
SPAMBOT_TIMEOUT_SECONDS = 20


async def collect() -> list[dict[str, Any]]:
    """Аккаунты с их здоровьем — та же сборка, что отдавала /api/accounts/health."""
    ping_stats = await get_account_ping_stats()
    by_sender = {str(row.get("sender_id")): row
                 for row in ping_stats if row.get("sender_id") is not None}
    known = {name: {"session_name": name, "status": "known"} for name in state.session_names}
    known.update(state.accounts_state)
    now = datetime.now()
    items = []
    for account in known.values():
        stats = by_sender.get(str(account.get("user_id")), {})
        status_value = account.get("status", "unknown")
        problem = account_problem(account, now, app_started_at=state.started_at)
        healthy = status_value == "online" and not account.get("last_error") and problem is None
        items.append({
            **account,
            "healthy": healthy,
            "problem": problem[1] if problem else "",
            "pings_total": stats.get("total", 0),
            "wins": stats.get("wins", 0),
            "last_ping_at": stats.get("last_ping_at"),
        })
    return sorted(items, key=lambda a: str(a.get("session_name") or ""))


def card(accounts: list[dict[str, Any]]) -> str:
    online = sum(1 for a in accounts if a.get("status") == "online")
    problems = sum(1 for a in accounts if a.get("problem"))
    lines = [
        header("🛰", "Аккаунты", "Управление › Аккаунты"),
        f"{kv('🟢', 'Онлайн', f'{online}/{len(accounts)}')}   {kv('⚠️', 'Проблем', problems)}",
        DIV,
    ]
    if not accounts:
        lines.append(empty("Сессий не найдено."))
    for account in accounts:
        name = account.get("session_name") or "?"
        lines.append(f"{dot(bool(account.get('healthy')))} `{name}` · {account.get('status', '?')}"
                     f" · упоминаний `{account.get('pings_total', 0)}`"
                     f" · побед `{account.get('wins', 0)}`")
        if account.get("problem"):
            lines.append(f"   {account['problem']}")
        elif account.get("last_error"):
            lines.append(f"   ⚠️ __{str(account['last_error'])[:120]}__")
        if account.get("last_update_at"):
            lines.append(f"   📡 __обновления: {fmt_dt(account['last_update_at'])}__")
        if account.get("last_ping_at"):
            lines.append(f"   🕐 __последнее упоминание: {fmt_dt(account['last_ping_at'])}__")
        if account.get("spam_check"):
            lines.append(f"   🛡 __@SpamBot: {account['spam_check']}__")
    lines.append("\n" + AUTH_HINT)
    return "\n".join(lines)


def online_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The list both the buttons and the click handlers index into."""
    return [a for a in accounts if a.get("status") == "online"]


def keyboard(accounts: list[dict[str, Any]]) -> list[list[Button]]:
    # Indexed within the online accounts: the handler resolves the same list,
    # so a non-online account listed first can no longer shift the target.
    rows: list[list[Button]] = [
        [Button.inline(f"⏏️ {account.get('session_name')}"[:40], f"ac:off:{i}".encode()),
         Button.inline("🛡 Спам-блок", f"ac:spam:{i}".encode())]
        for i, account in enumerate(online_accounts(accounts))
    ]
    login = webapp_row("➕ Подключить аккаунт", "/app?s=login")
    if login:
        rows.append(login)
    rows.append([Button.inline("♻️ Перезапустить мониторинг", b"menu_restart")])
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"ac"),
    ])
    return rows


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "off":
        await _disconnect(click)
        return
    if action == "spam":
        await _spam_check(click)
        return
    accounts = await collect()
    await safe_edit(click.event, card(accounts), buttons=keyboard(accounts))


async def _target(click: Click) -> tuple[str, Any]:
    index = click.int_arg(2)
    online = online_accounts(await collect())
    if index is None or not 0 <= index < len(online):
        return "", None
    name = str(online[index].get("session_name") or "")
    client = next((c for c in state.clients if getattr(c, "_session_name_custom", "") == name), None)
    return name, client


async def _disconnect(click: Click) -> None:
    """Отключить один аккаунт. Адресуется индексом в том же порядке показа."""
    name, _client = await _target(click)
    if not name:
        await click.event.answer("Список изменился — откройте заново", alert=True)
        return
    await disconnect_account(name)
    await click.event.answer(f"Отключён: {name}")
    accounts = await collect()
    await safe_edit(click.event, card(accounts), buttons=keyboard(accounts))


async def ask_spambot(client) -> str:
    """One `/start` to @SpamBot from this account; its reply text ('' on silence)."""
    async with client.conversation(SPAMBOT, timeout=SPAMBOT_TIMEOUT_SECONDS) as conv:
        await conv.send_message("/start")
        reply = await conv.get_response()
    return str(getattr(reply, "raw_text", "") or "")


async def _spam_check(click: Click) -> None:
    name, client = await _target(click)
    if not name or client is None:
        await click.event.answer("Аккаунт не в сети — откройте список заново", alert=True)
        return
    # Answer now: the conversation can outlive the callback query.
    await click.event.answer(f"Спрашиваю @SpamBot от {name}…")
    try:
        text = await ask_spambot(client)
    except asyncio.TimeoutError:
        text = ""
    except Exception as exc:
        logger.warning("SpamBot check for %s failed: %s", name, exc)
        text = ""
    free, summary = spam_verdict(text)
    state.accounts_state.setdefault(name, {"session_name": name})["spam_check"] = (
        f"{summary} · {datetime.now():%d.%m %H:%M}"
    )
    await record_app_event("INFO" if free else "WARNING", "telegram", "SpamBot check",
                           {"session_name": name, "free": free})
    accounts = await collect()
    note = f"🛡 **@SpamBot · {name}:** {summary}"
    if text and not free:
        note += f"\n__{text[:600]}__"
    await safe_edit(click.event, note + "\n\n" + card(accounts), buttons=keyboard(accounts))


def register(router: CallbackRouter) -> None:
    router.group("ac", admin=True)(handle)
