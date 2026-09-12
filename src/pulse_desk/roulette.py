"""Daily yobo-roulette reminder — pure scheduling logic.

The owner spins a third-party bot's roulette once a day from every account.
The cooldown runs from the *last* account's click, so the right alarm time
drifts: it is whatever time yesterday's last click happened. The owner reports
that time back and it becomes tomorrow's alarm.

No I/O here. The whole state is one JSON blob in ``app_settings`` under the key
``roulette``; the caller reads and writes it. Shape::

    {"enabled": true, "time": "12:35", "last_cycle": "2026-09-05",
     "awaiting": true, "last_click": "2026-09-05T12:41:00"}

``last_cycle`` is the date of the last *closed* day — set both when a reminder
goes out and when the owner checks in. It is what makes the loop idempotent:
no double fire inside one day, one (not five) reminders after a multi-day
outage, and a slot missed while the PC was off still fires once on startup.
That last part is why this is not a copy of ``digest_loop``, which sleeps to
its target and silently loses a slot it slept through.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .bot_prefs import parse_hhmm

# Time of the last click made before this feature existed (04.09.2026), so the
# very first reminder lands the day it ships instead of a day later.
DEFAULT_ROULETTE_TIME = "12:35"
SEED_CLICK_AT = "2026-09-04T12:35:00"

# The loop re-reads the config every tick rather than sleeping to the target:
# a time reported mid-day takes effect at once, and a missed slot catches up.
ROULETTE_POLL_SECONDS = 30


def seed_cfg() -> dict:
    """First-run config, seeded from the last click made before this feature."""
    return {
        "enabled": True,
        "time": DEFAULT_ROULETTE_TIME,
        "last_cycle": SEED_CLICK_AT[:10],
        "last_click": SEED_CLICK_AT,
        "awaiting": False,
    }


def normalize_roulette_cfg(raw: Any) -> dict:
    """Coerce a stored blob (or None, or junk) into a complete config."""
    cfg = {"enabled": True, "time": DEFAULT_ROULETTE_TIME, "last_cycle": "", "last_click": "", "awaiting": False}
    if isinstance(raw, dict):
        cfg.update(raw)
    cfg["time"] = parse_hhmm(str(cfg.get("time") or "")) or DEFAULT_ROULETTE_TIME
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["awaiting"] = bool(cfg.get("awaiting", False))
    cfg["last_cycle"] = str(cfg.get("last_cycle") or "")
    cfg["last_click"] = str(cfg.get("last_click") or "")
    return cfg


def _slot(now: datetime, cfg: dict) -> datetime:
    """Today's alarm moment for this config."""
    hhmm = parse_hhmm(str(cfg.get("time") or "")) or DEFAULT_ROULETTE_TIME
    return now.replace(hour=int(hhmm[:2]), minute=int(hhmm[3:]), second=0, microsecond=0)


def roulette_due(now: datetime, cfg: dict) -> bool:
    """Should a reminder go out right now?"""
    if not cfg.get("enabled"):
        return False
    if str(cfg.get("last_cycle") or "") == now.date().isoformat():
        return False
    return now >= _slot(now, cfg)


def next_fire_at(now: datetime, cfg: dict) -> Optional[datetime]:
    """When the next reminder is due; None when the reminder is off.

    A moment in the past means the slot is overdue — the loop fires it on its
    next tick (that is the catch-up after an overnight shutdown).
    """
    if not cfg.get("enabled"):
        return None
    slot = _slot(now, cfg)
    if str(cfg.get("last_cycle") or "") == now.date().isoformat():
        return slot + timedelta(days=1)
    return slot


def checkin_moment(now: datetime, hhmm: str) -> Optional[datetime]:
    """Resolve a reported ``HH:MM`` to a real moment; None if unparseable.

    Today at that time, or yesterday when that would still be ahead of ``now``
    — reporting a 23:50 click at 00:30 must not book the alarm for 00:30.
    """
    norm = parse_hhmm(hhmm)
    if not norm:
        return None
    moment = now.replace(hour=int(norm[:2]), minute=int(norm[3:]), second=0, microsecond=0)
    if moment > now:
        moment -= timedelta(days=1)
    return moment


def apply_checkin(cfg: dict, when: datetime) -> dict:
    """Record the click: its time becomes the alarm, and its day is closed."""
    out = dict(cfg)
    out["time"] = f"{when.hour:02d}:{when.minute:02d}"
    out["last_click"] = when.replace(second=0, microsecond=0).isoformat()
    out["last_cycle"] = when.date().isoformat()
    out["awaiting"] = False
    return out


def mark_sent(cfg: dict, now: datetime) -> dict:
    """Close the day because the reminder went out; now waiting for a time."""
    out = dict(cfg)
    out["last_cycle"] = now.date().isoformat()
    out["awaiting"] = True
    return out


def skip_today(cfg: dict, now: datetime) -> dict:
    """Close the day without a check-in: silence today, keep tomorrow's time."""
    out = dict(cfg)
    out["last_cycle"] = now.date().isoformat()
    out["awaiting"] = False
    return out
