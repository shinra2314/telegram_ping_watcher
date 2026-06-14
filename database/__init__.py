"""SQLite persistence layer for Pulse Desk, split into focused modules.

This package replaces the old monolithic ``database.py``. The public API is
re-exported here so ``import database`` / ``from database import X`` keep
working unchanged. ``database.DB_PATH`` stays a mutable module attribute
(tests monkeypatch it); all submodules resolve it dynamically through
``_core.db_path()``.

Schema changes: bump ``SCHEMA_VERSION`` in ``_core.py`` and add a migration
branch in ``schema.init_db``.
"""
from __future__ import annotations

from ._core import (
    BASE_DIR,
    DEFAULT_BACKUP_DIR,
    DEFAULT_DB_PATH,
    SCHEMA_VERSION,
    _add_where,
    _columns,
    _connect,
    _env_int,
    _fts_query,
    _json_loads,
    _now_iso,
    _parse_iso_datetime,
    _parse_mentions,
    _search_tokens,
    _sync_ping_indexes,
)

# Mutable on purpose: tests and tooling override these per-run.
DB_PATH = DEFAULT_DB_PATH
BACKUP_DIR = DEFAULT_BACKUP_DIR

from .backups import backup_db_if_present, create_db_backup, list_db_backups
from .boards import get_debt_board, get_giveaway_board, get_task_overview
from .bot_access import (
    create_bot_key,
    delete_broadcast_messages,
    get_bot_key_by_secret,
    get_bot_member,
    get_broadcast_messages,
    list_bot_keys,
    list_bot_members,
    prune_broadcast_messages,
    revoke_bot_key,
    save_broadcast_messages,
    set_bot_member_blocked,
    set_bot_member_prefs,
    touch_bot_member,
    upsert_bot_member,
)
from .channels import (
    get_channel_profile,
    get_source_score,
    get_source_scores,
    recalculate_source_scores,
    update_channel_deadlines,
    upsert_channel_profile,
)
from .checkpoints import (
    get_checkpoint,
    get_checkpoints,
    get_latest_checkpoints,
    save_checkpoint,
    save_checkpoints,
)
from .events import get_events, get_recent_problem_events, record_event
from .giveaways import (
    get_giveaway_actions,
    get_recent_giveaway_actions,
    get_giveaway_candidate,
    get_giveaway_candidates,
    reconcile_giveaway_flags,
    reconcile_giveaway_outcomes,
    reconcile_win_flags,
    record_giveaway_action,
    seed_giveaway_candidates_from_pings,
    update_giveaway_candidate_status,
    upsert_giveaway_candidate,
)
from .maintenance import cleanup_old_data
from .market import get_market_history, save_market_snapshot
from .outbox import cleanup_outbox, enqueue_outbox_event, get_outbox_after, get_outbox_stats
from .pings import (
    _build_pings_filters,
    add_ping_tag,
    delete_ping,
    delete_ping_by_message_id,
    get_all_tags,
    get_giveaway_pings_with_links,
    get_ping_by_id,
    get_ping_by_message_ref,
    get_pings,
    get_pings_grouped,
    mark_ping_read,
    mark_pings_read,
    rebuild_search_indexes,
    remove_ping_tag,
    save_ping,
    search_pings_fts,
    toggle_favorite,
    update_ping_deadline,
    update_ping_meta,
)
from .push import delete_push_subscription, get_push_subscriptions, save_push_subscription
from .reminders import (
    backfill_deadlines_from_text,
    get_due_reminders,
    mark_reminder_sent,
    replace_ping_reminders,
)
from .scan_runs import (
    get_latest_scan_run,
    get_scan_run_health,
    get_scan_runs,
    interrupt_stale_scan_runs,
    start_scan_run,
    update_scan_run,
)
from .schema import get_schema_version, init_db
from .settings_kv import get_setting, get_settings_history, set_setting
from .stats import get_account_ping_stats, get_db_stats, get_detailed_stats, get_report_data
