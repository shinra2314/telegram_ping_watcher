"""Per-key access grants: which bot features a guest may open and which
notifications reach them.

A grant is a plain dict::

    {"features": ["stats", ...], "notify": ["wins", ...], "accounts": ["muver"]}

``accounts`` is a whitelist of tracked usernames — empty means "all accounts".
Grants are chosen by the owner when an access key (invite link) is created and
copied onto the member row when the key is redeemed, so revoking or editing the
key later never strips an already-onboarded guest of a working menu.

No Telethon/DB imports — everything here is unit-testable in isolation.
"""

from __future__ import annotations

import json
from typing import Any, Optional

# feature code -> (label, hint shown in the web UI)
FEATURES: dict[str, tuple[str, str]] = {
    "stats": ("📊 Статистика", "Сводка: сколько записей, новых, избранных"),
    "recent": ("🕐 Последние", "Лента последних упоминаний"),
    "search": ("🔎 Поиск", "Поиск по всей базе упоминаний"),
    "giveaways": ("🎁 Розыгрыши", "Доска розыгрышей и срочные дедлайны"),
    "market": ("💹 Курсы", "Курсы криптовалют"),
    "status": ("🛰 Статус", "Состояние аккаунтов, аптайм, размер базы"),
}

# notification code -> (label, hint)
NOTIFY_TYPES: dict[str, tuple[str, str]] = {
    "mentions": ("🔔 Упоминания", "Любое совпадение по отслеживаемым юзернеймам"),
    "giveaways": ("🎁 Розыгрыши", "Найден новый розыгрыш"),
    "wins": ("🏆 Победы", "Похоже на победу в розыгрыше"),
    "deadlines": ("⏰ Дедлайны", "Напоминания о дедлайнах"),
    "digest": ("📰 Дайджест", "Ежедневная сводка"),
}

# notification_type_of() codes -> notify grant codes
NOTIFY_TYPE_ALIASES: dict[str, str] = {
    "mention": "mentions",
    "giveaway": "giveaways",
    "win": "wins",
    "deadline": "deadlines",
    "digest": "digest",
}

# Aggregate notifications cover every tracked account in one message, so they
# cannot be narrowed to a whitelist — an account-scoped key never gets them.
AGGREGATE_TYPES = frozenset({"digest"})

ALL_FEATURES: list[str] = list(FEATURES)
ALL_NOTIFY: list[str] = list(NOTIFY_TYPES)


def full_permissions() -> dict:
    """Everything a viewer can get — the pre-grants default for legacy keys."""
    return {"features": list(ALL_FEATURES), "notify": list(ALL_NOTIFY), "accounts": []}


def _clean_codes(raw: Any, allowed: list[str]) -> list[str]:
    """Keep only known codes, in catalog order, deduplicated."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    wanted = {str(item).strip().lower() for item in raw}
    return [code for code in allowed if code in wanted]


def clean_accounts(raw: Any) -> list[str]:
    """Normalize an account whitelist: strip '@', drop blanks/dupes (case-insensitive)."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        name = str(item).strip().lstrip("@")
        if not name:
            continue
        lowered = name.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(name)
    return result


def normalize_permissions(data: Any) -> dict:
    """Coerce arbitrary input into a grant dict. Missing keys mean 'grant all'."""
    if not isinstance(data, dict):
        return full_permissions()
    features = _clean_codes(data.get("features"), ALL_FEATURES) if "features" in data else list(ALL_FEATURES)
    notify = _clean_codes(data.get("notify"), ALL_NOTIFY) if "notify" in data else list(ALL_NOTIFY)
    return {"features": features, "notify": notify, "accounts": clean_accounts(data.get("accounts"))}


def parse_permissions(raw: Optional[str]) -> dict:
    """Tolerant parse of the ``permissions`` JSON column. Empty/garbage — full access."""
    if not raw:
        return full_permissions()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return full_permissions()
    return normalize_permissions(data)


def dump_permissions(perms: dict) -> str:
    return json.dumps(normalize_permissions(perms), ensure_ascii=False)


def has_feature(perms: dict, code: str) -> bool:
    return code in (perms.get("features") or [])


def _mention_names(mentions: Any) -> set[str]:
    """Accept a list of '@name' strings or the raw JSON column value."""
    if isinstance(mentions, str):
        try:
            mentions = json.loads(mentions)
        except (ValueError, TypeError):
            mentions = [mentions]
    if not isinstance(mentions, (list, tuple, set)):
        return set()
    return {str(item).strip().lstrip("@").lower() for item in mentions if str(item).strip()}


def accounts_allowed(perms: dict, mentions: Any) -> bool:
    """True when the whitelist is empty or one of `mentions` is on it."""
    whitelist = {name.lower() for name in (perms.get("accounts") or [])}
    if not whitelist:
        return True
    return bool(whitelist & _mention_names(mentions))


def permission_allows_notification(perms: dict, notif_type: str, mentions: Any = None) -> bool:
    """Key-level gate: notification type granted, and the account whitelist matches."""
    code = NOTIFY_TYPE_ALIASES.get(notif_type, notif_type)
    if code not in allowed_pref_keys(perms):
        return False
    if code in AGGREGATE_TYPES:
        return True
    return accounts_allowed(perms, mentions)


def allowed_pref_keys(perms: dict) -> list[str]:
    """Notification toggles a member may see in their personal settings.

    Aggregate types drop out entirely once the key is scoped to specific
    accounts — one digest would otherwise expose every other account.
    """
    granted = perms.get("notify") or []
    scoped = bool(perms.get("accounts"))
    return [code for code in ALL_NOTIFY if code in granted and not (scoped and code in AGGREGATE_TYPES)]


def catalogs() -> dict:
    """Feature/notification catalogs for the web UI key builder."""
    return {
        "features": [{"code": code, "label": label, "hint": hint} for code, (label, hint) in FEATURES.items()],
        "notify": [{"code": code, "label": label, "hint": hint} for code, (label, hint) in NOTIFY_TYPES.items()],
    }


def render_permissions_summary(perms: dict) -> str:
    """Short human-readable grant description for bot messages."""
    features = perms.get("features") or []
    notify = allowed_pref_keys(perms)  # what actually fires, not just what was ticked
    accounts = perms.get("accounts") or []
    feature_line = "все разделы" if len(features) == len(ALL_FEATURES) else (
        ", ".join(FEATURES[c][0] for c in features) or "нет разделов"
    )
    notify_line = "все типы" if len(notify) == len(ALL_NOTIFY) else (
        ", ".join(NOTIFY_TYPES[c][0] for c in notify) or "отключены"
    )
    accounts_line = "все аккаунты" if not accounts else ", ".join(f"@{name}" for name in accounts)
    return "\n".join(
        [
            f"📂 Разделы: {feature_line}",
            f"🔔 Уведомления: {notify_line}",
            f"👤 Аккаунты: {accounts_line}",
        ]
    )
