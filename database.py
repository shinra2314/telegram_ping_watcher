from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

import aiosqlite

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk.config import get_settings
    from pulse_desk.deadlines import iso_or_none, parse_claim_deadline, parse_deadline, parse_participation_deadline
    from pulse_desk.giveaways import giveaway_outcome_resolution, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule

    _settings = get_settings()
    DB_PATH = _settings.db_path
    BACKUP_DIR = _settings.backup_dir
except Exception:  # pragma: no cover - keeps parser tests independent from optional config deps.
    parse_deadline = None
    parse_claim_deadline = None
    parse_participation_deadline = None
    iso_or_none = lambda value: value.replace(microsecond=0).isoformat() if value else None
    is_giveaway_outcome_text = lambda text: False
    is_win_text = lambda text, keywords: False
    giveaway_outcome_resolution = lambda text: "pending"
    matches_strict_giveaway_rule = lambda text, chat_type, keywords: chat_type == "channel" and any(keyword.lower() in (text or "").lower() for keyword in keywords if keyword)
    DB_PATH = Path(os.getenv("PULSE_DB_PATH", BASE_DIR / "pulse_desk.db"))
    BACKUP_DIR = DB_PATH.parent / "backups"

SCHEMA_VERSION = 10


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@asynccontextmanager
async def _connect():
    async with aiosqlite.connect(DB_PATH) as db:
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


async def add_ping_tag(ping_id: int, tag: str) -> list[str]:
    tag = tag.strip()
    tag = tag.replace('"', '')
    if not tag:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        if tag not in tags:
            tags.append(tag)
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def remove_ping_tag(ping_id: int, tag: str) -> list[str]:
    tag = tag.strip().replace('"', '')
    if not tag:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        tags = [t for t in tags if t != tag]
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def get_all_tags() -> list[str]:
    async with _connect() as db:
        rows = await (await db.execute("SELECT DISTINCT tags FROM pings WHERE tags IS NOT NULL AND tags != '[]'")).fetchall()
    seen: set[str] = set()
    for (raw,) in rows:
        for t in _json_loads(raw, []):
            if t:
                seen.add(t)
    return sorted(seen)


