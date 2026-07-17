"""Mutable watcher settings extracted from main.py.

Keyword lists and tracked usernames live on the shared ``state`` so every
module sees updates immediately. Runtime tunables (scan/market/giveaway
numbers) are module-level attributes here — read them via the module
(``watch_settings.SCAN_INTERVAL_SECONDS``) so ``apply_runtime_settings``
changes are visible everywhere.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Optional

from telegram_ping_watcher import DEFAULT_USERNAMES, build_ping_regex, normalize_usernames

from . import app_ctx
from .app_ctx import settings, state
from .bot_prefs import parse_hhmm
from .scan import normalize_recent_edit_scan_limit, normalize_scan_history_limit

HIGH_PRIORITY_KEYWORDS_DEFAULT = ["срочно", "важно", "winner", "победитель", "итоги", "приз", "claim", "airdrop", "ton"]

# Runtime tunables (admin-editable; defaults come from .env via app_ctx).
SCAN_INTERVAL_SECONDS = app_ctx.SCAN_INTERVAL_SECONDS
SCAN_ACCOUNT_CONCURRENCY = app_ctx.SCAN_ACCOUNT_CONCURRENCY
SCAN_HISTORY_LIMIT = app_ctx.SCAN_HISTORY_LIMIT
EDIT_SCAN_RECENT_MESSAGES = app_ctx.EDIT_SCAN_RECENT_MESSAGES
STARTUP_SCAN_DELAY_SECONDS = app_ctx.STARTUP_SCAN_DELAY_SECONDS
MARKET_POLL_SECONDS = app_ctx.MARKET_POLL_SECONDS
MARKET_ALERT_CHANGE_PCT = app_ctx.MARKET_ALERT_CHANGE_PCT
MARKET_RETENTION_DAYS = app_ctx.MARKET_RETENTION_DAYS
GIVEAWAY_ACTION_ACCOUNT = app_ctx.GIVEAWAY_ACTION_ACCOUNT
GIVEAWAY_REVIEW_MODE = app_ctx.GIVEAWAY_REVIEW_MODE
GIVEAWAY_ANALYZE_RECENT_MESSAGES = app_ctx.GIVEAWAY_ANALYZE_RECENT_MESSAGES
GIVEAWAY_INACTIVE_CHANNEL_DAYS = app_ctx.GIVEAWAY_INACTIVE_CHANNEL_DAYS
GIVEAWAY_MIN_ACTION_DELAY_SECONDS = app_ctx.GIVEAWAY_MIN_ACTION_DELAY_SECONDS


def load_usernames() -> list[str]:
    configured = [item.strip() for item in settings.usernames.split(",") if item.strip()] or list(DEFAULT_USERNAMES)
    extra = [item.strip() for item in settings.extra_usernames.split(",") if item.strip()]
    return normalize_usernames([*configured, *extra])


def bootstrap_state() -> None:
    """Seed keyword/tracking state from .env defaults (runs at import)."""
    state.ping_usernames = load_usernames()
    state.ping_regex = build_ping_regex(state.ping_usernames)
    state.win_keywords = [item.strip() for item in settings.win_keywords.split(",") if item.strip()]
    state.giveaway_keywords = [item.strip() for item in settings.giveaway_keywords.split(",") if item.strip()]
    state.check_keywords = [item.strip() for item in settings.check_keywords.split(",") if item.strip()]
    state.high_priority_keywords = list(HIGH_PRIORITY_KEYWORDS_DEFAULT)
    state.ignore_keywords = []
    state.join_button_keywords = [item.strip() for item in settings.join_button_keywords.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Notification settings
# ---------------------------------------------------------------------------


def default_notification_settings() -> dict[str, Any]:
    mode = str(settings.broadcast_moderation_mode or "auto").strip().lower()
    return {
        "enabled": True,
        "usernames": [],
        "chats": [],
        "keywords": [],
        "rules": [],
        "cooldown_seconds": 120,
        "include_giveaways": True,
        "include_wins": True,
        "moderation_mode": mode if mode in {"auto", "moderated"} else "auto",
        "approval_timeout_seconds": _as_int(settings.broadcast_approval_timeout_seconds, 300, 30, 3600),
    }


async def load_notification_settings() -> dict[str, Any]:
    from database import get_setting

    saved = await get_setting("notifications", default_notification_settings())
    defaults = default_notification_settings()
    if isinstance(saved, dict):
        defaults.update(saved)
    if defaults.get("moderation_mode") not in ("auto", "moderated"):
        defaults["moderation_mode"] = "auto"
    defaults["approval_timeout_seconds"] = _as_int(defaults.get("approval_timeout_seconds"), 300, 30, 3600)
    return defaults


def is_quiet_time(settings: dict[str, Any]) -> bool:
    quiet = settings.get("quiet_hours") or {}
    if not isinstance(quiet, dict) or not quiet.get("enabled"):
        return False
    try:
        start = datetime.strptime(str(quiet.get("from", "23:00")), "%H:%M").time()
        end = datetime.strptime(str(quiet.get("to", "08:00")), "%H:%M").time()
    except ValueError:
        return False
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


def notification_matches(record: dict[str, Any], settings: dict[str, Any]) -> bool:
    if not settings.get("enabled", True):
        return False
    if is_quiet_time(settings):
        return False
    mentions = " ".join(record.get("mentions") or []).lower()
    chat = (record.get("chat") or "").lower()
    text = (record.get("text") or "").lower()
    usernames = [item.strip().lstrip("@").lower() for item in settings.get("usernames", []) if item.strip()]
    chats = [item.strip().lower() for item in settings.get("chats", []) if item.strip()]
    keywords = [item.strip().lower() for item in settings.get("keywords", []) if item.strip()]
    if usernames and not any(username in mentions for username in usernames):
        return False
    if chats and not any(item in chat for item in chats):
        return False
    if keywords and not any(item in text for item in keywords):
        return False
    if record.get("is_giveaway") and not settings.get("include_giveaways", True):
        return False
    if record.get("is_win") and not settings.get("include_wins", True):
        return False
    for rule in settings.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        rule_usernames = [item.strip().lstrip("@").lower() for item in rule.get("usernames", []) if str(item).strip()]
        rule_chats = [str(item).strip().lower() for item in rule.get("chats", []) if str(item).strip()]
        rule_keywords = [str(item).strip().lower() for item in rule.get("keywords", []) if str(item).strip()]
        if rule_usernames and not any(username in mentions for username in rule_usernames):
            continue
        if rule_chats and not any(item in chat for item in rule_chats):
            continue
        if rule_keywords and not any(item in text for item in rule_keywords):
            continue
        return bool(rule.get("notify", True))
    return True


def should_throttle_notification(record: dict[str, Any], cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False
    if record.get("chat_type") == "channel":
        return False
    raw_key = f"{record.get('chat_id')}|{record.get('mentions')}|{(record.get('text') or '')[:180].lower()}"
    key = hashlib.sha1(raw_key.encode("utf-8", errors="ignore")).hexdigest()
    now = datetime.now()
    last_seen = state.notification_seen.get(key)
    state.notification_seen[key] = now
    stale_before = now - timedelta(hours=2)
    for old_key, seen_at in list(state.notification_seen.items()):
        if seen_at < stale_before:
            state.notification_seen.pop(old_key, None)
    return bool(last_seen and (now - last_seen).total_seconds() < cooldown_seconds)


# ---------------------------------------------------------------------------
# Keyword settings
# ---------------------------------------------------------------------------


def default_keyword_settings() -> dict[str, list[str]]:
    return {
        "win_keywords": state.win_keywords,
        "giveaway_keywords": state.giveaway_keywords,
        "check_keywords": state.check_keywords,
        "high_priority_keywords": state.high_priority_keywords,
        "ignore_keywords": state.ignore_keywords,
    }


async def load_keyword_settings() -> dict[str, list[str]]:
    from database import get_setting

    saved = await get_setting("keywords", default_keyword_settings())
    defaults = default_keyword_settings()
    if isinstance(saved, dict):
        for key in defaults:
            values = saved.get(key)
            if isinstance(values, list):
                defaults[key] = [str(item).strip() for item in values if str(item).strip()]
    return defaults


def apply_keyword_settings(values: dict[str, list[str]]) -> None:
    state.win_keywords = values.get("win_keywords") or state.win_keywords
    state.giveaway_keywords = values.get("giveaway_keywords") or state.giveaway_keywords
    state.check_keywords = values.get("check_keywords") or state.check_keywords
    state.high_priority_keywords = values.get("high_priority_keywords") or state.high_priority_keywords
    state.ignore_keywords = values.get("ignore_keywords") or []


# ---------------------------------------------------------------------------
# Tracking settings
# ---------------------------------------------------------------------------


def default_tracking_settings() -> dict[str, Any]:
    return {"usernames": load_usernames()}


async def load_tracking_settings() -> dict[str, Any]:
    from database import get_setting

    saved = await get_setting("tracking", None)
    if isinstance(saved, dict) and isinstance(saved.get("usernames"), list):
        usernames = normalize_usernames(saved.get("usernames") or [])
        if usernames:
            return {"usernames": usernames, "source": "saved"}
    defaults = default_tracking_settings()
    defaults["source"] = "env"
    return defaults


def apply_tracking_settings(values: dict[str, Any]) -> dict[str, Any]:
    usernames = normalize_usernames(values.get("usernames") or [])
    if not usernames:
        usernames = load_usernames()
    state.ping_usernames = usernames
    state.ping_regex = build_ping_regex(state.ping_usernames)
    # Tracked set changed: drop resolved ids so they re-resolve for the new usernames.
    state.ping_user_ids.clear()
    state.ping_user_ids_resolved.clear()
    return {"usernames": state.ping_usernames}


# ---------------------------------------------------------------------------
# Runtime tunables
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def runtime_settings_payload() -> dict[str, Any]:
    return {
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
        "scan_account_concurrency": SCAN_ACCOUNT_CONCURRENCY,
        "scan_history_limit": SCAN_HISTORY_LIMIT,
        "edit_scan_recent_messages": EDIT_SCAN_RECENT_MESSAGES,
        "startup_scan_delay_seconds": STARTUP_SCAN_DELAY_SECONDS,
        "market_poll_seconds": MARKET_POLL_SECONDS,
        "market_alert_change_pct": MARKET_ALERT_CHANGE_PCT,
        "market_retention_days": MARKET_RETENTION_DAYS,
        "giveaway_action_account": GIVEAWAY_ACTION_ACCOUNT,
        "giveaway_review_mode": GIVEAWAY_REVIEW_MODE,
        "giveaway_analyze_recent_messages": GIVEAWAY_ANALYZE_RECENT_MESSAGES,
        "giveaway_inactive_channel_days": GIVEAWAY_INACTIVE_CHANNEL_DAYS,
        "giveaway_min_action_delay_seconds": GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
    }


def sanitize_runtime_settings(values: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    raw = runtime_settings_payload()
    if isinstance(values, dict):
        raw.update(values)
    action_account = str(raw.get("giveaway_action_account") or settings.giveaway_action_account or "").strip().lstrip("@")
    review_mode = str(raw.get("giveaway_review_mode") or "manual").strip().lower()
    if review_mode not in {"manual", "assisted", "strict"}:
        review_mode = "manual"
    return {
        "scan_interval_seconds": _as_int(raw.get("scan_interval_seconds"), 900, 60, 86400),
        "scan_account_concurrency": _as_int(raw.get("scan_account_concurrency"), settings.scan_account_concurrency, 1, 8),
        "scan_history_limit": normalize_scan_history_limit(raw.get("scan_history_limit"), settings.scan_history_limit),
        "edit_scan_recent_messages": normalize_recent_edit_scan_limit(raw.get("edit_scan_recent_messages"), settings.edit_scan_recent_messages),
        "startup_scan_delay_seconds": _as_int(raw.get("startup_scan_delay_seconds"), settings.startup_scan_delay_seconds, 0, 300),
        "market_poll_seconds": _as_int(raw.get("market_poll_seconds"), 300, 60, 86400),
        "market_alert_change_pct": _as_float(raw.get("market_alert_change_pct"), 5.0, 0.1, 100.0),
        "market_retention_days": _as_int(raw.get("market_retention_days"), 7, 1, 365),
        "giveaway_action_account": action_account,
        "giveaway_review_mode": review_mode,
        "giveaway_analyze_recent_messages": _as_int(raw.get("giveaway_analyze_recent_messages"), 50, 5, 300),
        "giveaway_inactive_channel_days": _as_int(raw.get("giveaway_inactive_channel_days"), 14, 1, 365),
        "giveaway_min_action_delay_seconds": _as_int(raw.get("giveaway_min_action_delay_seconds"), 45, 0, 3600),
    }


async def load_runtime_settings() -> dict[str, Any]:
    from database import get_setting

    saved = await get_setting("runtime", None)
    return sanitize_runtime_settings(saved if isinstance(saved, dict) else None)


def apply_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    global GIVEAWAY_ACTION_ACCOUNT, GIVEAWAY_REVIEW_MODE
    global GIVEAWAY_ANALYZE_RECENT_MESSAGES, GIVEAWAY_INACTIVE_CHANNEL_DAYS, GIVEAWAY_MIN_ACTION_DELAY_SECONDS
    global SCAN_INTERVAL_SECONDS, SCAN_ACCOUNT_CONCURRENCY, SCAN_HISTORY_LIMIT, EDIT_SCAN_RECENT_MESSAGES, STARTUP_SCAN_DELAY_SECONDS
    global MARKET_POLL_SECONDS, MARKET_ALERT_CHANGE_PCT, MARKET_RETENTION_DAYS

    cleaned = sanitize_runtime_settings(values)
    SCAN_INTERVAL_SECONDS = cleaned["scan_interval_seconds"]
    SCAN_ACCOUNT_CONCURRENCY = cleaned["scan_account_concurrency"]
    SCAN_HISTORY_LIMIT = cleaned["scan_history_limit"]
    EDIT_SCAN_RECENT_MESSAGES = cleaned["edit_scan_recent_messages"]
    STARTUP_SCAN_DELAY_SECONDS = cleaned["startup_scan_delay_seconds"]
    MARKET_POLL_SECONDS = cleaned["market_poll_seconds"]
    MARKET_ALERT_CHANGE_PCT = cleaned["market_alert_change_pct"]
    MARKET_RETENTION_DAYS = cleaned["market_retention_days"]
    GIVEAWAY_ACTION_ACCOUNT = cleaned["giveaway_action_account"]
    GIVEAWAY_REVIEW_MODE = cleaned["giveaway_review_mode"]
    GIVEAWAY_ANALYZE_RECENT_MESSAGES = cleaned["giveaway_analyze_recent_messages"]
    GIVEAWAY_INACTIVE_CHANNEL_DAYS = cleaned["giveaway_inactive_channel_days"]
    GIVEAWAY_MIN_ACTION_DELAY_SECONDS = cleaned["giveaway_min_action_delay_seconds"]
    return cleaned


# ---------------------------------------------------------------------------
# Digest settings
# ---------------------------------------------------------------------------


async def load_digest_settings() -> dict:
    from database import get_setting

    saved = await get_setting("digest", None)
    cfg = {"enabled": True, "time": "09:00"}
    if isinstance(saved, dict):
        cfg.update(saved)
    cfg["time"] = parse_hhmm(str(cfg.get("time") or "")) or "09:00"
    cfg["enabled"] = bool(cfg.get("enabled", True))
    return cfg


bootstrap_state()
