"""File-level SQLite backups: consistent snapshots, zipped, rotated by age.

A backup used to be ``shutil.copy2`` of the main file. In WAL mode that copy
misses every transaction still sitting in ``-wal`` — and if the previous process
was killed mid-checkpoint (the PC is switched off nightly), the main file alone
is not even consistent. ``sqlite3.Connection.backup`` reads through the WAL and
gives a point-in-time snapshot, safe while the app keeps writing.

Copies are zipped (a 21 MB database packs to a few MB, small enough for the
bot to send) and rotated by ``pulse_desk.housekeeping.backups_to_delete``.
"""
from __future__ import annotations

import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ._core import BASE_DIR, _env_int, backup_dir, db_path  # _core puts src/ on sys.path

from pulse_desk.housekeeping import (
    KEEP_RECENT,
    MB,
    BackupItem,
    backup_created_at,
    backup_needed,
    backups_to_delete,
)


def _effective_backup_dir() -> Path:
    path = db_path()
    return backup_dir() if path.parent == BASE_DIR else path.parent / "backups"


def _backup_files(target_dir: Path) -> list[tuple[Path, datetime, int]]:
    """Every backup of the current DB, old plain ``.db`` copies included."""
    if not target_dir.exists():
        return []
    stem = db_path().stem
    found: list[tuple[Path, datetime, int]] = []
    for pattern in (f"{stem}_*.db", f"{stem}_*.db.zip"):
        for path in target_dir.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            created = backup_created_at(path.name) or datetime.fromtimestamp(stat.st_mtime)
            found.append((path, created, stat.st_size))
    found.sort(key=lambda row: row[1], reverse=True)
    return found


def _snapshot(source: Path, target_zip: Path) -> None:
    """Consistent copy of ``source`` packed into ``target_zip`` (atomic rename)."""
    raw = target_zip.with_suffix("")  # pulse_desk_….db
    part = target_zip.with_name(target_zip.name + ".part")
    try:
        with closing(sqlite3.connect(str(source), timeout=30)) as src, \
                closing(sqlite3.connect(str(raw))) as dst:
            src.backup(dst)
        with zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(raw, arcname=raw.name)
        part.replace(target_zip)
    finally:
        raw.unlink(missing_ok=True)
        part.unlink(missing_ok=True)


def _create(target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = target_dir / f"{db_path().stem}_{stamp}.db.zip"
    _snapshot(db_path(), target)
    return target


def prune_db_backups(retention: Optional[int] = None, *, now: Optional[datetime] = None) -> list[str]:
    """Apply the rotation; returns the names removed.

    ``BACKUP_RETENTION=0`` still disables deletion entirely, as it always did.
    A positive value caps how many of the newest copies are kept unconditionally.
    """
    retention = _env_int("BACKUP_RETENTION", 10) if retention is None else retention
    if retention <= 0:
        return []
    target_dir = _effective_backup_dir()
    files = _backup_files(target_dir)
    items = [BackupItem(path.name, created, size) for path, created, size in files]
    doomed = backups_to_delete(
        items,
        now or datetime.now(),
        keep_recent=min(KEEP_RECENT, retention),
        max_total_bytes=max(0, _env_int("BACKUP_MAX_TOTAL_MB", 1024)) * MB,
    )
    for name in doomed:
        (target_dir / name).unlink(missing_ok=True)
    return doomed


def backup_db_if_present(retention: Optional[int] = None) -> None:
    """Startup backup, skipped when a recent enough copy already exists."""
    if not db_path().exists():
        return
    target_dir = _effective_backup_dir()
    files = _backup_files(target_dir)
    newest = files[0][1] if files else None
    if backup_needed(newest, datetime.now(), _env_int("BACKUP_MIN_INTERVAL_HOURS", 6)):
        _create(target_dir)
    prune_db_backups(retention)


def create_db_backup() -> Optional[dict[str, Any]]:
    """Backup on demand (bot button, daily maintenance). Blocking — call in a thread."""
    if not db_path().exists():
        return None
    target = _create(_effective_backup_dir())
    prune_db_backups()
    stat = target.stat()
    return {
        "name": target.name,
        "path": str(target),
        "size": stat.st_size,
        "created_at": (backup_created_at(target.name) or datetime.now()).replace(microsecond=0).isoformat(),
    }


def newest_backup_at() -> Optional[datetime]:
    files = _backup_files(_effective_backup_dir())
    return files[0][1] if files else None


def backups_total_bytes() -> int:
    return sum(size for _path, _created, size in _backup_files(_effective_backup_dir()))


def list_db_backups(limit: int = 50) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, created, size in _backup_files(_effective_backup_dir())[:limit]:
        rows.append({
            "name": path.name,
            "path": str(path),
            "size": size,
            "created_at": created.replace(microsecond=0).isoformat(),
        })
    return rows
