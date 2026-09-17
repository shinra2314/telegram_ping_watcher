"""Pure housekeeping decisions: when to VACUUM, which backups to drop, disk alerts.

No I/O — the ``maintenance`` job in ``loops.py`` and ``database/backups.py``
gather the facts (page counts, backup files, free bytes) and act on what these
functions return, so every rule here unit-tests without a database or a disk.

Two facts shaped the rules:

* The PC is switched off every night, so anything timed "every N hours since
  the process started" never fires. ``VACUUM_INTERVAL_HOURS=168`` counted from
  startup left 114 MB of free pages inside a 136 MB file. VACUUM is therefore
  triggered by how much of the file is actually empty, with a stored timestamp
  as the fallback.
* A backup was taken on every start and only the newest ten were kept, so a
  day of restarts wiped out every older copy. Rotation is now by age tiers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MB = 1024 * 1024

# VACUUM when this share of the file is free pages and it is worth the rewrite.
VACUUM_FREE_RATIO = 0.20
VACUUM_MIN_FREE_BYTES = 16 * MB

KEEP_RECENT = 3
KEEP_DAILY = 7
KEEP_WEEKLY = 4

# File rules: (directory relative to the app root, glob, days to keep). Only
# files the app itself produces belong here. Session files are secrets and are
# never removed automatically.
FILE_RULES: tuple[tuple[str, str, int], ...] = (
    ("data/cards", "*.png", 1),
    ("data", "telegram_login_*.svg", 1),
    ("logs", "codex-*.log", 7),
)

_BACKUP_STAMP = re.compile(r"_(\d{8}_\d{6})\.db(?:\.zip)?$")


@dataclass(frozen=True)
class BackupItem:
    name: str
    created_at: datetime
    size: int


def backup_created_at(name: str) -> Optional[datetime]:
    """Timestamp from ``pulse_desk_YYYYmmdd_HHMMSS.db[.zip]``.

    The file mtime is not reliable: ``shutil.copy2`` used to copy the source
    DB's mtime onto the backup, so an old copy can claim to be brand new.
    """
    match = _BACKUP_STAMP.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    except ValueError:
        return None


def vacuum_due(
    *,
    page_count: int,
    freelist_count: int,
    page_size: int,
    last_vacuum_at: Optional[datetime],
    now: datetime,
    interval_hours: int = 168,
) -> bool:
    """Whether the main DB deserves a VACUUM right now."""
    if page_count <= 0:
        return False
    free_bytes = max(0, freelist_count) * max(0, page_size)
    if free_bytes >= VACUUM_MIN_FREE_BYTES and freelist_count / page_count >= VACUUM_FREE_RATIO:
        return True
    if interval_hours <= 0 or freelist_count <= 0:
        return False
    if last_vacuum_at is None:
        return True
    return now - last_vacuum_at >= timedelta(hours=interval_hours)


def backup_needed(newest_backup_at: Optional[datetime], now: datetime, min_interval_hours: int = 6) -> bool:
    """A new startup backup only when the newest one is old enough.

    Without this, a burst of restarts replaced the whole backup history with
    copies of the same state taken minutes apart.
    """
    if newest_backup_at is None or min_interval_hours <= 0:
        return True
    return now - newest_backup_at >= timedelta(hours=min_interval_hours)


def backups_to_delete(
    items: list[BackupItem],
    now: datetime,
    *,
    keep_recent: int = KEEP_RECENT,
    keep_daily: int = KEEP_DAILY,
    keep_weekly: int = KEEP_WEEKLY,
    max_total_bytes: int = 0,
) -> list[str]:
    """Names of backups to remove: grandfather-father-son rotation plus a size cap.

    Kept: the ``keep_recent`` newest copies, the newest copy of each of the last
    ``keep_daily`` days, and the newest copy of each of the ``keep_weekly`` most
    recent ISO weeks older than that. Then, while the kept set is over
    ``max_total_bytes``, the oldest kept copy goes — but the newest copy is
    never removed, whatever its size.
    """
    ordered = sorted(items, key=lambda item: item.created_at, reverse=True)
    if not ordered:
        return []
    keep: list[BackupItem] = list(ordered[:max(1, keep_recent)])

    today = now.date()
    daily_from = today - timedelta(days=max(0, keep_daily - 1))
    seen_days: set = set()
    for item in ordered:
        day = item.created_at.date()
        if daily_from <= day <= today and day not in seen_days:
            seen_days.add(day)
            if item not in keep:
                keep.append(item)

    seen_weeks: list[tuple[int, int]] = []
    for item in ordered:
        if item.created_at.date() >= daily_from:
            continue
        week = tuple(item.created_at.isocalendar()[:2])
        if week in seen_weeks:
            continue
        if len(seen_weeks) >= keep_weekly:
            break
        seen_weeks.append(week)
        if item not in keep:
            keep.append(item)

    if max_total_bytes > 0:
        keep.sort(key=lambda item: item.created_at, reverse=True)
        while len(keep) > 1 and sum(item.size for item in keep) > max_total_bytes:
            keep.pop()

    kept_names = {item.name for item in keep}
    return [item.name for item in ordered if item.name not in kept_names]


def disk_low(free_bytes: int, threshold_mb: int) -> bool:
    """True when free space on the app's disk is below the alert threshold."""
    return threshold_mb > 0 and free_bytes < threshold_mb * MB


def freelist_ratio(page_count: int, freelist_count: int) -> float:
    return freelist_count / page_count if page_count > 0 else 0.0


def gap_worth_reporting(last_alive_at: Optional[datetime], started_at: datetime, min_hours: float = 2.0) -> bool:
    """Whether the downtime before this start is long enough to summarise.

    ``last_alive_at`` is the last heartbeat the previous process stored; a
    routine restart is minutes, the nightly shutdown is hours.
    """
    if last_alive_at is None or started_at <= last_alive_at:
        return False
    return started_at - last_alive_at >= timedelta(hours=min_hours)


def format_duration(delta: timedelta) -> str:
    """``9 ч 16 мин`` / ``45 мин``."""
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    return f"{minutes} мин"
