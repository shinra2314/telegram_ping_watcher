"""Pure helpers for per-member bot notification preferences and settings menus.

No Telethon/DB imports — everything here is unit-testable in isolation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

DEFAULT_MEMBER_PREFS = {
    "muted": False,
    "mentions": True,
    "giveaways": True,
    "wins": True,
    "deadlines": False,
    "digest": False,
}

# callback scope code -> (settings key, display label)
KEYWORD_SCOPES = {
    "w": ("win_keywords", "🏆 Победы"),
    "g": ("giveaway_keywords", "🎁 Розыгрыши"),
    "h": ("high_priority_keywords", "⚡ Приоритет"),
    "i": ("ignore_keywords", "🚫 Игнор"),
}

_TYPE_TO_PREF = {
    "mention": "mentions",
    "giveaway": "giveaways",
    "win": "wins",
    "deadline": "deadlines",
    "digest": "digest",
}


def parse_member_prefs(raw: Optional[str]) -> dict:
    """Tolerant parse of the notification_prefs JSON column over defaults."""
    prefs = dict(DEFAULT_MEMBER_PREFS)
    if not raw:
        return prefs
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return prefs
    if isinstance(data, dict):
        for key in prefs:
            if key in data:
                prefs[key] = bool(data[key])
    return prefs


def toggle_member_pref(prefs: dict, key: str) -> dict:
    """Return a new prefs dict with `key` flipped; unknown key — unchanged copy."""
    updated = dict(DEFAULT_MEMBER_PREFS)
    updated.update(prefs)
    if key in DEFAULT_MEMBER_PREFS:
        updated[key] = not bool(updated.get(key))
    return updated


def notification_type_of(record: dict) -> str:
    if record.get("is_win"):
        return "win"
    if record.get("is_giveaway"):
        return "giveaway"
    return "mention"


def member_allows(prefs: dict, notif_type: str) -> bool:
    if prefs.get("muted"):
        return False
    pref_key = _TYPE_TO_PREF.get(notif_type)
    if pref_key is None:
        return True
    return bool(prefs.get(pref_key, DEFAULT_MEMBER_PREFS.get(pref_key, True)))


def filter_broadcast_members(members: list[dict], notif_type: str, admin_ids: set[int]) -> list[dict]:
    """Members eligible for a broadcast of `notif_type`: not blocked, not admin, prefs allow."""
    result = []
    for member in members:
        if member.get("blocked"):
            continue
        try:
            tg_id = int(member.get("tg_id"))
        except (TypeError, ValueError):
            continue
        if tg_id in admin_ids:
            continue
        prefs = parse_member_prefs(member.get("notification_prefs"))
        if member_allows(prefs, notif_type):
            result.append(member)
    return result


def parse_hhmm(text: str) -> Optional[str]:
    """Validate/normalize 'H:M' input to 'HH:MM'; None if invalid."""
    match = re.fullmatch(r"\s*(\d{1,2})[:.](\d{1,2})\s*", text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def seconds_until_hhmm(now: datetime, hhmm: str) -> float:
    """Seconds until the next occurrence of HH:MM; invalid input falls back to 09:00."""
    normalized = parse_hhmm(hhmm) or "09:00"
    hour, minute = int(normalized[:2]), int(normalized[3:])
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def parse_quiet_hours_input(text: str) -> Optional[tuple[str, str]]:
    """Parse '23:00-08:00' into ('23:00', '08:00'); None if invalid."""
    parts = (text or "").split("-")
    if len(parts) != 2:
        return None
    start, end = parse_hhmm(parts[0]), parse_hhmm(parts[1])
    if start is None or end is None:
        return None
    return start, end


def render_tracking_text(usernames: list[str]) -> str:
    lines = ["👁 **Отслеживаемые юзернеймы**", ""]
    if usernames:
        lines.extend(f"{i}. @{name.lstrip('@')}" for i, name in enumerate(usernames, 1))
    else:
        lines.append("__Список пуст.__")
    return "\n".join(lines)


def render_keyword_list_text(label: str, items: list[str]) -> str:
    lines = [f"{label} — **ключевые слова**", ""]
    if items:
        lines.extend(f"{i}. {item}" for i, item in enumerate(items, 1))
    else:
        lines.append("__Список пуст.__")
    return "\n".join(lines)


def _onoff(value: Any) -> str:
    return "✅ вкл" if value else "❌ выкл"


def render_notification_settings_text(settings: dict, digest_cfg: dict) -> str:
    quiet = settings.get("quiet_hours") or {}
    quiet_state = _onoff(quiet.get("enabled"))
    quiet_range = f" ({quiet.get('from', '—')}–{quiet.get('to', '—')})" if quiet.get("enabled") else ""
    return "\n".join(
        [
            "🔔 **Настройки уведомлений**",
            "",
            f"Уведомления: {_onoff(settings.get('enabled', True))}",
            f"Розыгрыши: {_onoff(settings.get('include_giveaways', True))}",
            f"Победы: {_onoff(settings.get('include_wins', True))}",
            f"Тихие часы: {quiet_state}{quiet_range}",
            f"Кулдаун: {int(settings.get('cooldown_seconds') or 0)} сек",
            "",
            f"📰 Дайджест: {_onoff(digest_cfg.get('enabled', True))} в {digest_cfg.get('time', '09:00')}",
        ]
    )


def render_member_prefs_text(prefs: dict) -> str:
    def mark(key: str) -> str:
        return "✅" if prefs.get(key) else "🔕"

    return "\n".join(
        [
            "🔔 **Мои уведомления**",
            "",
            f"{'🔕 Всё отключено' if prefs.get('muted') else '🔔 Уведомления включены'}",
            "",
            f"{mark('mentions')} Упоминания",
            f"{mark('giveaways')} Розыгрыши",
            f"{mark('wins')} Победы",
            f"{mark('deadlines')} Дедлайны",
            f"{mark('digest')} Ежедневный дайджест",
        ]
    )