def backup_db_if_present(retention: Optional[int] = None) -> None:
    if not DB_PATH.exists():
        return
    backup_dir = BACKUP_DIR if DB_PATH.parent == BASE_DIR else DB_PATH.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{DB_PATH.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    if not backup_path.exists():
        shutil.copy2(DB_PATH, backup_path)
    retention = _env_int("BACKUP_RETENTION", 10) if retention is None else retention
    if retention > 0:
        backups = sorted(backup_dir.glob(f"{DB_PATH.stem}_*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
        for old_backup in backups[retention:]:
            old_backup.unlink(missing_ok=True)


async def init_db() -> None:
    backup_db_if_present()
    async with _connect() as db:
        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                chat TEXT,
                chat_id INTEGER,
                sender TEXT,
                sender_id INTEGER,
                message_id INTEGER,
                mentions TEXT,
                link TEXT,
                text TEXT,
                chat_type TEXT,
                status TEXT DEFAULT 'new',
                is_favorite BOOLEAN DEFAULT 0,
                is_win BOOLEAN DEFAULT 0,
                detected_at TEXT,
                auto_joined BOOLEAN DEFAULT 0,
                is_giveaway BOOLEAN DEFAULT 0,
                giveaway_status TEXT DEFAULT '',
                priority_score INTEGER DEFAULT 0,
                priority_label TEXT DEFAULT 'normal',
                note TEXT DEFAULT '',
                UNIQUE(chat_id, message_id)
            )
            """
        )

        pings_columns = await _columns(db, "pings")
        migrations = {
            "chat_type": "ALTER TABLE pings ADD COLUMN chat_type TEXT",
            "status": "ALTER TABLE pings ADD COLUMN status TEXT DEFAULT 'new'",
            "is_favorite": "ALTER TABLE pings ADD COLUMN is_favorite BOOLEAN DEFAULT 0",
            "is_win": "ALTER TABLE pings ADD COLUMN is_win BOOLEAN DEFAULT 0",
            "detected_at": "ALTER TABLE pings ADD COLUMN detected_at TEXT",
            "auto_joined": "ALTER TABLE pings ADD COLUMN auto_joined BOOLEAN DEFAULT 0",
            "is_giveaway": "ALTER TABLE pings ADD COLUMN is_giveaway BOOLEAN DEFAULT 0",
            "giveaway_status": "ALTER TABLE pings ADD COLUMN giveaway_status TEXT DEFAULT ''",
            "priority_score": "ALTER TABLE pings ADD COLUMN priority_score INTEGER DEFAULT 0",
            "priority_label": "ALTER TABLE pings ADD COLUMN priority_label TEXT DEFAULT 'normal'",
            "note": "ALTER TABLE pings ADD COLUMN note TEXT DEFAULT ''",
            "deadline_at": "ALTER TABLE pings ADD COLUMN deadline_at TEXT",
            "deadline_source": "ALTER TABLE pings ADD COLUMN deadline_source TEXT DEFAULT ''",
            "deadline_text": "ALTER TABLE pings ADD COLUMN deadline_text TEXT DEFAULT ''",
            "reminder_at": "ALTER TABLE pings ADD COLUMN reminder_at TEXT",
            "reminder_sent_at": "ALTER TABLE pings ADD COLUMN reminder_sent_at TEXT",
            "action_status": "ALTER TABLE pings ADD COLUMN action_status TEXT DEFAULT 'new'",
            "tags": "ALTER TABLE pings ADD COLUMN tags TEXT DEFAULT '[]'",
        }
        for column, sql in migrations.items():
            if column not in pings_columns:
                await db.execute(sql)
        await db.execute("UPDATE pings SET detected_at = COALESCE(detected_at, date, ?) WHERE detected_at IS NULL", (_now_iso(),))
        await db.execute("UPDATE pings SET status = 'new' WHERE status IS NULL OR status = ''")
        await db.execute("UPDATE pings SET priority_label = COALESCE(priority_label, 'normal') WHERE priority_label IS NULL OR priority_label = ''")
        await db.execute("UPDATE pings SET note = COALESCE(note, '') WHERE note IS NULL")
        await db.execute("UPDATE pings SET deadline_source = COALESCE(deadline_source, '') WHERE deadline_source IS NULL")
        await db.execute("UPDATE pings SET deadline_text = COALESCE(deadline_text, '') WHERE deadline_text IS NULL")
        await db.execute("UPDATE pings SET action_status = 'new' WHERE action_status IS NULL OR action_status = ''")
        await db.execute("UPDATE pings SET is_giveaway = COALESCE(is_giveaway, 0)")
        await db.execute("UPDATE pings SET giveaway_status = '' WHERE is_giveaway = 0 AND (giveaway_status IS NULL OR giveaway_status = '')")
        await db.execute("UPDATE pings SET giveaway_status = 'pending' WHERE (is_giveaway = 1 OR is_win = 1) AND (giveaway_status IS NULL OR giveaway_status = '')")
        await db.execute("UPDATE pings SET tags = '[]' WHERE tags IS NULL OR tags = ''")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ping_mentions (
                ping_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                PRIMARY KEY (ping_id, username),
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS pings_fts
            USING fts5(chat, sender, mentions, text)
            """
        )
        db.row_factory = aiosqlite.Row
        existing_ping_ids = {
            row["ping_id"]
            for row in await (await db.execute("SELECT DISTINCT ping_id FROM ping_mentions")).fetchall()
        }
        existing_fts_ids = {
            row["rowid"]
            for row in await (await db.execute("SELECT rowid FROM pings_fts")).fetchall()
        }
        rows = await (await db.execute("SELECT id, chat, sender, mentions, text FROM pings")).fetchall()
        await db.execute("DELETE FROM ping_mentions WHERE ping_id NOT IN (SELECT id FROM pings)")
        await db.execute("DELETE FROM pings_fts WHERE rowid NOT IN (SELECT id FROM pings)")
        for row in rows:
            if row["id"] not in existing_ping_ids or row["id"] not in existing_fts_ids:
                await _sync_ping_indexes(db, int(row["id"]), dict(row))
        db.row_factory = None

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_checkpoints (
                session_name TEXT,
                username TEXT,
                last_message_id INTEGER,
                updated_at TEXT,
                PRIMARY KEY (session_name, username)
            )
            """
        )
        checkpoints_columns = await _columns(db, "scan_checkpoints")
        if "updated_at" not in checkpoints_columns:
            await db.execute("ALTER TABLE scan_checkpoints ADD COLUMN updated_at TEXT")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS market_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at_iso TEXT,
                data TEXT NOT NULL,
                volume_24h REAL
            )
            """
        )
        market_columns = await _columns(db, "market_history")
        if "fetched_at_iso" not in market_columns:
            await db.execute("ALTER TABLE market_history ADD COLUMN fetched_at_iso TEXT")
        if "fetched_at" in market_columns:
            await db.execute("UPDATE market_history SET fetched_at_iso = COALESCE(fetched_at_iso, fetched_at)")
        await db.execute("UPDATE market_history SET fetched_at_iso = COALESCE(fetched_at_iso, ?) WHERE fetched_at_iso IS NULL", (_now_iso(),))

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                total_accounts INTEGER DEFAULT 0,
                processed_accounts INTEGER DEFAULT 0,
                total_usernames INTEGER DEFAULT 0,
                processed_usernames INTEGER DEFAULT 0,
                found INTEGER DEFAULT 0,
                last_error TEXT,
                cancel_requested BOOLEAN DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                context TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_settings_history_key ON settings_history(key)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_profiles (
                chat_id INTEGER PRIMARY KEY,
                chat TEXT,
                username TEXT,
                description TEXT,
                deadline_at TEXT,
                deadline_text TEXT,
                fetched_at TEXT,
                last_error TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS source_scores (
                chat_id INTEGER PRIMARY KEY,
                chat TEXT,
                chat_type TEXT,
                total_pings INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                giveaways INTEGER DEFAULT 0,
                important INTEGER DEFAULT 0,
                resolved INTEGER DEFAULT 0,
                noise INTEGER DEFAULT 0,
                avg_priority REAL DEFAULT 0,
                score REAL DEFAULT 0,
                last_ping_at TEXT,
                updated_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ping_id INTEGER NOT NULL,
                remind_at TEXT NOT NULL,
                sent_at TEXT,
                kind TEXT DEFAULT 'deadline',
                message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS viewer_shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                token_hint TEXT,
                created_at TEXT NOT NULL,
                last_seen_at TEXT,
                active BOOLEAN DEFAULT 1
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_notifications_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                delivered_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_candidates (
                ping_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending_review',
                score INTEGER DEFAULT 0,
                reasons TEXT DEFAULT '[]',
                required_channels TEXT DEFAULT '[]',
                join_buttons TEXT DEFAULT '[]',
                external_requirements TEXT DEFAULT '[]',
                blocked_reason TEXT DEFAULT '',
                estimated_value REAL,
                analyzed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS giveaway_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ping_id INTEGER,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                actor TEXT DEFAULT 'system',
                message TEXT DEFAULT '',
                context TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE SET NULL
            )
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_chat_type ON pings(chat_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_detected_at ON pings(detected_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_status ON pings(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_giveaway ON pings(is_giveaway)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_giveaway_status ON pings(giveaway_status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_win ON pings(is_win)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_favorite ON pings(is_favorite)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_priority_score ON pings(priority_score)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_priority_label ON pings(priority_label)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_sender ON pings(sender)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_mentions ON pings(mentions)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_status_detected ON pings(status, detected_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_deadline_at ON pings(deadline_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_action_status ON pings(action_status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ping_mentions_username ON ping_mentions(username)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_market_fetched_at_iso ON market_history(fetched_at_iso)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_level_created ON app_events(level, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_remind_at ON reminders(remind_at, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_outbox_created ON app_notifications_outbox(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_candidates_status ON giveaway_candidates(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_candidates_score ON giveaway_candidates(score)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_giveaway_actions_ping ON giveaway_actions(ping_id)")
        await db.commit()


async def save_ping(record: dict[str, Any]) -> Optional[int]:
    async with _connect() as db:
        mentions_json = json.dumps(record.get("mentions", []), ensure_ascii=False)
        detected_at = record.get("detected_at") or _now_iso()
        try:
            cursor = await db.execute(
                """
                INSERT INTO pings (
                    date, chat, chat_id, sender, sender_id, message_id, mentions,
                    link, text, chat_type, detected_at, is_win, auto_joined, is_giveaway,
                    giveaway_status, priority_score, priority_label, note,
                    deadline_at, deadline_source, deadline_text, reminder_at, reminder_sent_at, action_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("date"),
                    record.get("chat"),
                    record.get("chat_id"),
                    record.get("sender"),
                    record.get("sender_id"),
                    record.get("message_id"),
                    mentions_json,
                    record.get("link"),
                    record.get("text"),
                    record.get("chat_type"),
                    detected_at,
                    1 if record.get("is_win") else 0,
                    1 if record.get("auto_joined") else 0,
                    1 if record.get("is_giveaway") else 0,
                    record.get("giveaway_status") or ("pending" if (record.get("is_giveaway") or record.get("is_win")) else ""),
                    int(record.get("priority_score") or 0),
                    record.get("priority_label") or "normal",
                    record.get("note") or "",
                    record.get("deadline_at"),
                    record.get("deadline_source") or "",
                    record.get("deadline_text") or "",
                    record.get("reminder_at"),
                    record.get("reminder_sent_at"),
                    record.get("action_status") or "new",
                ),
            )
            ping_id = int(cursor.lastrowid)
            await _sync_ping_indexes(db, ping_id, record)
            await db.commit()
            return ping_id
        except aiosqlite.IntegrityError:
            async with db.execute(
                "SELECT id FROM pings WHERE chat_id = ? AND message_id = ?",
                (record.get("chat_id"), record.get("message_id")),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                ping_id = int(row[0])
                await db.execute(
                    """
                    UPDATE pings
                    SET date = COALESCE(?, date),
                        chat = COALESCE(NULLIF(?, ''), chat),
                        sender = COALESCE(NULLIF(?, ''), sender),
                        sender_id = COALESCE(?, sender_id),
                        mentions = ?,
                        link = COALESCE(NULLIF(?, ''), link),
                        text = COALESCE(?, text),
                        chat_type = COALESCE(NULLIF(?, ''), chat_type),
                        is_win = ?, auto_joined = ?, is_giveaway = ?,
                        giveaway_status = CASE
                            WHEN ? = 1 AND (giveaway_status IS NULL OR giveaway_status = '') THEN 'pending'
                            WHEN ? = 0 THEN ''
                            ELSE giveaway_status
                        END,
                        priority_score = ?, priority_label = COALESCE(NULLIF(?, ''), priority_label),
                        deadline_at = CASE
                            WHEN COALESCE(deadline_source, '') = 'manual' THEN deadline_at
                            WHEN ? IS NOT NULL THEN ?
                            ELSE deadline_at
                        END,
                        deadline_source = CASE
                            WHEN COALESCE(deadline_source, '') = 'manual' THEN deadline_source
                            WHEN ? IS NOT NULL THEN COALESCE(NULLIF(?, ''), deadline_source)
                            ELSE deadline_source
                        END,
                        deadline_text = CASE
                            WHEN COALESCE(deadline_source, '') = 'manual' THEN deadline_text
                            WHEN ? IS NOT NULL THEN COALESCE(NULLIF(?, ''), deadline_text)
                            ELSE deadline_text
                        END,
                        reminder_at = COALESCE(?, reminder_at),
                        action_status = CASE
                            WHEN action_status IS NULL OR action_status = '' OR action_status = 'new'
                            THEN COALESCE(NULLIF(?, ''), action_status)
                            ELSE action_status
                        END
                    WHERE id = ?
                    """,
                    (
                        record.get("date"),
                        record.get("chat"),
                        record.get("sender"),
                        record.get("sender_id"),
                        mentions_json,
                        record.get("link"),
                        record.get("text"),
                        record.get("chat_type"),
                        1 if record.get("is_win") else 0,
                        1 if record.get("auto_joined") else 0,
                        1 if (record.get("is_giveaway") or record.get("is_win")) else 0,
                        1 if (record.get("is_giveaway") or record.get("is_win")) else 0,
                        1 if record.get("is_giveaway") else 0,
                        int(record.get("priority_score") or 0),
                        record.get("priority_label") or "",
                        record.get("deadline_at"),
                        record.get("deadline_at"),
                        record.get("deadline_at"),
                        record.get("deadline_source") or "",
                        record.get("deadline_at"),
                        record.get("deadline_text") or "",
                        record.get("reminder_at"),
                        record.get("action_status") or "",
                        ping_id,
                    ),
                )
                await _sync_ping_indexes(db, ping_id, record)
                await db.commit()
                return ping_id


async def toggle_favorite(ping_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE pings SET is_favorite = CASE WHEN is_favorite THEN 0 ELSE 1 END WHERE id = ?", (ping_id,))
        await db.commit()


async def mark_ping_read(ping_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE pings SET status = 'read' WHERE id = ?", (ping_id,))
        await db.commit()


def _build_pings_filters(
    *,
    chat_type: Optional[str] = None,
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    source_score_min: Optional[float] = None,
    tag: Optional[str] = None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if chat_type and chat_type != "all":
        if chat_type == "giveaway":
            _add_where(where, params, "is_giveaway = 1")
        elif chat_type == "win":
            _add_where(where, params, "is_win = 1")
        elif chat_type == "auto_joined":
            _add_where(where, params, "auto_joined = 1")
        elif chat_type == "important":
            _add_where(where, params, "priority_score >= 60")
        else:
            _add_where(where, params, "chat_type = ?", chat_type)
    if status:
        _add_where(where, params, "status = ?", status)
    if favorite is not None:
        _add_where(where, params, "is_favorite = ?", 1 if favorite else 0)
    if mention:
        needle = mention.strip().lstrip("@")
        _add_where(
            where,
            params,
            "EXISTS (SELECT 1 FROM ping_mentions pm WHERE pm.ping_id = pings.id AND lower(pm.username) = lower(?))",
            needle,
        )
    if search:
        raw_search = search.strip()
        text = f"%{raw_search}%"
        tokens = _search_tokens(raw_search)
        fts = _fts_query(search)
        token_clauses: list[str] = []
        token_params: list[Any] = []
        for token in tokens:
            like = f"%{token}%"
            token_clauses.append(
                "(text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE)"
            )
            token_params.extend([like, like, like, like])
        token_sql = " AND ".join(token_clauses)
        if fts:
            condition = "(pings.id IN (SELECT rowid FROM pings_fts WHERE pings_fts MATCH ?) OR text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE"
            values: list[Any] = [fts, text, text, text, text]
            if token_sql:
                condition += f" OR ({token_sql})"
                values.extend(token_params)
            condition += ")"
            _add_where(
                where,
                params,
                condition,
                *values,
            )
        else:
            condition = "(text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE"
            values = [text, text, text, text]
            if token_sql:
                condition += f" OR ({token_sql})"
                values.extend(token_params)
            condition += ")"
            _add_where(where, params, condition, *values)
    if date_from:
        _add_where(where, params, "detected_at >= ?", date_from)
    if date_to:
        _add_where(where, params, "detected_at <= ?", date_to)
    if priority_min is not None:
        _add_where(where, params, "priority_score >= ?", priority_min)
    if action_status:
        _add_where(where, params, "action_status = ?", action_status)
    if deadline_from:
        _add_where(where, params, "deadline_at >= ?", deadline_from)
    if deadline_to:
        _add_where(where, params, "deadline_at <= ?", deadline_to)
    if has_deadline is not None:
        _add_where(where, params, "deadline_at IS NOT NULL" if has_deadline else "deadline_at IS NULL")
    if source_score_min is not None:
        _add_where(
            where,
            params,
            "chat_id IN (SELECT chat_id FROM source_scores WHERE score >= ?)",
            source_score_min,
        )
    if tag:
        _add_where(where, params, "tags LIKE ?", f'%"{tag}"%')
    return where, params


async def mark_pings_read(
    *,
    chat_type: Optional[str] = None,
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    source_score_min: Optional[float] = None,
    only_new: bool = True,
) -> int:
    where, params = _build_pings_filters(
        chat_type=chat_type,
        status=status,
        favorite=favorite,
        mention=mention,
        search=search,
        date_from=date_from,
        date_to=date_to,
        priority_min=priority_min,
        action_status=action_status,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_deadline=has_deadline,
        source_score_min=source_score_min,
    )
    if only_new:
        _add_where(where, params, "status = 'new'")
    query = "UPDATE pings SET status = 'read'"
    if where:
        query += " WHERE " + " AND ".join(where)
    async with _connect() as db:
        cursor = await db.execute(query, params)
        await db.commit()
        return int(cursor.rowcount or 0)


async def update_ping_meta(
    ping_id: int,
    status: Optional[str] = None,
    note: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    giveaway_status: Optional[str] = None,
    deadline_at: Optional[str] = None,
    deadline_source: Optional[str] = None,
    deadline_text: Optional[str] = None,
    reminder_at: Optional[str] = None,
    action_status: Optional[str] = None,
) -> None:
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if note is not None:
        updates["note"] = note
    if is_favorite is not None:
        updates["is_favorite"] = 1 if is_favorite else 0
    if giveaway_status is not None:
        updates["giveaway_status"] = giveaway_status
    if deadline_at is not None:
        updates["deadline_at"] = deadline_at or None
        updates["deadline_source"] = (deadline_source or "manual") if deadline_at else ""
        updates["deadline_text"] = deadline_text or ("Ручной дедлайн" if deadline_at else "")
    if deadline_text is not None:
        updates["deadline_text"] = deadline_text
    if reminder_at is not None:
        updates["reminder_at"] = reminder_at or None
        if not reminder_at:
            updates["reminder_sent_at"] = None
    if action_status is not None:
        updates["action_status"] = action_status
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    async with _connect() as db:
        await db.execute(f"UPDATE pings SET {set_clause} WHERE id = ?", (*updates.values(), ping_id))
        await db.commit()


async def update_ping_deadline(
    ping_id: int,
    deadline_at: Optional[str],
    deadline_source: str,
    deadline_text: str = "",
    action_status: Optional[str] = None,
) -> None:
    async with _connect() as db:
        await db.execute(
            """
            UPDATE pings
            SET deadline_at = ?,
                deadline_source = ?,
                deadline_text = ?,
                action_status = CASE
                    WHEN ? IS NOT NULL AND (action_status IS NULL OR action_status = '' OR action_status = 'new')
                    THEN ?
                    ELSE action_status
                END
            WHERE id = ?
            """,
            (deadline_at, deadline_source, deadline_text, action_status, action_status, ping_id),
        )
        await db.commit()


async def get_checkpoint(session_name: str, username: str) -> int:
    async with _connect() as db:
        async with db.execute(
            "SELECT last_message_id FROM scan_checkpoints WHERE session_name = ? AND username = ?",
            (session_name, username),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


async def get_checkpoints(session_name: str, usernames: list[str]) -> dict[str, int]:
    keys = [str(username) for username in usernames if str(username)]
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    async with _connect() as db:
        rows = await (
            await db.execute(
                f"""
                SELECT username, last_message_id
                FROM scan_checkpoints
                WHERE session_name = ? AND username IN ({placeholders})
                """,
                (session_name, *keys),
            )
        ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}


async def get_latest_checkpoints(usernames: list[str]) -> dict[str, int]:
    keys = [str(username) for username in usernames if str(username)]
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    async with _connect() as db:
        rows = await (
            await db.execute(
                f"""
                SELECT username, MAX(last_message_id)
                FROM scan_checkpoints
                WHERE username IN ({placeholders})
                GROUP BY username
                """,
                keys,
            )
        ).fetchall()
        return {str(row[0]): int(row[1] or 0) for row in rows}


async def save_checkpoint(session_name: str, username: str, last_message_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO scan_checkpoints (session_name, username, last_message_id, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_name, username, last_message_id, _now_iso()),
        )
        await db.commit()


async def save_checkpoints(session_name: str, checkpoints: dict[str, int]) -> None:
    rows = [
        (session_name, str(username), int(last_message_id), _now_iso())
        for username, last_message_id in checkpoints.items()
        if str(username) and int(last_message_id or 0) > 0
    ]
    if not rows:
        return
    async with _connect() as db:
        await db.executemany(
            """
            INSERT OR REPLACE INTO scan_checkpoints (session_name, username, last_message_id, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        await db.commit()


async def delete_ping(chat_id: int, message_id: int) -> None:
    async with _connect() as db:
        rows = await (await db.execute("SELECT id FROM pings WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))).fetchall()
        for row in rows:
            await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (row[0],))
        await db.execute("DELETE FROM pings WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
        await db.commit()


async def delete_ping_by_message_id(message_id: int) -> None:
    async with _connect() as db:
        rows = await (await db.execute("SELECT id FROM pings WHERE message_id = ?", (message_id,))).fetchall()
        for row in rows:
            await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (row[0],))
        await db.execute("DELETE FROM pings WHERE message_id = ?", (message_id,))
        await db.commit()


def _add_where(where: list[str], params: list[Any], condition: str, *values: Any) -> None:
    where.append(condition)
    params.extend(values)


async def get_pings(
    limit: int = 100,
    chat_type: Optional[str] = None,
    sort_order: str = "DESC",
    offset: int = 0,
    sort_by: str = "detected_at",
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    source_score_min: Optional[float] = None,
    tag: Optional[str] = None,
) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM pings"
        where, params = _build_pings_filters(
            chat_type=chat_type,
            status=status,
            favorite=favorite,
            mention=mention,
            search=search,
            date_from=date_from,
            date_to=date_to,
            priority_min=priority_min,
            action_status=action_status,
            deadline_from=deadline_from,
            deadline_to=deadline_to,
            has_deadline=has_deadline,
            source_score_min=source_score_min,
            tag=tag,
        )

        if where:
            query += " WHERE " + " AND ".join(where)

        direction = "DESC" if sort_order.upper() == "DESC" else "ASC"
        valid_sort_fields = {"detected_at", "date", "chat", "sender", "status", "id", "priority_score", "deadline_at", "action_status"}
        field = sort_by if sort_by in valid_sort_fields else "detected_at"
        query += f" ORDER BY {field} {direction}"
        if limit and limit > 0:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_pings_grouped(
    limit: int = 50,
    chat_type: str = "all",
    search: Optional[str] = None,
    mention: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows = await get_pings(limit=0, chat_type=chat_type, search=search, mention=mention, sort_by="priority_score")
    grouped: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row.get("chat_id") or row.get("chat")
        if key not in grouped:
            grouped[key] = dict(row)
            grouped[key]["group_count"] = 0
        grouped[key]["group_count"] += 1
    values = list(grouped.values())
    return values[:limit] if limit and limit > 0 else values


async def rebuild_search_indexes() -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT id, chat, sender, mentions, text FROM pings")).fetchall()
        await db.execute("DELETE FROM ping_mentions")
        await db.execute("DELETE FROM pings_fts")
        for row in rows:
            await _sync_ping_indexes(db, int(row["id"]), dict(row))
        ping_count = len(rows)
        fts_count = (await (await db.execute("SELECT COUNT(*) AS count FROM pings_fts")).fetchone())["count"]
        mention_count = (await (await db.execute("SELECT COUNT(*) AS count FROM ping_mentions")).fetchone())["count"]
        await db.commit()
        return {"pings": ping_count, "fts": int(fts_count or 0), "mentions": int(mention_count or 0)}


async def save_market_snapshot(snapshot: dict[str, Any]) -> None:
    fetched_at = snapshot.get("fetched_at_iso") or _now_iso()
    snapshot["fetched_at_iso"] = fetched_at
    async with _connect() as db:
        await db.execute(
            "INSERT INTO market_history (fetched_at_iso, data) VALUES (?, ?)",
            (fetched_at, json.dumps(snapshot, ensure_ascii=False)),
        )
        await db.commit()


async def get_market_history(limit: int = 50) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT fetched_at_iso, data FROM market_history ORDER BY fetched_at_iso DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = json.loads(row["data"])
                item["fetched_at_iso"] = item.get("fetched_at_iso") or row["fetched_at_iso"]
                result.append(item)
            return result


async def upsert_channel_profile(
    chat_id: int,
    chat: str,
    username: str = "",
    description: str = "",
    deadline_at: Optional[str] = None,
    deadline_text: str = "",
    last_error: str = "",
) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO channel_profiles (
                chat_id, chat, username, description, deadline_at,
                deadline_text, fetched_at, last_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat = excluded.chat,
                username = excluded.username,
                description = excluded.description,
                deadline_at = excluded.deadline_at,
                deadline_text = excluded.deadline_text,
                fetched_at = excluded.fetched_at,
                last_error = excluded.last_error
            """,
            (chat_id, chat, username, description, deadline_at, deadline_text, _now_iso(), last_error),
        )
        await db.commit()


async def get_channel_profile(chat_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM channel_profiles WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def update_channel_deadlines(chat_id: int, deadline_at: Optional[str], deadline_text: str = "") -> int:
    if not deadline_at:
        return 0
    async with _connect() as db:
        cursor = await db.execute(
            """
            UPDATE pings
            SET deadline_at = ?,
                deadline_source = 'channel_description',
                deadline_text = ?
            WHERE chat_id = ?
              AND is_giveaway = 1
              AND COALESCE(deadline_source, '') != 'manual'
              AND (deadline_at IS NULL OR deadline_at = '')
              AND COALESCE(action_status, 'new') NOT IN ('claimed', 'scam', 'missed', 'closed')
            """,
            (deadline_at, deadline_text, chat_id),
        )
        await db.commit()
        return int(cursor.rowcount or 0)


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def replace_ping_reminders(ping_id: int, deadline_at: Optional[str], reminder_at: Optional[str] = None) -> None:
    deadline = _parse_iso_datetime(deadline_at)
    now = datetime.now()
    reminders: list[tuple[str, str]] = []
    if reminder_at:
        reminders.append((reminder_at, "manual"))
    elif deadline and deadline > now:
        for hours in (24, 2):
            remind_at = deadline - timedelta(hours=hours)
            if remind_at > now:
                reminders.append((remind_at.replace(microsecond=0).isoformat(), f"deadline-{hours}h"))
    async with _connect() as db:
        await db.execute("DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL", (ping_id,))
        for remind_at, kind in reminders:
            await db.execute(
                """
                INSERT INTO reminders (ping_id, remind_at, sent_at, kind, message, created_at)
                VALUES (?, ?, NULL, ?, '', ?)
                """,
                (ping_id, remind_at, kind, _now_iso()),
            )
        await db.execute(
            "UPDATE pings SET reminder_at = ?, reminder_sent_at = NULL WHERE id = ?",
            ((reminders[0][0] if reminders else reminder_at), ping_id),
        )
        await db.commit()


async def backfill_deadlines_from_text(limit: int = 5000) -> int:
    if parse_deadline is None:
        return 0
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, date, detected_at, chat_type, text, deadline_at, deadline_source
            FROM pings
            WHERE is_giveaway = 1
              AND COALESCE(deadline_source, '') != 'manual'
              AND COALESCE(deadline_source, '') != 'channel_description'
              AND COALESCE(text, '') != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    changed = 0
    for row in rows:
        reference = _parse_iso_datetime(row["date"]) or _parse_iso_datetime(row["detected_at"]) or datetime.now()
        if reference.tzinfo is not None:
            reference = reference.replace(tzinfo=None)
        is_outcome = is_giveaway_outcome_text(row["text"] or "")
        match = parse_claim_deadline(row["text"], now=reference) if is_outcome and parse_claim_deadline else parse_participation_deadline(row["text"], now=reference)
        if not match:
            if is_outcome and row["deadline_source"] == "claim_window_text":
                await update_ping_deadline(int(row["id"]), None, "", "", "claim_prize")
                await replace_ping_reminders(int(row["id"]), None)
                changed += 1
            continue
        deadline_at = iso_or_none(match.deadline_at)
        source = "claim_window_text" if is_outcome else ("channel_post_text" if row["chat_type"] == "channel" else "message_text")
        if row["deadline_at"] == deadline_at and row["deadline_source"] == source:
            continue
        next_action = "claim_prize" if source == "claim_window_text" else "waiting_result"
        await update_ping_deadline(int(row["id"]), deadline_at, source, match.matched_text, next_action)
        await replace_ping_reminders(int(row["id"]), deadline_at)
        changed += 1
    return changed


def _matches_strict_giveaway_rule(text: str, chat_type: str, keywords: Sequence[str]) -> bool:
    return matches_strict_giveaway_rule(text, chat_type, keywords)


async def reconcile_giveaway_outcomes(limit: int = 10000) -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, is_win, action_status, giveaway_status, priority_score, deadline_source
            FROM pings
            WHERE is_giveaway = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    marked = 0
    for row in rows:
        if not is_giveaway_outcome_text(row["text"] or ""):
            continue
        resolution = giveaway_outcome_resolution(row["text"] or "")
        is_final = (row["giveaway_status"] or "") in {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"} or (row["action_status"] or "") in {"claimed", "missed", "scam", "closed"}
        next_action = row["action_status"] or "new"
        if not is_final and next_action in {"", "new", "waiting_result", "to_check"}:
            next_action = "missed" if resolution == "missed" else "claim_prize"
        next_giveaway_status = resolution if not is_final and resolution == "missed" else "pending"
        async with _connect() as db:
            await db.execute(
                """
                UPDATE pings
                SET is_win = 1,
                    giveaway_status = CASE
                        WHEN COALESCE(giveaway_status, '') = '' THEN ?
                        WHEN COALESCE(giveaway_status, '') = 'pending' AND ? = 'missed' THEN 'missed'
                        ELSE giveaway_status
                    END,
                    action_status = ?,
                    priority_score = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 90 ELSE priority_score END,
                    priority_label = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 'critical' ELSE priority_label END,
                    deadline_at = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN NULL
                        ELSE deadline_at
                    END,
                    deadline_source = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN ''
                        ELSE deadline_source
                    END,
                    deadline_text = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN ''
                        ELSE deadline_text
                    END
                WHERE id = ?
                """,
                (next_giveaway_status, next_giveaway_status, next_action, int(row["id"])),
            )
            if (row["deadline_source"] or "") in {"", "channel_description_missing"}:
                await db.execute("DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL", (int(row["id"]),))
            await db.commit()
        marked += 1
    return {"marked": marked}


async def reconcile_win_flags(win_keywords: Sequence[str], limit: int = 10000) -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, chat_type, is_win, is_giveaway, action_status, giveaway_status, priority_score
            FROM pings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    enabled = 0
    disabled = 0
    final_statuses = {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"}
    for row in rows:
        should_be_win = is_win_text(row["text"] or "", win_keywords)
        current = bool(row["is_win"])
        if should_be_win == current:
            continue
        current_action = row["action_status"] or "new"
        current_giveaway_status = row["giveaway_status"] or ""
        is_final = current_action in {"claimed", "missed", "scam", "closed"} or current_giveaway_status in final_statuses
        async with _connect() as db:
            if should_be_win:
                next_action = "claim_prize" if not is_final and current_action in {"", "new", "to_check", "waiting_result"} else current_action
                await db.execute(
                    """
                    UPDATE pings
                    SET is_win = 1,
                        action_status = ?,
                        priority_score = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 90 ELSE priority_score END,
                        priority_label = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 'critical' ELSE priority_label END
                    WHERE id = ?
                    """,
                    (next_action, int(row["id"])),
                )
                enabled += 1
            else:
                if is_final:
                    next_action = current_action
                elif row["is_giveaway"]:
                    next_action = "waiting_result" if current_action in {"claim_prize", "to_check", "new", ""} else current_action
                else:
                    next_action = "to_check" if int(row["priority_score"] or 0) >= 60 else "new"
                await db.execute(
                    """
                    UPDATE pings
                    SET is_win = 0,
                        action_status = ?,
                        priority_score = CASE
                            WHEN is_giveaway = 1 THEN MIN(COALESCE(priority_score, 0), 65)
                            ELSE MIN(COALESCE(priority_score, 0), 55)
                        END,
                        priority_label = CASE
                            WHEN is_giveaway = 1 THEN 'high'
                            ELSE 'medium'
                        END
                    WHERE id = ?
                    """,
                    (next_action, int(row["id"])),
                )
                disabled += 1
            await db.commit()
    return {"enabled": enabled, "disabled": disabled}


async def reconcile_giveaway_flags(keywords: Sequence[str], limit: int = 10000) -> dict[str, int]:
    """Align stored rows with the channel+keyword giveaway rule used for new scans."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, chat_type, is_giveaway, is_win, priority_score, action_status, deadline_source
            FROM pings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    enabled = 0
    disabled = 0
    for row in rows:
        should_be_giveaway = _matches_strict_giveaway_rule(row["text"] or "", row["chat_type"] or "", keywords)
        is_giveaway = bool(row["is_giveaway"])
        if should_be_giveaway == is_giveaway:
            continue
        ping_id = int(row["id"])
        current_action = row["action_status"] or "new"
        if should_be_giveaway:
            next_action = "waiting_result" if current_action in {"", "new"} else current_action
            async with _connect() as db:
                await db.execute(
                    """
                    UPDATE pings
                    SET is_giveaway = 1,
                        giveaway_status = CASE
                            WHEN COALESCE(giveaway_status, '') = '' THEN 'pending'
                            ELSE giveaway_status
                        END,
                        action_status = ?
                    WHERE id = ?
                    """,
                    (next_action, ping_id),
                )
                await db.commit()
            enabled += 1
            continue

        if row["is_win"]:
            next_action = "claim_prize" if current_action in {"", "new", "waiting_result"} else current_action
        elif current_action in {"", "new", "waiting_result"}:
            next_action = "to_check" if int(row["priority_score"] or 0) >= 60 else "new"
        else:
            next_action = current_action
        keep_manual_deadline = (row["deadline_source"] or "") == "manual"
        async with _connect() as db:
            if keep_manual_deadline:
                await db.execute(
                    """
                    UPDATE pings
                    SET is_giveaway = 0,
                        giveaway_status = '',
                        action_status = ?
                    WHERE id = ?
                    """,
                    (next_action, ping_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE pings
                    SET is_giveaway = 0,
                        giveaway_status = '',
                        action_status = ?,
                        deadline_at = NULL,
                        deadline_source = '',
                        deadline_text = '',
                        reminder_at = NULL,
                        reminder_sent_at = NULL
                    WHERE id = ?
                    """,
                    (next_action, ping_id),
                )
                await db.execute("DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL", (ping_id,))
            await db.commit()
        disabled += 1
    return {"enabled": enabled, "disabled": disabled}


async def get_due_reminders(now_iso: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT r.*, p.chat, p.text, p.link, p.deadline_at, p.deadline_source, p.action_status, p.giveaway_status
            FROM reminders r
            JOIN pings p ON p.id = r.ping_id
            WHERE r.sent_at IS NULL
              AND r.remind_at <= ?
              AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'scam', 'missed', 'closed')
              AND COALESCE(p.giveaway_status, '') NOT IN ('claimed', 'scam', 'missed_unsubscribe')
            ORDER BY r.remind_at ASC
            LIMIT ?
            """,
            (now_iso, limit),
        )).fetchall()
        return [dict(row) for row in rows]


async def mark_reminder_sent(reminder_id: int) -> None:
    sent_at = _now_iso()
    async with _connect() as db:
        row = await (await db.execute("SELECT ping_id FROM reminders WHERE id = ?", (reminder_id,))).fetchone()
        await db.execute("UPDATE reminders SET sent_at = ? WHERE id = ?", (sent_at, reminder_id))
        if row:
            await db.execute("UPDATE pings SET reminder_sent_at = ? WHERE id = ?", (sent_at, int(row[0])))
        await db.commit()


async def enqueue_outbox_event(event_type: str, payload: dict[str, Any]) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            "INSERT INTO app_notifications_outbox (created_at, event_type, payload) VALUES (?, ?, ?)",
            (_now_iso(), event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_outbox_stats() -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        total = await (await db.execute("SELECT COUNT(*) AS value FROM app_notifications_outbox")).fetchone()
        pending = await (await db.execute(
            "SELECT COUNT(*) AS value FROM app_notifications_outbox WHERE delivered_at IS NULL OR delivered_at = ''"
        )).fetchone()
        latest = await (await db.execute("SELECT MAX(created_at) AS value FROM app_notifications_outbox")).fetchone()
        oldest = await (await db.execute("SELECT MIN(created_at) AS value FROM app_notifications_outbox")).fetchone()
        recent = await (await db.execute(
            "SELECT COUNT(*) AS value FROM app_notifications_outbox WHERE created_at >= ?",
            ((datetime.now() - timedelta(hours=1)).replace(microsecond=0).isoformat(),),
        )).fetchone()
        by_type_rows = await (await db.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM app_notifications_outbox
            GROUP BY event_type
            ORDER BY count DESC
            LIMIT 8
            """
        )).fetchall()
        return {
            "total": int(total["value"] or 0),
            "pending": int(pending["value"] or 0),
            "recent_1h": int(recent["value"] or 0),
            "pressure": "high" if int(total["value"] or 0) > 4500 else "ok",
            "oldest_created_at": oldest["value"],
            "latest_created_at": latest["value"],
            "by_type": [dict(row) for row in by_type_rows],
        }


async def get_outbox_after(last_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT * FROM app_notifications_outbox
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (last_id, limit),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except json.JSONDecodeError:
                item["payload"] = {}
            result.append(item)
        return result


def _candidate_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    item = dict(row)
    for key, default in (
        ("reasons", []),
        ("required_channels", []),
        ("join_buttons", []),
        ("external_requirements", []),
    ):
        item[key] = _json_loads(item.get(key), default)
    if "mentions" in item:
        item["mentions"] = _parse_mentions(item.get("mentions"))
    return item


async def get_ping_by_id(ping_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return None
        item = dict(row)
        item["mentions"] = _parse_mentions(item.get("mentions"))
        return item


async def get_ping_by_message_ref(chat_id: Any, message_id: Any) -> Optional[dict[str, Any]]:
    if chat_id is None or message_id is None:
        return None
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT * FROM pings WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            )
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["mentions"] = _parse_mentions(item.get("mentions"))
        return item


async def upsert_giveaway_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    ping_id = int(candidate["ping_id"])
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO giveaway_candidates (
                ping_id, status, score, reasons, required_channels, join_buttons,
                external_requirements, blocked_reason, estimated_value, analyzed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ping_id) DO UPDATE SET
                status = excluded.status,
                score = excluded.score,
                reasons = excluded.reasons,
                required_channels = excluded.required_channels,
                join_buttons = excluded.join_buttons,
                external_requirements = excluded.external_requirements,
                blocked_reason = excluded.blocked_reason,
                estimated_value = excluded.estimated_value,
                analyzed_at = excluded.analyzed_at,
                updated_at = excluded.updated_at
            """,
            (
                ping_id,
                candidate.get("status") or "pending_review",
                int(candidate.get("score") or 0),
                json.dumps(candidate.get("reasons") or [], ensure_ascii=False),
                json.dumps(candidate.get("required_channels") or [], ensure_ascii=False),
                json.dumps(candidate.get("join_buttons") or [], ensure_ascii=False),
                json.dumps(candidate.get("external_requirements") or [], ensure_ascii=False),
                candidate.get("blocked_reason") or "",
                candidate.get("estimated_value"),
                now,
                now,
            ),
        )
        await db.commit()
    saved = await get_giveaway_candidate(ping_id)
    return saved or candidate


async def update_giveaway_candidate_status(ping_id: int, status: str, blocked_reason: Optional[str] = None) -> None:
    updates = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, _now_iso()]
    if blocked_reason is not None:
        updates.append("blocked_reason = ?")
        params.append(blocked_reason)
    params.append(ping_id)
    async with _connect() as db:
        await db.execute(f"UPDATE giveaway_candidates SET {', '.join(updates)} WHERE ping_id = ?", params)
        await db.commit()


async def get_giveaway_candidate(ping_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """
            SELECT c.*, p.chat, p.chat_id, p.message_id, p.link, p.text, p.deadline_at,
                   p.giveaway_status, p.action_status, p.mentions
            FROM giveaway_candidates c
            JOIN pings p ON p.id = c.ping_id
            WHERE c.ping_id = ?
            """,
            (ping_id,),
        )).fetchone()
        return _candidate_from_row(row) if row else None


async def get_giveaway_candidates(
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if status:
        where = "WHERE c.status = ?"
        params.append(status)
    params.append(limit)
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""
            SELECT c.*, p.chat, p.chat_id, p.message_id, p.link, p.text, p.deadline_at,
                   p.giveaway_status, p.action_status, p.mentions
            FROM giveaway_candidates c
            JOIN pings p ON p.id = c.ping_id
            {where}
            ORDER BY
                CASE c.status WHEN 'recommended' THEN 0 WHEN 'pending_review' THEN 1 WHEN 'manual_required' THEN 2 ELSE 3 END,
                c.score DESC,
                c.updated_at DESC
            LIMIT ?
            """,
            params,
        )).fetchall()
        return [_candidate_from_row(row) for row in rows]


async def seed_giveaway_candidates_from_pings(limit: int = 500) -> int:
    now = _now_iso()
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO giveaway_candidates (
                ping_id, status, score, reasons, required_channels, join_buttons,
                external_requirements, blocked_reason, estimated_value, analyzed_at, updated_at
            )
            SELECT
                p.id,
                'pending_review',
                COALESCE(p.priority_score, 0),
                ?,
                '[]',
                '[]',
                '[]',
                '',
                NULL,
                ?,
                ?
            FROM pings p
            LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
            WHERE p.is_giveaway = 1
              AND c.ping_id IS NULL
            ORDER BY p.detected_at DESC
            LIMIT ?
            """,
            (json.dumps(["Pending safe analysis."], ensure_ascii=False), now, now, limit),
        )
        await db.commit()
        return int(cursor.rowcount or 0)


async def record_giveaway_action(
    ping_id: Optional[int],
    action: str,
    status: str,
    actor: str = "system",
    message: str = "",
    context: Optional[dict[str, Any]] = None,
) -> int:
    context_json = json.dumps(context or {}, ensure_ascii=False, sort_keys=True)
    async with _connect() as db:
        if action == "analyze" and ping_id is not None:
            cutoff = (datetime.now() - timedelta(hours=6)).replace(microsecond=0).isoformat()
            existing = await (await db.execute(
                """
                SELECT id
                FROM giveaway_actions
                WHERE ping_id = ?
                  AND action = ?
                  AND status = ?
                  AND actor = ?
                  AND message = ?
                  AND context = ?
                  AND created_at >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (ping_id, action, status, actor, message, context_json, cutoff),
            )).fetchone()
            if existing:
                await db.execute("UPDATE giveaway_actions SET created_at = ? WHERE id = ?", (_now_iso(), int(existing[0])))
                await db.commit()
                return int(existing[0])
        cursor = await db.execute(
            """
            INSERT INTO giveaway_actions (ping_id, action, status, actor, message, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ping_id,
                action,
                status,
                actor,
                message,
                context_json,
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_giveaway_actions(ping_id: int, limit: int = 50) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT * FROM giveaway_actions
            WHERE ping_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ping_id, limit),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = _json_loads(item.get("context"), {})
            result.append(item)
        return result


async def cleanup_outbox(days: int = 2, max_events: int = 5000) -> None:
    cutoff = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    async with _connect() as db:
        await db.execute("DELETE FROM app_notifications_outbox WHERE created_at < ?", (cutoff,))
        if max_events > 0:
            await db.execute(
                """
                DELETE FROM app_notifications_outbox
                WHERE id NOT IN (
                    SELECT id
                    FROM app_notifications_outbox
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (max_events,),
            )
        await db.commit()


async def recalculate_source_scores() -> None:
    now = _now_iso()
    async with _connect() as db:
        await db.execute("DELETE FROM source_scores")
        await db.execute(
            """
            INSERT INTO source_scores (
                chat_id, chat, chat_type, total_pings, wins, giveaways, important,
                resolved, noise, avg_priority, score, last_ping_at, updated_at
            )
            SELECT
                chat_id,
                COALESCE(MAX(chat), 'unknown') AS chat,
                COALESCE(MAX(chat_type), 'unknown') AS chat_type,
                COUNT(*) AS total_pings,
                COALESCE(SUM(is_win), 0) AS wins,
                COALESCE(SUM(is_giveaway), 0) AS giveaways,
                COALESCE(SUM(CASE WHEN priority_score >= 60 OR status = 'important' THEN 1 ELSE 0 END), 0) AS important,
                COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) AS resolved,
                COALESCE(SUM(CASE WHEN status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam' THEN 1 ELSE 0 END), 0) AS noise,
                COALESCE(AVG(priority_score), 0) AS avg_priority,
                ROUND(
                    COALESCE(AVG(priority_score), 0)
                    + COALESCE(SUM(is_win), 0) * 25
                    + COALESCE(SUM(is_giveaway), 0) * 4
                    + COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) * 2
                    - COALESCE(SUM(CASE WHEN status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam' THEN 1 ELSE 0 END), 0) * 8,
                    2
                ) AS score,
                MAX(detected_at) AS last_ping_at,
                ? AS updated_at
            FROM pings
            WHERE chat_id IS NOT NULL
            GROUP BY chat_id
            """,
            (now,),
        )
        await db.commit()


async def get_source_scores(limit: int = 50) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM source_scores ORDER BY score DESC, total_pings DESC LIMIT ?",
            (limit,),
        )).fetchall()
        return [dict(row) for row in rows]


async def get_source_score(chat_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM source_scores WHERE chat_id = ?", (chat_id,))).fetchone()
        return dict(row) if row else None


async def get_task_overview(limit: int = 300) -> dict[str, list[dict[str, Any]]]:
    rows = await get_pings(limit=limit, chat_type="giveaway", sort_by="deadline_at", sort_order="ASC")
    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    buckets: dict[str, list[dict[str, Any]]] = {
        "overdue": [],
        "today": [],
        "tomorrow": [],
        "waiting_result": [],
        "no_deadline": [],
        "all_open": [],
    }
    closed = {"claimed", "scam", "missed", "missed_unsubscribe", "missed_reply", "closed"}
    for row in rows:
        if row.get("action_status") in closed or row.get("giveaway_status") in closed:
            continue
        buckets["all_open"].append(row)
        if row.get("action_status") == "waiting_result":
            buckets["waiting_result"].append(row)
        deadline = _parse_iso_datetime(row.get("deadline_at"))
        if not deadline:
            if not row.get("is_win") and row.get("action_status") != "claim_prize":
                buckets["no_deadline"].append(row)
            continue
        if deadline < now:
            buckets["overdue"].append(row)
        elif deadline.date() == today:
            buckets["today"].append(row)
        elif deadline.date() == tomorrow:
            buckets["tomorrow"].append(row)
    return buckets


def _giveaway_board_row(row: aiosqlite.Row, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now()
    item = dict(row)
    item["mentions"] = _parse_mentions(item.get("mentions"))
    for key, default in (
        ("candidate_reasons", []),
        ("required_channels", []),
        ("join_buttons", []),
        ("external_requirements", []),
    ):
        item[key] = _json_loads(item.get(key), default)
    giveaway_status = item.get("giveaway_status") or "pending"
    action_status = item.get("action_status") or "new"
    candidate_status = item.get("candidate_status") or ""
    item["is_final"] = giveaway_status in {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"} or action_status in {"claimed", "missed", "scam", "closed"}
    item["is_claim"] = bool(item.get("is_win")) or action_status == "claim_prize"
    deadline = _parse_iso_datetime(item.get("deadline_at"))
    item["deadline_state"] = "missing"
    item["deadline_seconds"] = None
    item["deadline_badge_class"] = "bad"
    if deadline:
        seconds = int((deadline - now).total_seconds())
        item["deadline_seconds"] = seconds
        if seconds < 0:
            item["deadline_state"] = "overdue"
            item["deadline_badge_class"] = "bad"
        elif deadline.date() == now.date():
            item["deadline_state"] = "today"
            item["deadline_badge_class"] = "warn"
        elif deadline.date() == (now + timedelta(days=1)).date():
            item["deadline_state"] = "tomorrow"
            item["deadline_badge_class"] = "info"
        else:
            item["deadline_state"] = "upcoming"
            item["deadline_badge_class"] = "good"
    elif item["is_claim"]:
        item["deadline_state"] = "claim_unknown"
        item["deadline_badge_class"] = "warn"
    item["workflow_stage"] = _giveaway_workflow_stage(item)
    item["needs_decision"] = (
        not item["is_final"]
        and (
            action_status in {"new", "to_check", "claim_prize"}
            or candidate_status in {"recommended", "manual_required"}
            or (not item.get("deadline_at") and not item["is_claim"])
        )
    )
    item["deadline_label"] = item.get("deadline_at") or "deadline_missing"
    item["workflow_hint"] = _giveaway_workflow_hint(item)
    item["sort_rank"] = _giveaway_sort_rank(item)
    return item


def _giveaway_workflow_stage(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "done"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual"
    if item.get("is_claim"):
        return "claim"
    if not item.get("deadline_at"):
        return "missing_deadline"
    if item.get("deadline_state") == "overdue":
        return "overdue"
    if item.get("deadline_state") in {"today", "tomorrow"}:
        return "soon"
    return "waiting"


def _giveaway_sort_rank(item: dict[str, Any]) -> int:
    if item.get("is_final"):
        return 90
    if item.get("blocked_reason") or item.get("external_requirements"):
        return 15
    if item.get("is_claim"):
        return 0 if item.get("deadline_state") == "overdue" else 1
    if item.get("deadline_state") == "overdue":
        return 5
    if item.get("deadline_state") == "today":
        return 10
    if item.get("candidate_status") == "recommended":
        return 12
    if item.get("deadline_state") == "tomorrow":
        return 20
    if item.get("workflow_stage") == "missing_deadline":
        return 40
    return 50


def _giveaway_workflow_hint(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "closed"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual_review"
    if item.get("is_claim"):
        return "claim_prize"
    if not item.get("deadline_at"):
        return "set_deadline"
    if item.get("candidate_status") == "recommended":
        return "recommended"
    return "watch"


async def get_giveaway_board(limit: int = 80) -> dict[str, Any]:
    now = _now_iso()
    now_dt = _parse_iso_datetime(now) or datetime.now()
    base_where = "(p.is_giveaway = 1 OR p.is_win = 1)"
    stats_base_where = "(is_giveaway = 1 OR is_win = 1)"
    active_where = (
        f"{base_where} "
        "AND COALESCE(p.giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') "
        "AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')"
    )
    select_sql = """
        SELECT
            p.*,
            c.status AS candidate_status,
            c.score AS candidate_score,
            c.reasons AS candidate_reasons,
            c.required_channels,
            c.join_buttons,
            c.external_requirements,
            c.blocked_reason,
            c.estimated_value,
            c.analyzed_at,
            c.updated_at AS candidate_updated_at
        FROM pings p
        LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
    """

    async def fetch_bucket(db: aiosqlite.Connection, where_sql: str) -> list[dict[str, Any]]:
        rows = await (await db.execute(
            f"""
            {select_sql}
            WHERE {where_sql}
            ORDER BY
                CASE WHEN p.deadline_at IS NULL OR p.deadline_at = '' THEN 1 ELSE 0 END,
                p.deadline_at ASC,
                p.detected_at DESC
            LIMIT ?
            """,
            (min(limit * 4, 500),),
        )).fetchall()
        items = [_giveaway_board_row(row, now_dt) for row in rows]
        items.sort(key=lambda item: (int(item.get("sort_rank") or 99), item.get("deadline_at") or "9999-12-31", -int(item.get("id") or 0)))
        return items[:limit]

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        buckets = {
            "need_action": await fetch_bucket(
                db,
                active_where
                + " AND (p.is_win = 1"
                + " OR COALESCE(p.action_status, 'new') IN ('claim_prize', 'to_check')"
                + " OR COALESCE(c.status, '') = 'recommended')"
                + " AND COALESCE(c.status, '') != 'manual_required'"
                + " AND COALESCE(c.blocked_reason, '') = ''"
                + " AND COALESCE(c.external_requirements, '[]') IN ('[]', '')",
            ),
            "waiting_result": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND COALESCE(p.action_status, 'new') = 'waiting_result'"
                + " AND p.deadline_at IS NOT NULL AND p.deadline_at <> ''",
            ),
            "no_deadline": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND COALESCE(p.action_status, 'new') != 'claim_prize'"
                + " AND (p.deadline_at IS NULL OR p.deadline_at = '')",
            ),
            "suspicious": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND (COALESCE(c.status, '') = 'manual_required'"
                + " OR COALESCE(c.blocked_reason, '') <> ''"
                + " OR COALESCE(c.external_requirements, '[]') NOT IN ('[]', ''))",
            ),
            "done": await fetch_bucket(
                db,
                base_where
                + " AND (COALESCE(p.giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed')"
                + " OR COALESCE(p.action_status, '') IN ('claimed', 'missed', 'scam', 'closed'))",
            ),
        }
        stats_row = await (await db.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN (COALESCE(action_status, 'new') = 'claim_prize' OR is_win = 1) AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') AND COALESCE(action_status, '') NOT IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS claim_prize,
                SUM(CASE WHEN is_win = 0 AND COALESCE(action_status, 'new') = 'waiting_result' AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') THEN 1 ELSE 0 END) AS waiting_result,
                SUM(CASE WHEN is_win = 0 AND (deadline_at IS NULL OR deadline_at = '') AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') THEN 1 ELSE 0 END) AS no_deadline,
                SUM(CASE WHEN deadline_at IS NOT NULL AND deadline_at <> '' AND deadline_at < ? AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') AND COALESCE(action_status, '') NOT IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS overdue,
                SUM(CASE WHEN COALESCE(giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') OR COALESCE(action_status, '') IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'missed_reply' THEN 1 ELSE 0 END) AS missed_reply
            FROM pings
            WHERE {stats_base_where}
            """,
            (now,),
        )).fetchone()
        candidate_rows = await (await db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM giveaway_candidates
            GROUP BY status
            ORDER BY count DESC
            """
        )).fetchall()
        action_rows = await (await db.execute(
            """
            SELECT action, status, COUNT(*) AS count
            FROM giveaway_actions
            GROUP BY action, status
            ORDER BY count DESC
            LIMIT 8
            """
        )).fetchall()
        return {
            "generated_at": now,
            "stats": {key: int(stats_row[key] or 0) for key in stats_row.keys()},
            "candidate_statuses": [dict(row) for row in candidate_rows],
            "action_statuses": [dict(row) for row in action_rows],
            "outbox": await get_outbox_stats(),
            "buckets": buckets,
            "bucket_counts": {key: len(value) for key, value in buckets.items()},
        }


async def get_debt_board(tracked_usernames: Sequence[str], limit: int = 160) -> dict[str, Any]:
    now = _now_iso()
    now_dt = _parse_iso_datetime(now) or datetime.now()
    safe_limit = max(10, min(int(limit or 160), 500))
    select_sql = """
        SELECT
            p.*,
            c.status AS candidate_status,
            c.score AS candidate_score,
            c.reasons AS candidate_reasons,
            c.required_channels,
            c.join_buttons,
            c.external_requirements,
            c.blocked_reason,
            c.estimated_value,
            c.analyzed_at,
            c.updated_at AS candidate_updated_at
        FROM pings p
        LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
        WHERE (p.is_win = 1 OR COALESCE(p.action_status, '') = 'claim_prize')
          AND COALESCE(p.chat_type, '') = 'channel'
          AND COALESCE(NULLIF(p.giveaway_status, ''), 'pending') = 'pending'
          AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')
        ORDER BY
            CASE WHEN COALESCE(p.status, '') = 'new' THEN 0 ELSE 1 END,
            COALESCE(p.priority_score, 0) DESC,
            p.detected_at DESC,
            p.id DESC
        LIMIT ?
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(select_sql, (safe_limit,))).fetchall()
    debts = [_giveaway_board_row(row, now_dt) for row in rows]
    for item in debts:
        item["debt_status"] = "pending_prize"
        item["giveaway_status"] = item.get("giveaway_status") or "pending"
        item["action_status"] = item.get("action_status") or "claim_prize"

    ordered_usernames: list[str] = []
    seen: set[str] = set()
    for username in tracked_usernames:
        normalized = str(username or "").strip().lstrip("@")
        key = normalized.lower()
        if key and key not in seen:
            seen.add(key)
            ordered_usernames.append(normalized)

    profile_rows: dict[str, list[dict[str, Any]]] = {username.lower(): [] for username in ordered_usernames}
    unassigned: list[dict[str, Any]] = []
    for item in debts:
        mentions = [str(value).strip().lstrip("@") for value in item.get("mentions") or []]
        mention_keys = {value.lower() for value in mentions if value}
        matched = False
        for username in ordered_usernames:
            key = username.lower()
            if key in mention_keys:
                profile_rows[key].append(item)
                matched = True
        if not matched:
            unassigned.append(item)

    profiles = []
    for username in ordered_usernames:
        rows_for_user = profile_rows.get(username.lower(), [])
        profiles.append({
            "key": username.lower(),
            "username": username,
            "count": len(rows_for_user),
            "new_count": sum(1 for row in rows_for_user if row.get("status") == "new"),
            "critical_count": sum(1 for row in rows_for_user if int(row.get("priority_score") or 0) >= 90),
            "max_priority": max((int(row.get("priority_score") or 0) for row in rows_for_user), default=0),
            "last_detected_at": max((row.get("detected_at") or "" for row in rows_for_user), default=""),
            "rows": rows_for_user[:safe_limit],
        })
    if unassigned:
        profiles.append({
            "key": "_unassigned",
            "username": "без username",
            "count": len(unassigned),
            "new_count": sum(1 for row in unassigned if row.get("status") == "new"),
            "critical_count": sum(1 for row in unassigned if int(row.get("priority_score") or 0) >= 90),
            "max_priority": max((int(row.get("priority_score") or 0) for row in unassigned), default=0),
            "last_detected_at": max((row.get("detected_at") or "" for row in unassigned), default=""),
            "rows": unassigned[:safe_limit],
        })

    return {
        "generated_at": now,
        "stats": {
            "total": len(debts),
            "new": sum(1 for row in debts if row.get("status") == "new"),
            "critical": sum(1 for row in debts if int(row.get("priority_score") or 0) >= 90),
            "profiles_with_debt": sum(1 for profile in profiles if int(profile.get("count") or 0) > 0),
            "last_detected_at": max((row.get("detected_at") or "" for row in debts), default=""),
        },
        "profiles": profiles,
        "rows": debts,
    }


def _effective_backup_dir() -> Path:
    return BACKUP_DIR if DB_PATH.parent == BASE_DIR else DB_PATH.parent / "backups"


def create_db_backup() -> Optional[dict[str, Any]]:
    if not DB_PATH.exists():
        return None
    backup_dir = _effective_backup_dir()
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"{DB_PATH.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(DB_PATH, backup_path)
    return {
        "name": backup_path.name,
        "path": str(backup_path),
        "size": backup_path.stat().st_size,
        "created_at": datetime.fromtimestamp(backup_path.stat().st_mtime).replace(microsecond=0).isoformat(),
    }


def list_db_backups(limit: int = 50) -> list[dict[str, Any]]:
    backup_dir = _effective_backup_dir()
    if not backup_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(backup_dir.glob(f"{DB_PATH.stem}_*.db"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        rows.append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
        })
    return rows


async def start_scan_run(total_accounts: int, total_usernames: int) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO scan_runs (
                status, started_at, total_accounts, processed_accounts,
                total_usernames, processed_usernames, found, last_error, cancel_requested
            )
            VALUES ('running', ?, ?, 0, ?, 0, 0, NULL, 0)
            """,
            (_now_iso(), total_accounts, total_usernames),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_scan_run(scan_run_id: int, **fields: Any) -> None:
    allowed = {
        "status",
        "finished_at",
        "total_accounts",
        "processed_accounts",
        "total_usernames",
        "processed_usernames",
        "found",
        "last_error",
        "cancel_requested",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    async with _connect() as db:
        await db.execute(f"UPDATE scan_runs SET {set_clause} WHERE id = ?", (*updates.values(), scan_run_id))
        await db.commit()


async def get_latest_scan_run() -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_scan_runs(limit: int = 20) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (limit,))).fetchall()
        return [dict(row) for row in rows]


async def interrupt_stale_scan_runs(reason: str = "Application restarted before scan finished") -> list[dict[str, Any]]:
    finished_at = _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'running'
            ORDER BY id DESC
            """
        )).fetchall()
        if not rows:
            return []
        await db.execute(
            """
            UPDATE scan_runs
            SET status = 'interrupted',
                finished_at = ?,
                last_error = COALESCE(NULLIF(last_error, ''), ?),
                cancel_requested = 0
            WHERE status = 'running'
            """,
            (finished_at, reason),
        )
        await db.commit()
        return [dict(row) for row in rows]


async def get_scan_run_health(limit: int = 5) -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        running = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'running'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        interrupted = await (await db.execute(
            """
            SELECT *
            FROM scan_runs
            WHERE status = 'interrupted'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        counts = await (await db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM scan_runs
            GROUP BY status
            ORDER BY count DESC
            """
        )).fetchall()
        return {
            "running": [dict(row) for row in running],
            "recent_interrupted": [dict(row) for row in interrupted],
            "status_counts": [dict(row) for row in counts],
        }


async def get_account_ping_stats() -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT sender_id, sender, COUNT(*) AS total,
                   COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   MAX(detected_at) AS last_ping_at
            FROM pings
            GROUP BY sender_id, sender
            """
        )).fetchall()
        return [dict(row) for row in rows]


async def get_report_data(limit: int = 200) -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        totals = dict(await (await db.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status = 'new'), 0) AS new_count,
                   COALESCE(SUM(status = 'important'), 0) AS important_count,
                   COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            """
        )).fetchone())
        top_chats = [dict(row) for row in await (await db.execute(
            """
            SELECT chat, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            GROUP BY chat
            ORDER BY avg_priority DESC, count DESC
            LIMIT 15
            """
        )).fetchall()]
        recent = [dict(row) for row in await (await db.execute(
            "SELECT * FROM pings ORDER BY priority_score DESC, detected_at DESC LIMIT ?",
            (limit,),
        )).fetchall()]
        return {"generated_at": _now_iso(), "totals": totals, "top_chats": top_chats, "recent": recent}


async def record_event(level: str, source: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT INTO app_events (created_at, level, source, message, context) VALUES (?, ?, ?, ?, ?)",
            (_now_iso(), level.upper(), source, message, json.dumps(context or {}, ensure_ascii=False)),
        )
        await db.commit()


async def get_events(limit: int = 100, level: Optional[str] = None) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM app_events"
        params: list[Any] = []
        if level:
            query += " WHERE level = ?"
            params.append(level.upper())
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = await (await db.execute(query, params)).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["context"] = json.loads(item.get("context") or "{}")
            except json.JSONDecodeError:
                item["context"] = {}
            events.append(item)
        return events


async def get_recent_problem_events(limit: int = 10) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT *
            FROM app_events
            WHERE UPPER(level) IN ('ERROR', 'WARNING')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = _json_loads(item.get("context"), {})
            events.append(item)
        return events


async def get_setting(key: str, default: Any = None) -> Any:
    async with _connect() as db:
        async with db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return default


async def set_setting(key: str, value: Any) -> None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        existing = await (await db.execute("SELECT value FROM app_settings WHERE key = ?", (key,))).fetchone()
        old_value = existing["value"] if existing else None
        new_value = json.dumps(value, ensure_ascii=False)
        await db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, new_value, _now_iso()),
        )
        if old_value != new_value:
            await db.execute(
                "INSERT INTO settings_history (key, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)",
                (key, old_value, new_value, _now_iso()),
            )
        await db.commit()


async def get_settings_history(*, key: Optional[str] = None, limit: int = 50) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if key:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history WHERE key = ? ORDER BY changed_at DESC LIMIT ?",
                    (key, limit),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history ORDER BY changed_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
    return [dict(r) for r in rows]


async def get_schema_version() -> int:
    async with _connect() as db:
        async with db.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0


async def get_db_stats() -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute("SELECT COUNT(*) AS count FROM pings")).fetchone())["count"]
        unique_chats = (await (await db.execute("SELECT COUNT(DISTINCT chat_id) AS count FROM pings")).fetchone())["count"]
        favorites = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_favorite = 1")).fetchone())["count"]
        return {"total": total, "unique_chats": unique_chats, "favorites": favorites}


async def get_detailed_stats() -> dict[str, Any]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        top_chats = [dict(row) for row in await (await db.execute(
            "SELECT chat, COUNT(*) AS count FROM pings GROUP BY chat ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        top_senders = [dict(row) for row in await (await db.execute(
            "SELECT sender, COUNT(*) AS count FROM pings GROUP BY sender ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        monthly = [dict(row) for row in await (await db.execute(
            "SELECT strftime('%Y-%m', detected_at) AS month, COUNT(*) AS count FROM pings GROUP BY month ORDER BY month DESC"
        )).fetchall()]
        return {"top_chats": top_chats, "top_senders": top_senders, "monthly": monthly}


async def cleanup_old_data(days: int = 7) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM market_history WHERE fetched_at_iso < datetime('now', '-' || ? || ' days')", (days,))
        events_cutoff = (datetime.now() - timedelta(days=max(days, 7))).replace(microsecond=0).isoformat()
        await db.execute("DELETE FROM app_events WHERE created_at < ?", (events_cutoff,))
        await db.commit()
