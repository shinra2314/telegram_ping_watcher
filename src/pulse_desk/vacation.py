"""Vacation mode: the owner steps away, a trusted member minds the shop (pure rules).

Before a trip the owner used to hand things over by hand (handoff notes, a zip
for a friend) and still got every mention on the phone. Vacation mode is one
switch with an end date:

* the owner stops receiving minor notifications (market swings, recovery
  notices), and ping cards (mentions, giveaways, wins when ``mute_wins``) arrive
  **silently** instead of not at all — a mention is never dropped, see
  ``bot_notify.owner_card_silent``; alerts that need a decision (a dead job, a
  logged-out account) always arrive;
* an optional **delegate** — an existing member — is raised to the owner role
  for the period and receives the owner's notifications, then goes back to
  their previous role automatically when the period ends (``bot-janitor``).

The whole state is the ``vacation`` settings key.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

SETTINGS_KEY = "vacation"
PRESET_DAYS: tuple[int, ...] = (3, 7, 14)
MAX_DAYS = 60

MUTED_KINDS = frozenset({"mention", "giveaway", "market", "system"})


def normalize(raw: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {"active": False, "until": "", "started_at": "", "mute_wins": False, "delegate": None}
    if isinstance(raw, dict):
        cfg.update({k: raw[k] for k in cfg if k in raw})
    cfg["active"] = bool(cfg.get("active"))
    cfg["mute_wins"] = bool(cfg.get("mute_wins"))
    delegate = cfg.get("delegate")
    cfg["delegate"] = delegate if isinstance(delegate, dict) and delegate.get("tg_id") else None
    return cfg


def _until(cfg: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(cfg.get("until"))) if cfg.get("until") else None
    except ValueError:
        return None


def is_active(cfg: dict[str, Any], now: datetime) -> bool:
    until = _until(cfg)
    return bool(cfg.get("active")) and until is not None and now < until


def expired(cfg: dict[str, Any], now: datetime) -> bool:
    """Still flagged active, but its end date has passed — time to wind it down."""
    until = _until(cfg)
    return bool(cfg.get("active")) and until is not None and now >= until


def start(cfg: dict[str, Any], now: datetime, days: int) -> dict[str, Any]:
    days = max(1, min(MAX_DAYS, int(days)))
    return {**cfg, "active": True, "started_at": now.replace(microsecond=0).isoformat(),
            "until": (now + timedelta(days=days)).replace(microsecond=0).isoformat()}


def stop(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**cfg, "active": False}


def owner_receives(kind: str, cfg: dict[str, Any], now: datetime) -> bool:
    """Should a notification of ``kind`` still reach the owner?"""
    if not is_active(cfg, now):
        return True
    if kind in MUTED_KINDS:
        return False
    if kind == "win" and cfg.get("mute_wins"):
        return False
    return True


def delegate_receives(kind: str, cfg: dict[str, Any], now: datetime) -> Optional[int]:
    """The delegate's chat id when they should get a copy of an owner notification."""
    delegate = cfg.get("delegate")
    if not delegate or not is_active(cfg, now):
        return None
    if kind in ("market", "system"):
        return None
    return int(delegate["tg_id"])


def ends_label(cfg: dict[str, Any]) -> str:
    until = _until(cfg)
    return f"{until:%d.%m %H:%M}" if until else "—"
