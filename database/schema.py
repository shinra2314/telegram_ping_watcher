"""Schema creation and migrations. Bump SCHEMA_VERSION in ``_core`` and add a
migration branch here when the schema changes."""
from __future__ import annotations

import asyncio
import logging

import aiosqlite

from ._core import SCHEMA_VERSION, _columns, _connect, _now_iso, _sync_ping_indexes
from .backups import backup_db_if_present


async def init_db() -> None:
    # A snapshot + zip of the whole file — off the event loop. A failed backup
    # (full disk, locked file) is logged, never a reason to refuse to start.
    try:
        await asyncio.to_thread(backup_db_if_present)
    except Exception:
        logging.getLogger("pulse_desk").exception("Startup database backup failed")
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
                is_check BOOLEAN DEFAULT 0,
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
            "is_check": "ALTER TABLE pings ADD COLUMN is_check BOOLEAN DEFAULT 0",
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
            "deleted_at": "ALTER TABLE pings ADD COLUMN deleted_at TEXT",
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
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
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
            CREATE TABLE IF NOT EXISTS bot_access_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT DEFAULT '',
                secret TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked INTEGER NOT NULL DEFAULT 0,
                permissions TEXT DEFAULT ''
            )
            """
        )
        bot_keys_columns = await _columns(db, "bot_access_keys")
        if "permissions" not in bot_keys_columns:
            await db.execute("ALTER TABLE bot_access_keys ADD COLUMN permissions TEXT DEFAULT ''")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_members (
                tg_id INTEGER PRIMARY KEY,
                tg_username TEXT DEFAULT '',
                name TEXT DEFAULT '',
                key_id INTEGER,
                role TEXT NOT NULL DEFAULT 'viewer',
                joined_at TEXT NOT NULL,
                last_seen_at TEXT,
                blocked INTEGER NOT NULL DEFAULT 0,
                notification_prefs TEXT DEFAULT '',
                permissions TEXT DEFAULT ''
            )
            """
        )
        bot_members_columns = await _columns(db, "bot_members")
        if "notification_prefs" not in bot_members_columns:
            await db.execute("ALTER TABLE bot_members ADD COLUMN notification_prefs TEXT DEFAULT ''")
        if "access_default_policy" not in bot_members_columns:
            await db.execute("ALTER TABLE bot_members ADD COLUMN access_default_policy TEXT NOT NULL DEFAULT 'allow'")
        if "timezone" not in bot_members_columns:
            await db.execute("ALTER TABLE bot_members ADD COLUMN timezone TEXT DEFAULT ''")
        if "permissions" not in bot_members_columns:
            await db.execute("ALTER TABLE bot_members ADD COLUMN permissions TEXT DEFAULT ''")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_broadcast_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                tg_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS access_schedule (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id          INTEGER NOT NULL,
                enabled        INTEGER NOT NULL DEFAULT 1,
                active         INTEGER NOT NULL DEFAULT 1,
                start_at       TEXT,
                end_at         TEXT,
                timezone       TEXT NOT NULL DEFAULT 'UTC',
                repeat_rule    TEXT NOT NULL DEFAULT '{"type":"none"}',
                priority       INTEGER NOT NULL DEFAULT 100,
                label          TEXT DEFAULT '',
                created_by     INTEGER,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                FOREIGN KEY (tg_id) REFERENCES bot_members(tg_id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS access_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id       INTEGER NOT NULL,
                schedule_id INTEGER,
                action      TEXT NOT NULL,
                actor       TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT,
                created_at  TEXT NOT NULL
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

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ping_id INTEGER,
                notif_type TEXT NOT NULL DEFAULT 'mention',
                message TEXT NOT NULL,
                link TEXT DEFAULT '',
                file_path TEXT DEFAULT '',
                admin_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_by INTEGER,
                decided_at TEXT,
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE SET NULL
            )
            """
        )
        pending_columns = await _columns(db, "pending_broadcasts")
        if "bc_token" not in pending_columns:
            await db.execute("ALTER TABLE pending_broadcasts ADD COLUMN bc_token TEXT DEFAULT ''")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_pending_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                send_at TEXT NOT NULL,
                token TEXT NOT NULL DEFAULT '',
                notif_type TEXT NOT NULL DEFAULT 'mention',
                message TEXT NOT NULL,
                link TEXT DEFAULT '',
                file_path TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                cancelled_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS member_engagement (
                tg_id INTEGER NOT NULL,
                ping_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tg_id, ping_id),
                FOREIGN KEY (ping_id) REFERENCES pings(id) ON DELETE CASCADE
            )
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_chat_type ON pings(chat_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_detected_at ON pings(detected_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_status ON pings(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_giveaway ON pings(is_giveaway)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_giveaway_status ON pings(giveaway_status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_win ON pings(is_win)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_is_check ON pings(is_check)")
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
        await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_token ON bot_broadcast_messages(token)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_access_sched_user ON access_schedule(tg_id, active)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_access_sched_window ON access_schedule(start_at, end_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_access_sched_priority ON access_schedule(tg_id, priority DESC)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_access_audit_user ON access_audit(tg_id, created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_broadcasts_status ON pending_broadcasts(status, expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_sends_due ON bot_pending_sends(send_at, sent_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_sends_token ON bot_pending_sends(token)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_member_engagement_tg ON member_engagement(tg_id)")

        # --- schema 22: indexes for the bot's hot paths ----------------------
        # The giveaway board filters on `(is_giveaway = 1 OR is_win = 1)` and
        # orders by detected_at, four buckets plus four uncapped COUNT(*) per
        # render. Without this it was four full scans of `pings` joined to
        # `giveaway_candidates` on every home screen.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pings_board ON pings(is_giveaway, is_win, detected_at DESC)"
        )
        # The board's `sort=posted` branch orders by COALESCE(date, detected_at).
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_date ON pings(date)")
        # `GROUP BY chat` in both analytics builders.
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_chat ON pings(chat)")
        # list_bot_keys counts members per key with a correlated subquery; without
        # this it scanned all of bot_members once per key row.
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_members_key ON bot_members(key_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_scores_score ON source_scores(score DESC, total_pings DESC)"
        )

        # --- schema 23: clean chat + one-time invites -------------------------
        # Bot messages to delete later: minor notifications (mentions, market
        # alerts, recovery notices) for whoever switched auto-delete on. Telegram
        # lets a bot delete a private-chat message only while it is under 48 h
        # old, so `delete_after` is always set inside that window.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_ephemeral_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                delete_after TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ephemeral_due ON bot_ephemeral_messages(delete_after)"
        )
        # 0 = unlimited (every key issued before this column existed).
        if "max_uses" not in await _columns(db, "bot_access_keys"):
            await db.execute("ALTER TABLE bot_access_keys ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 0")
        # Detection latency (latency.py): a win usually arrives as an edit of an
        # old post, so its delay is edit → the moment the row became a win. Both
        # stay NULL on rows stored before this, and those are left out of stats.
        ping_cols = await _columns(db, "pings")
        if "edited_at" not in ping_cols:
            await db.execute("ALTER TABLE pings ADD COLUMN edited_at TEXT")
        if "win_detected_at" not in ping_cols:
            await db.execute("ALTER TABLE pings ADD COLUMN win_detected_at TEXT")
        # Copies of one winners post point at the primary row (dedupe.py); the
        # debts board lists primaries only.
        if "duplicate_of" not in ping_cols:
            await db.execute("ALTER TABLE pings ADD COLUMN duplicate_of INTEGER")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pings_duplicate_of ON pings(duplicate_of)")

        # --- schema 24: the owner's ping cards are an outbox -------------------
        # A card used to be one shot: sent on first detection or never, because
        # every later pass finds the row already stored. `notified_at` is when the
        # owner's card went out, `win_notified_at` when a 🏆 card did (a win that
        # was edited into a known post owes a second card). NULL = still owed;
        # `ping_notify.retry_owed_notifications` delivers it. Rows stored before
        # this column are settled here, or the first start would page the owner
        # with the whole history.
        if "notified_at" not in ping_cols:
            await db.execute("ALTER TABLE pings ADD COLUMN notified_at TEXT")
            await db.execute("UPDATE pings SET notified_at = COALESCE(detected_at, ?)", (_now_iso(),))
        if "win_notified_at" not in ping_cols:
            await db.execute("ALTER TABLE pings ADD COLUMN win_notified_at TEXT")
            await db.execute(
                "UPDATE pings SET win_notified_at = COALESCE(win_detected_at, detected_at, ?) WHERE is_win = 1",
                (_now_iso(),),
            )
        # Partial: the owed set is a handful of rows, the table is thousands.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pings_notify_owed ON pings(detected_at) "
            "WHERE notified_at IS NULL OR (is_win = 1 AND win_notified_at IS NULL)"
        )

        # --- schema 25: wallet-bot check auto-claim (check_claimer.py) ---------
        # One row per (account, bot, code): a relay step updates it in place. The
        # owner's own checks are journaled once per code with an empty session.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS check_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                bot TEXT NOT NULL,
                code TEXT NOT NULL,
                session TEXT NOT NULL DEFAULT '',
                account TEXT NOT NULL DEFAULT '',
                chat_id INTEGER,
                chat TEXT NOT NULL DEFAULT '',
                message_id INTEGER,
                link TEXT NOT NULL DEFAULT '',
                amount TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL,
                reply TEXT NOT NULL DEFAULT '',
                press_ms INTEGER,
                UNIQUE(session, bot, code)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_check_claims_created ON check_claims(created_at)")
        # --- schema 26: how fast each press went out (update received → startBot answered).
        if "press_ms" not in await _columns(db, "check_claims"):
            await db.execute("ALTER TABLE check_claims ADD COLUMN press_ms INTEGER")
        await db.commit()


async def get_schema_version() -> int:
    async with _connect() as db:
        async with db.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0
