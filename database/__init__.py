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

from .access_windows import (
    create_access_window,
    create_disable_until_window,
    deactivate_access_window,
    get_access_audit,
    get_access_window,
    list_access_windows,
    list_all_access_windows,
    record_access_audit,
    set_access_window_active,
    set_member_default_policy,
    set_member_timezone,
)
from .backups import (
    backup_db_if_present,
    backups_total_bytes,
    create_db_backup,
    list_db_backups,
    newest_backup_at,
    prune_db_backups,
)
from .boards import (
    get_debt_board,
    get_giveaway_board,
    get_task_overview,
    giveaway_account_counts,
    giveaway_bucket_total,
)
from .bot_access import (
    create_bot_key,
    delete_bot_key,
    delete_broadcast_messages,
    get_bot_key,
    get_bot_key_by_secret,
    get_bot_member,
    get_broadcast_messages,
    list_bot_key_members,
    list_bot_keys,
    list_bot_members,
    prune_broadcast_messages,
    revoke_bot_key,
    save_broadcast_messages,
    set_bot_key_expiry,
    set_bot_key_label,
    set_bot_key_max_uses,
    set_bot_key_permissions,
    set_bot_key_revoked,
    set_bot_key_role,
    set_bot_member_blocked,
    set_bot_member_prefs,
    set_bot_member_role,
    touch_bot_member,
    upsert_bot_member,
)
from .channels import (
    channel_win_stats,
    get_channel_profile,
    get_source_score,
    get_source_scores,
    recalculate_source_scores,
    upsert_channel_profile,
)
from .check_claims import (
    get_check_attempts,
    get_check_claim_stats,
    get_recent_check_claims,
    has_check_claim,
    record_check_claim,
    update_check_claim,
)
from .checkpoints import (
    get_checkpoint,
    get_checkpoints,
    get_latest_checkpoints,
    save_checkpoint,
    save_checkpoints,
)
from .dedupe import (
    get_duplicates,
    get_wins_for_dedupe,
    mark_duplicates,
    propagate_status_to_duplicates,
    unglue_same_chat_copies,
)
from .engagement import (
    account_win_stats,
    engagement_summary,
    get_member_engagement,
    member_engagement_for,
    member_engagement_since,
    member_engagement_stats,
    set_member_engagement,
)
from .ephemeral import (
    delete_ephemeral_rows,
    get_due_ephemeral_messages,
    prune_ephemeral_messages,
    schedule_message_deletion,
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
from .maintenance import (
    archive_db_path,
    cleanup_archive_db,
    cleanup_old_data,
    cleanup_unbounded_tables,
    db_page_stats,
    db_size_bytes,
    enforce_db_size_cap,
    vacuum_main_db,
)
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
    update_ping_meta,
)
from .ping_notifications import (
    count_owed_ping_notifications,
    expire_owed_ping_notifications,
    get_ping_notify_state,
    list_owed_ping_notifications,
    mark_ping_notified,
)
from .pending_broadcasts import (
    claim_pending_broadcast,
    create_pending_broadcast,
    get_due_pending_broadcasts,
    get_pending_broadcast,
    prune_pending_broadcasts,
    release_pending_broadcast,
    set_pending_broadcast_admin_message,
)
from .pending_sends import (
    cancel_pending_send,
    cancel_pending_sends,
    count_pending_sends,
    get_due_pending_sends,
    mark_pending_send_result,
    pending_sends_backlog,
    prune_pending_sends,
    queue_pending_send,
)
from .push import delete_push_subscription, get_push_subscriptions, save_push_subscription
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
