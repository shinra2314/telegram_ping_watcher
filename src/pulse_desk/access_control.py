"""Pure schedule-resolution logic for per-member bot access.

No Telethon/DB/I-O imports — everything here is a pure function of its inputs,
so the tricky bits (timezones, DST, midnight wrap, overlap priority) are fully
unit-testable. The database layer feeds rows in via ``window_from_row`` and the
bot layer asks ``resolve_access`` whether a member may act *right now*.

Time model: callers pass an **aware UTC** ``now``. Window wall-clock ranges are
interpreted in each window's IANA ``tz`` via ``zoneinfo`` (needs the ``tzdata``
package on Windows). Absolute ``start_at``/``end_at`` are aware UTC instants.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

DEFAULT_POLICY = "allow"
_DEFAULT_REPEAT = {"type": "none"}


@dataclass(frozen=True)
class Window:
    """One access rule. ``enabled`` is the *kind*: True = window grants access,
    False = window denies (blackout)."""

    id: int
    enabled: bool
    active: bool = True
    start_at: Optional[datetime] = None  # aware UTC, or None = unbounded
    end_at: Optional[datetime] = None    # aware UTC, or None = unbounded
    tz: str = "UTC"
    repeat: dict = field(default_factory=lambda: dict(_DEFAULT_REPEAT))
    priority: int = 100
    updated_at: str = ""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    until: Optional[datetime]  # when this decision may stop holding (cache TTL)


def parse_repeat_rule(raw: Any) -> dict:
    """Tolerant parse of the repeat_rule column into a dict; junk -> {'type':'none'}."""
    if isinstance(raw, dict):
        return raw or dict(_DEFAULT_REPEAT)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return dict(_DEFAULT_REPEAT)
        if isinstance(data, dict) and data:
            return data
    return dict(_DEFAULT_REPEAT)


def _as_utc(value: Any) -> Optional[datetime]:
    """Coerce an ISO string / datetime to an aware UTC datetime (naive => UTC)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def window_from_row(row: dict) -> Window:
    """Build a Window from a DB row (handles ISO strings + repeat JSON + 0/1 flags)."""
    return Window(
        id=int(row["id"]),
        enabled=bool(row.get("enabled")),
        active=bool(row.get("active", 1)),
        start_at=_as_utc(row.get("start_at")),
        end_at=_as_utc(row.get("end_at")),
        tz=(row.get("timezone") or "UTC"),
        repeat=parse_repeat_rule(row.get("repeat_rule")),
        priority=int(row.get("priority", 100) or 0),
        updated_at=str(row.get("updated_at") or ""),
    )


def _hhmm(value: str, default: str) -> str:
    return value if value else default


def _in_daily_range(cur: str, frm: str, to: str) -> bool:
    """`cur`/`frm`/`to` are 'HH:MM'. End exclusive; frm > to wraps past midnight."""
    if frm <= to:
        return frm <= cur < to
    return cur >= frm or cur < to


def window_contains(w: Window, now_utc: datetime) -> bool:
    if not w.active:
        return False
    if w.start_at is not None and now_utc < w.start_at:
        return False
    if w.end_at is not None and now_utc >= w.end_at:
        return False

    rtype = (w.repeat or {}).get("type", "none")
    if rtype == "none":
        # A pure absolute window — only meaningful if at least one bound is set.
        return w.start_at is not None or w.end_at is not None

    local = now_utc.astimezone(ZoneInfo(w.tz))
    frm = _hhmm((w.repeat or {}).get("from", ""), "00:00")
    to = _hhmm((w.repeat or {}).get("to", ""), "23:59")
    cur = local.strftime("%H:%M")

    if rtype == "daily":
        return _in_daily_range(cur, frm, to)

    if rtype == "weekly":
        days = set((w.repeat or {}).get("days") or [])
        today = local.isoweekday()
        if frm <= to:
            return today in days and frm <= cur < to
        # Overnight window belongs to its *start* day: today after `frm`,
        # or yesterday's window spilling past midnight (cur < `to`).
        yesterday = (local - timedelta(days=1)).isoweekday()
        return (today in days and cur >= frm) or (yesterday in days and cur < to)

    if rtype == "cron":
        try:
            from croniter import croniter
        except ImportError:
            return False
        expr = (w.repeat or {}).get("expr")
        if not expr:
            return False
        dur = timedelta(minutes=int((w.repeat or {}).get("dur_min", 1) or 1))
        prev = croniter(expr, local).get_prev(datetime)
        return prev <= local < prev + dur

    return False


def resolve_access(member: dict, windows: list[Window], now_utc: datetime) -> Decision:
    """Decide whether `member` may act at `now_utc`.

    Precedence: hard block (veto) -> matching windows (highest priority, newest
    on tie) -> member default policy.
    """
    if member.get("blocked"):
        return Decision(False, "blocked", None)

    until = next_boundary(windows, now_utc)
    matched = [w for w in windows if window_contains(w, now_utc)]
    if matched:
        winner = max(matched, key=lambda w: (w.priority, w.updated_at))
        return Decision(bool(winner.enabled), f"window#{winner.id}", until)

    allowed = (member.get("access_default_policy") or DEFAULT_POLICY) == "allow"
    return Decision(allowed, "default_policy", until)


# Audit actions a user can roll back (scheduler 'flip' and prior 'undo' are not).
_REVERSIBLE_ACTIONS = {"create", "manual_off", "delete", "manual_on", "policy"}


def _audit_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def find_undoable(audit_rows: list[dict]) -> Optional[dict]:
    """Newest user-reversible audit row (rows newest-first), skipping already-undone ones."""
    undone: set[int] = set()
    for row in audit_rows:
        if row.get("action") == "undo":
            uid = _audit_json(row.get("new_value")).get("undone_audit_id")
            if uid is not None:
                undone.add(int(uid))
    for row in audit_rows:
        if row.get("action") in _REVERSIBLE_ACTIONS and int(row.get("id")) not in undone:
            return row
    return None


def plan_undo(row: dict) -> dict:
    """Map an audit row to the reversal operation that cancels it."""
    action = row.get("action")
    sid = row.get("schedule_id")
    if action in ("create", "manual_off"):
        return {"op": "deactivate", "schedule_id": sid}
    if action == "delete":
        return {"op": "reactivate", "schedule_id": sid}
    if action == "manual_on":
        ids = _audit_json(row.get("new_value")).get("cancelled_ids") or []
        return {"op": "reactivate_many", "schedule_ids": [int(i) for i in ids]}
    if action == "policy":
        return {"op": "set_policy", "policy": _audit_json(row.get("old_value")).get("policy", "allow")}
    return {"op": "noop"}


def next_boundary(windows: list[Window], now_utc: datetime, horizon_hours: int = 48) -> datetime:
    """First minute in the future where any window's membership flips.

    Used as a cache TTL: the access decision cannot change before this instant.
    Minute-resolution scan (cheap; DST/midnight handled by ``window_contains``).
    Falls back to ``now + horizon`` when nothing changes within the horizon.
    """
    horizon = now_utc + timedelta(hours=horizon_hours)
    if not windows:
        return horizon
    base = [window_contains(w, now_utc) for w in windows]
    step = timedelta(minutes=1)
    t = now_utc + step
    while t <= horizon:
        if [window_contains(w, t) for w in windows] != base:
            return t
        t += step
    return horizon
