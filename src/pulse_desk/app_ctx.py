"""Общий контекст приложения: синглтоны настроек, состояния и логгера.

Держит ``settings``, ``state``, ``logger`` и производные константы. Модули
импортируют их отсюда, а не из ``main``, чтобы не было кольцевых импортов.

Веб-аутентификации здесь больше нет: с удалением веб-приложения (2026-09-12)
токены доступа потеряли смысл — единственная оставшаяся ручка ``/api/health``
слушает localhost, а права в боте решает ``bot_permissions``.
"""
from __future__ import annotations

from .config import get_settings
from .logging_config import configure_logging
from .runtime import AppState
from .scan import normalize_scan_history_limit, normalize_recent_edit_scan_limit
from .telegram_reconnect import reconnect_delay_seconds as _calc_reconnect_delay  # noqa: F401

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

settings = get_settings()
state = AppState()
logger = configure_logging(settings.log_file, settings.log_level, settings.telethon_log_level)

# ---------------------------------------------------------------------------
# Derived constants (mirror of the block in main.py lines 154-232)
# ---------------------------------------------------------------------------

BASE_DIR = settings.base_dir
LOG_FILE = settings.log_file

API_ID = settings.api_id
API_HASH = settings.api_hash
BOT_TOKEN = settings.bot_token
GIVEAWAY_ACTION_ACCOUNT = settings.giveaway_action_account.strip().lstrip("@")
GIVEAWAY_REVIEW_MODE = settings.giveaway_review_mode.strip().lower() or "manual"
GIVEAWAY_ANALYZE_RECENT_MESSAGES = settings.giveaway_analyze_recent_messages
GIVEAWAY_INACTIVE_CHANNEL_DAYS = settings.giveaway_inactive_channel_days
GIVEAWAY_MIN_ACTION_DELAY_SECONDS = settings.giveaway_min_action_delay_seconds
SCAN_INTERVAL_SECONDS = settings.scan_interval_seconds
SCAN_ACCOUNT_CONCURRENCY = max(1, min(8, settings.scan_account_concurrency))
SCAN_HISTORY_LIMIT = normalize_scan_history_limit(settings.scan_history_limit)
EDIT_SCAN_RECENT_MESSAGES = normalize_recent_edit_scan_limit(settings.edit_scan_recent_messages)
STARTUP_SCAN_DELAY_SECONDS = max(0, min(300, settings.startup_scan_delay_seconds))
STARTUP_SCAN_WAIT_SECONDS = max(0, min(300, settings.startup_scan_wait_seconds))
TELEGRAM_CONNECT_TIMEOUT_SECONDS = max(5, min(120, settings.telegram_connect_timeout_seconds))
TELEGRAM_RETRY_DELAY_SECONDS = max(1, min(60, settings.telegram_retry_delay_seconds))
TELEGRAM_RECONNECT_BASE_SECONDS = max(5, min(600, settings.telegram_reconnect_base_seconds))
TELEGRAM_RECONNECT_MAX_SECONDS = max(
    TELEGRAM_RECONNECT_BASE_SECONDS, min(1800, settings.telegram_reconnect_max_seconds)
)
TELEGRAM_RECONNECT_JITTER_SECONDS = max(0, min(120, settings.telegram_reconnect_jitter_seconds))
MARKET_POLL_SECONDS = settings.market_poll_seconds
MARKET_ALERT_CHANGE_PCT = settings.market_alert_change_pct
MARKET_RETENTION_DAYS = settings.market_retention_days
PINGS_RETENTION_DAYS = settings.pings_retention_days
VACUUM_INTERVAL_HOURS = settings.vacuum_interval_hours
DB_MAX_SIZE_MB = max(0, settings.db_max_size_mb)
DB_ARCHIVE_ENABLED = settings.db_archive_enabled
SCAN_RUNS_RETENTION = max(0, settings.scan_runs_retention)
AUDIT_RETENTION_DAYS = max(0, settings.audit_retention_days)
ARCHIVE_RETENTION_DAYS = max(0, settings.archive_retention_days)
DISK_FREE_ALERT_MB = max(0, settings.disk_free_alert_mb)
FLOOD_WAIT_MAX_SECONDS = max(60, settings.flood_wait_max_seconds)
PENDING_AUTH_TTL_SECONDS = settings.pending_auth_ttl_seconds
ADMIN_ID = settings.admin_id

# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def app_base_url() -> str:
    """Return the canonical base URL for the application."""
    host = settings.host
    port = settings.port
    if (host in ("0.0.0.0", "127.0.0.1", "localhost")) and port == 80:
        return f"http://{host}"
    if (host in ("0.0.0.0", "127.0.0.1", "localhost")) and port == 443:
        return f"https://{host}"
    return f"http://{host}:{port}"
