"""File-level SQLite backup helpers."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ._core import BASE_DIR, _env_int, backup_dir, db_path


def _effective_backup_dir() -> Path:
    path = db_path()
    return backup_dir() if path.parent == BASE_DIR else path.parent / "backups"


def backup_db_if_present(retention: Optional[int] = None) -> None:
    path = db_path()
    if not path.exists():
        return
    target_dir = _effective_backup_dir()
    target_dir.mkdir(exist_ok=True)
    backup_path = target_dir / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    retention = _env_int("BACKUP_RETENTION", 10) if retention is None else retention
    if retention > 0:
        backups = sorted(target_dir.glob(f"{path.stem}_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)
        for old_backup in backups[retention:]:
            old_backup.unlink(missing_ok=True)


def create_db_backup() -> Optional[dict[str, Any]]:
    path = db_path()
    if not path.exists():
        return None
    target_dir = _effective_backup_dir()
    target_dir.mkdir(exist_ok=True)
    backup_path = target_dir / f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(path, backup_path)
    return {
        "name": backup_path.name,
        "path": str(backup_path),
        "size": backup_path.stat().st_size,
        "created_at": datetime.fromtimestamp(backup_path.stat().st_mtime).replace(microsecond=0).isoformat(),
    }


def list_db_backups(limit: int = 50) -> list[dict[str, Any]]:
    target_dir = _effective_backup_dir()
    if not target_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(target_dir.glob(f"{db_path().stem}_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        rows.append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
        })
    return rows
