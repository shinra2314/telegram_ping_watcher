"""Auto-delete of minor bot notifications (pure rules).

A chat that receives every mention, every market swing and every "job
recovered" notice fills up with messages nobody reads twice. Whoever switches
this on gets those deleted after a chosen number of hours. Wins and giveaways
are never auto-deleted — they are the reason the bot exists and the owner may
still need the link days later.

Telegram lets a bot delete a message in a private chat only while it is younger
than 48 hours, so the longest choice is 47 h.

The owner's choice lives in the ``autoclean`` settings key; a member's in their
``notification_prefs`` (``autoclean_hours``). The janitor job does the deleting.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

CHOICES: tuple[int, ...] = (0, 6, 24, 47)
MAX_HOURS = 47

# Notification kinds that may be auto-deleted. Everything else stays.
CLEANABLE_KINDS = frozenset({"mention", "market", "system"})

SETTINGS_KEY = "autoclean"


def clean_hours(raw: Any) -> int:
    """Coerce to one of ``CHOICES``; anything unknown means off."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value in CHOICES else 0


def next_choice(hours: Any) -> int:
    """The button cycles off → 6 h → 24 h → 47 h → off."""
    current = clean_hours(hours)
    return CHOICES[(CHOICES.index(current) + 1) % len(CHOICES)]


def label(hours: Any) -> str:
    value = clean_hours(hours)
    return f"через {value} ч" if value else "выкл"


def should_schedule(kind: str, hours: Any) -> bool:
    return clean_hours(hours) > 0 and kind in CLEANABLE_KINDS


def delete_at(now: datetime, hours: Any) -> datetime:
    return now + timedelta(hours=min(MAX_HOURS, clean_hours(hours)))


def owner_hours(cfg: Any) -> int:
    return clean_hours((cfg or {}).get("hours") if isinstance(cfg, dict) else 0)
