"""Shared application context for Pulse Desk FastAPI routers.

Holds the singleton ``settings``, ``state``, ``logger``, all derived
constants, and the FastAPI auth-dependency helpers.  Router modules import
from here instead of from ``main``, which avoids circular imports.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from .config import get_settings
from .logging_config import configure_logging
from .runtime import AppState
from .scan import normalize_scan_history_limit, normalize_recent_edit_scan_limit
from .security import token_matches
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
STATIC_DIR = settings.static_dir
LOG_FILE = settings.log_file

API_ID = settings.api_id
API_HASH = settings.api_hash
BOT_TOKEN = settings.bot_token
WEB_AUTH_TOKEN = settings.web_auth_token.strip()
ADMIN_TOKEN = settings.effective_admin_token
VIEWER_TOKEN = settings.viewer_token.strip()
PUBLIC_SHARE_MODE = settings.public_share_mode
AUTO_JOIN_GIVEAWAYS = settings.auto_join_giveaways
DRY_RUN_GIVEAWAYS = settings.dry_run_giveaways
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
PENDING_AUTH_TTL_SECONDS = settings.pending_auth_ttl_seconds
ALLOW_QUERY_TOKEN = settings.allow_query_token
ADMIN_ID = settings.admin_id

# ---------------------------------------------------------------------------
# FastAPI auth dependencies (copied verbatim from main.py)
# ---------------------------------------------------------------------------


def get_current_role(
    request: Request,
    x_pulse_token: Optional[str] = Header(default=None),
) -> str:
    if not ADMIN_TOKEN and not VIEWER_TOKEN:
        return "admin"
    provided = x_pulse_token or request.cookies.get("pulse_token")
    if not provided and ALLOW_QUERY_TOKEN:
        provided = request.query_params.get("token")
        if provided:
            logger.warning("Access token was provided through URL query; prefer X-Pulse-Token header.")
    if token_matches(provided, ADMIN_TOKEN):
        return "admin"
    if token_matches(provided, WEB_AUTH_TOKEN):
        return "admin"
    if token_matches(provided, VIEWER_TOKEN):
        return "viewer"
    raise HTTPException(status_code=401, detail="Invalid or missing access token")


def require_admin(role: str = Depends(get_current_role)) -> str:
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")
    return role


def require_viewer(role: str = Depends(get_current_role)) -> str:
    """Allow any authenticated role (admin or viewer)."""
    return role


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
