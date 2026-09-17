"""Health of the Telegram accounts that do the watching (pure rules).

An account that got logged out, banned or stuck reconnecting stops seeing its
channels, and nothing told the owner: the status only changed on the accounts
screen nobody opens. The ``account-health`` job applies these rules every few
minutes and pages the owner once per problem (and once when it clears).

Problems, in the order they are checked:

* the session is gone — ``unauthorized`` (logged out elsewhere, session revoked)
* the account is banned — ``banned``
* the session is used from another IP at once — ``session_ip_conflict``
* a start failed with an error — ``error``
* reconnecting / rate-limited / connecting for more than ``STUCK_MINUTES``
* online, but no update for ``SILENT_HOURS`` — connected but deaf

@SpamBot's answer is classified by ``spam_verdict``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

STUCK_MINUTES = 30
SILENT_HOURS = 12

HARD_PROBLEMS = {
    "unauthorized": "🔒 разлогинен — сессия больше не действует",
    "banned": "⛔ заблокирован Telegram",
    "session_ip_conflict": "🔑 сессия открыта с другого IP — Telegram её отклоняет",
    "error": "⚠️ не запускается",
}
STUCK_STATUSES = {
    "reconnecting": "переподключается",
    "rate_limited": "ждёт FloodWait",
    "connecting": "подключается",
}

# Telethon error class names → the status they mean.
_AUTH_ERRORS = {
    "AuthKeyUnregisteredError": "unauthorized",
    "SessionRevokedError": "unauthorized",
    "SessionExpiredError": "unauthorized",
    "UnauthorizedError": "unauthorized",
    "UserDeactivatedError": "banned",
    "UserDeactivatedBanError": "banned",
    "PhoneNumberBannedError": "banned",
}


def _ts(raw: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def classify_auth_error(exc: BaseException) -> Optional[str]:
    """The account status an exception from a probe means, or None for a transient error."""
    for cls in type(exc).__mro__:
        status = _AUTH_ERRORS.get(cls.__name__)
        if status:
            return status
    return None


def account_problem(account: dict[str, Any], now: datetime, *,
                    app_started_at: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    """``(kind, text)`` for one account, or None when it is fine.

    ``kind`` identifies the problem for de-duplication: a reconnect that has
    been stuck for 31 and then 45 minutes is one alert, not two.
    """
    status = str(account.get("status") or "")
    if status in HARD_PROBLEMS:
        detail = HARD_PROBLEMS[status]
        error = str(account.get("last_error") or "").strip()
        return status, (f"{detail}: {error[:120]}" if error and status == "error" else detail)
    if status in STUCK_STATUSES:
        since = _ts(account.get("status_since")) or _ts(account.get("disconnected_at")) or _ts(account.get("connecting_at"))
        if since and now - since >= timedelta(minutes=STUCK_MINUTES):
            minutes = int((now - since).total_seconds() // 60)
            return "stuck", f"⏳ {STUCK_STATUSES[status]} уже {minutes} мин"
        return None
    if status == "online":
        connected = _ts(account.get("connected_at"))
        last = _ts(account.get("last_update_at")) or connected
        if app_started_at and now - app_started_at < timedelta(hours=SILENT_HOURS):
            return None
        if last and now - last >= timedelta(hours=SILENT_HOURS):
            hours = int((now - last).total_seconds() // 3600)
            return "silent", f"🔇 в сети, но {hours} ч без обновлений"
    return None


def diff_problems(previous: dict[str, str], current: dict[str, tuple[str, str]]
                  ) -> tuple[dict[str, str], list[str]]:
    """(accounts with a new kind of problem → text, accounts that recovered).

    ``previous`` maps account → problem kind already reported.
    """
    fresh = {name: text for name, (kind, text) in current.items() if previous.get(name) != kind}
    recovered = [name for name in previous if name not in current]
    return fresh, recovered


def spam_verdict(text: str) -> tuple[bool, str]:
    """(free of limits, short summary) from @SpamBot's reply."""
    lowered = (text or "").lower()
    free_markers = ("no limits", "free as a bird", "свободен от каких-либо ограничений",
                    "нет ограничений", "good news")
    if any(marker in lowered for marker in free_markers):
        return True, "✅ ограничений нет"
    if not lowered.strip():
        return False, "❔ @SpamBot не ответил"
    return False, "⚠️ есть ограничения (спам-блок)"
