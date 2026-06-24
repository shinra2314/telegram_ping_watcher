"""Shared low-level helpers for the database package.

``DB_PATH`` is owned by the package ``__init__`` so callers (and tests) can
monkeypatch ``database.DB_PATH``; everything here reads it dynamically via
``db_path()``.
"""
from __future__ import annotations

import json
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

BASE_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk.config import get_settings
    from pulse_desk.deadlines import iso_or_none, parse_claim_deadline, parse_deadline, parse_participation_deadline
    from pulse_desk.giveaways import giveaway_outcome_resolution, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule

    _settings = get_settings()
    DEFAULT_DB_PATH = _settings.db_path
    DEFAULT_BACKUP_DIR = _settings.backup_dir
except Exception:  # pragma: no cover - keeps parser tests independent from optional config deps.
    parse_deadline = None
    parse_claim_deadline = None
    parse_participation_deadline = None
    iso_or_none = lambda value: value.replace(microsecond=0).isoformat() if value else None
    is_giveaway_outcome_text = lambda text: False
    is_win_text = lambda text, keywords: False
    giveaway_outcome_resolution = lambda text: "pending"
    matches_strict_giveaway_rule = lambda text, chat_type, keywords: chat_type == "channel" and any(keyword.lower() in (text or "").lower() for keyword in keywords if keyword)
    DEFAULT_DB_PATH = Path(os.getenv("PULSE_DB_PATH", BASE_DIR / "pulse_desk.db"))
    DEFAULT_BACKUP_DIR = DEFAULT_DB_PATH.parent / "backups"

SCHEMA_VERSION = 16


def db_path() -> Path:
    """Resolve the current DB path through the package so tests can patch it."""
    import database

    return database.DB_PATH


def backup_dir() -> Path:
    import database

    return database.BACKUP_DIR


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


async def _columns(db: aiosqlite.Connection, table: str) -> set[str]:
    columns: set[str] = set()
    async with db.execute(f"PRAGMA table_info({table})") as cursor:
        async for row in cursor:
            columns.add(row[1])
    return columns


def _parse_mentions(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
            raw_items = parsed if isinstance(parsed, list) else [value]
        except json.JSONDecodeError:
            raw_items = [item.strip() for item in value.split(",")]
    else:
        raw_items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        username = str(item).strip().lstrip("@")
        if not username:
            continue
        lowered = username.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(username)
    return result


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _search_tokens(search: str, *, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[\w]+", search or "", flags=re.UNICODE)
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        clean = token.strip().strip("_").lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _fts_query(search: str) -> Optional[str]:
    tokens = _search_tokens(search)
    if not tokens:
        return None
    return " AND ".join(f'"{token}"*' for token in tokens)


async def _sync_ping_indexes(db: aiosqlite.Connection, ping_id: int, record: dict[str, Any]) -> None:
    await db.execute("DELETE FROM ping_mentions WHERE ping_id = ?", (ping_id,))
    for username in _parse_mentions(record.get("mentions")):
        await db.execute(
            "INSERT OR IGNORE INTO ping_mentions (ping_id, username) VALUES (?, ?)",
            (ping_id, username),
        )
    await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (ping_id,))
    await db.execute(
        "INSERT INTO pings_fts(rowid, chat, sender, mentions, text) VALUES (?, ?, ?, ?, ?)",
        (
            ping_id,
            record.get("chat") or "",
            record.get("sender") or "",
            " ".join(f"@{username}" for username in _parse_mentions(record.get("mentions"))),
            record.get("text") or "",
        ),
    )


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _add_where(where: list[str], params: list[Any], condition: str, *values: Any) -> None:
    where.append(condition)
    params.extend(values)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
