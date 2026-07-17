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
    "checks": True,
    "deadlines": False,
    "digest": False,
    # Minimum giveaway candidate score a member wants to see (0 = everything).
    "min_score": 0,
}

# callback scope code -> (settings key, display label)
KEYWORD_SCOPES = {
    "w": ("win_keywords", "🏆 Победы"),
    "g": ("giveaway_keywords", "🎁 Розыгрыши"),
    "c": ("check_keywords", "💸 Чеки"),
    "h": ("high_priority_keywords", "⚡ Приоритет"),
    "i": ("ignore_keywords", "🚫 Игнор"),
}

_TYPE_TO_PREF = {
    "mention": "mentions",
    "giveaway": "giveaways",
    "win": "wins",
    "check": "checks",
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
            if key not in data:
                continue
            if key == "min_score":
                try:
                    prefs[key] = max(0, min(100, int(data[key])))
                except (TypeError, ValueError):
                    pass
            else:
                prefs[key] = bool(data[key])
    return prefs


def toggle_member_pref(prefs: dict, key: str) -> dict:
    """Return a new prefs dict with `key` flipped; unknown key — unchanged copy."""
    updated = dict(DEFAULT_MEMBER_PREFS)
    updated.update(prefs)
    if key in DEFAULT_MEMBER_PREFS and key != "min_score":
        updated[key] = not bool(updated.get(key))
    return updated


def notification_type_of(record: dict) -> str:
    if record.get("is_win"):
        return "win"
    if record.get("is_giveaway"):
        return "giveaway"
    return "mention"


def member_allows(prefs: dict, notif_type: str, score: Optional[int] = None) -> bool:
    if prefs.get("muted"):
        return False
    pref_key = _TYPE_TO_PREF.get(notif_type)
    if pref_key is None:
        return True
    if not prefs.get(pref_key, DEFAULT_MEMBER_PREFS.get(pref_key, True)):
        return False
    # Personal quality bar: giveaways below the member's min_score are skipped.
    # Unknown score (None) always passes — never hide what we can't rate.
    if notif_type == "giveaway" and score is not None:
        try:
            min_score = int(prefs.get("min_score") or 0)
        except (TypeError, ValueError):
            min_score = 0
        if min_score > 0 and int(score) < min_score:
            return False
    return True


def filter_broadcast_members(
    members: list[dict],
    notif_type: str,
    admin_ids: set[int],
    score: Optional[int] = None,
    premium_only: Optional[bool] = None,
) -> list[dict]:
    """Members eligible for a broadcast of `notif_type`: not blocked, not admin, prefs allow.

    `premium_only`: True — only premium members, False — only non-premium, None — everyone.
    """
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
        is_premium = (member.get("role") or "viewer") == "premium"
        if premium_only is True and not is_premium:
            continue
        if premium_only is False and is_premium:
            continue
        prefs = parse_member_prefs(member.get("notification_prefs"))
        if member_allows(prefs, notif_type, score=score):
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


_WEEKDAYS = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}


def parse_weekday_spec(text: str) -> list[int]:
    """'mon-fri' -> [1..5]; 'sat,sun' -> [6,7]; wraps (fri-mon); [] if invalid."""
    text = (text or "").strip().lower()
    if not text:
        return []
    result: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            if a not in _WEEKDAYS or b not in _WEEKDAYS:
                return []
            start, end = _WEEKDAYS[a], _WEEKDAYS[b]
            seq = range(start, end + 1) if start <= end else list(range(start, 8)) + list(range(1, end + 1))
            result.update(seq)
        elif part in _WEEKDAYS:
            result.add(_WEEKDAYS[part])
        else:
            return []
    return sorted(result)


def parse_duration_to_seconds(text: str) -> Optional[int]:
    """'2h' -> 7200, '30m' -> 1800, '1d' -> 86400, '1h30m' -> 5400. None if invalid."""
    text = re.sub(r"\s+", "", (text or "").strip().lower())
    if not text:
        return None
    matches = re.findall(r"(\d+)([dhm])", text)
    if not matches or "".join(f"{n}{u}" for n, u in matches) != text:
        return None
    unit = {"d": 86400, "h": 3600, "m": 60}
    return sum(int(n) * unit[u] for n, u in matches)


def next_hhmm_datetime(now: datetime, hhmm: str) -> Optional[datetime]:
    """Next occurrence of HH:MM at/after `now` (today if future, else tomorrow); None if invalid."""
    norm = parse_hhmm(hhmm)
    if not norm:
        return None
    target = now.replace(hour=int(norm[:2]), minute=int(norm[3:]), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


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
    moderated = settings.get("moderation_mode") == "moderated"
    timeout_min = max(1, int(settings.get("approval_timeout_seconds") or 300) // 60)
    moderation_line = (
        f"Модерация рассылок: 🛡 вкл (авто-отправка через {timeout_min} мин)"
        if moderated
        else "Модерация рассылок: 📤 авто (сразу всем)"
    )
    return "\n".join(
        [
            "🔔 **Настройки уведомлений**",
            "",
            f"Уведомления: {_onoff(settings.get('enabled', True))}",
            f"Розыгрыши: {_onoff(settings.get('include_giveaways', True))}",
            f"Победы: {_onoff(settings.get('include_wins', True))}",
            moderation_line,
            f"Тихие часы: {quiet_state}{quiet_range}",
            f"Кулдаун: {int(settings.get('cooldown_seconds') or 0)} сек",
            "",
            f"📰 Дайджест: {_onoff(digest_cfg.get('enabled', True))} в {digest_cfg.get('time', '09:00')}",
        ]
    )


def render_member_prefs_text(prefs: dict) -> str:
    def mark(key: str) -> str:
        return "✅" if prefs.get(key) else "🔕"

    min_score = int(prefs.get("min_score") or 0)
    score_line = f"🎯 Мин. score розыгрышей: {min_score}" if min_score else "🎯 Мин. score розыгрышей: любой"
    return "\n".join(
        [
            "🔔 **Мои уведомления**",
            "",
            f"{'🔕 Всё отключено' if prefs.get('muted') else '🔔 Уведомления включены'}",
            "",
            f"{mark('mentions')} Упоминания",
            f"{mark('giveaways')} Розыгрыши",
            f"{mark('wins')} Победы",
            f"{mark('checks')} Чеки",
            f"{mark('deadlines')} Дедлайны",
            f"{mark('digest')} Ежедневный дайджест",
            score_line,
        ]
    )
