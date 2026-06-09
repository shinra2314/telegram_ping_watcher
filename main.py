from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
import sys
from typing import Any, Optional

import aiosqlite
import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from telethon import Button, TelegramClient, events, types
from telethon.errors import (
    FloodWaitError,
    MessageNotModifiedError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.tl.functions.channels import GetFullChannelRequest, LeaveChannelRequest

from database import (
    DB_PATH,
    cleanup_old_data,
    cleanup_outbox,
    create_db_backup,
    delete_ping_by_message_id,
    delete_ping,
    enqueue_outbox_event,
    get_checkpoints,
    get_events,
    get_db_stats,
    get_debt_board,
    get_giveaway_actions,
    get_giveaway_board,
    get_giveaway_candidate,
    get_giveaway_candidates,
    get_ping_by_id,
    get_ping_by_message_ref,
    get_account_ping_stats,
    backfill_deadlines_from_text,
    get_channel_profile,
    get_market_history,
    get_outbox_after,
    get_pings,
    get_pings_grouped,
    get_report_data,
    get_schema_version,
    get_source_score,
    get_source_scores,
    get_setting,
    get_settings_history,
    get_task_overview,
    get_latest_scan_run,
    get_latest_checkpoints,
    get_outbox_stats,
    get_recent_problem_events,
    get_scan_runs,
    get_scan_run_health,
    init_db,
    interrupt_stale_scan_runs,
    list_db_backups,
    mark_ping_read as mark_ping_read_db,
    mark_pings_read as mark_pings_read_db,
    record_event,
    record_giveaway_action,
    recalculate_source_scores,
    rebuild_search_indexes,
    reconcile_giveaway_flags,
    reconcile_giveaway_outcomes,
    reconcile_win_flags,
    replace_ping_reminders,
    get_due_reminders,
    mark_reminder_sent,
    save_checkpoint,
    save_checkpoints,
    save_market_snapshot,
    save_ping,
    save_push_subscription,
    delete_push_subscription,
    get_push_subscriptions,
    create_bot_key,
    list_bot_keys,
    get_bot_key_by_secret,
    revoke_bot_key,
    upsert_bot_member,
    get_bot_member,
    list_bot_members,
    touch_bot_member,
    set_bot_member_blocked,
    search_pings_fts,
    seed_giveaway_candidates_from_pings,
    set_setting,
    start_scan_run,
    add_ping_tag,
    remove_ping_tag,
    get_all_tags,
    toggle_favorite,
    update_giveaway_candidate_status,
    update_ping_deadline,
    update_ping_meta,
    update_scan_run,
    upsert_giveaway_candidate,
    upsert_channel_profile,
)
from telegram_ping_watcher import (
    DEFAULT_USERNAMES,
    build_ping_regex,
    chat_type_from_entity,
    message_to_record,
    message_looks_like_broadcast_channel,
    normalize_usernames,
)

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk import APP_VERSION
from pulse_desk.api_models import (
    AuthRequest,
    KeywordsRequest,
    NotificationSettingsRequest,
    PingMetaRequest,
    RulesUiRequest,
    RuntimeSettingsRequest,
    SavedFiltersRequest,
    SignInRequest,
    UsernamesRequest,
)
from pulse_desk.dashboard import build_dashboard_summary
from pulse_desk.digest import format_digest
from pulse_desk.deadlines import iso_or_none, parse_claim_deadline, parse_deadline, parse_participation_deadline
from pulse_desk.giveaways import analyze_giveaway, giveaway_outcome_resolution, inactive_channel_candidate, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule
from pulse_desk.jobs import runtime_health, start_supervised_task, start_tracked_task
from pulse_desk.live import publish_live_event, register_dashboard_invalidator
from pulse_desk.push import generate_vapid_keys, send_push, vapid_public_key_b64
from pulse_desk.scan import channel_sweep_start_id, normalize_recent_edit_scan_limit, normalize_scan_history_limit
from pulse_desk.security import generate_access_key, is_weak_token, mask_secret
from pulse_desk.statuses import ACTION_STATUSES, GIVEAWAY_STATUSES, PING_STATUSES
from pulse_desk.telegram_errors import (
    AUTH_KEY_DUPLICATED_STATUS,
    auth_key_duplicated_message,
    is_auth_key_duplicated,
)
from pulse_desk.telegram_reconnect import reconnect_delay_seconds as calculate_reconnect_delay_seconds


from pulse_desk.app_ctx import (
    ADMIN_ID,
    ADMIN_TOKEN,
    ALLOW_QUERY_TOKEN,
    AUTO_JOIN_GIVEAWAYS,
    DRY_RUN_GIVEAWAYS,
    EDIT_SCAN_RECENT_MESSAGES,
    GIVEAWAY_ACTION_ACCOUNT,
    GIVEAWAY_ANALYZE_RECENT_MESSAGES,
    GIVEAWAY_INACTIVE_CHANNEL_DAYS,
    GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
    GIVEAWAY_REVIEW_MODE,
    FLOOD_WAIT_MAX_SECONDS,
    MARKET_ALERT_CHANGE_PCT,
    MARKET_POLL_SECONDS,
    MARKET_RETENTION_DAYS,
    PINGS_RETENTION_DAYS,
    VACUUM_INTERVAL_HOURS,
    PENDING_AUTH_TTL_SECONDS,
    PUBLIC_SHARE_MODE,
    SCAN_ACCOUNT_CONCURRENCY,
    SCAN_HISTORY_LIMIT,
    SCAN_INTERVAL_SECONDS,
    STARTUP_SCAN_DELAY_SECONDS,
    STARTUP_SCAN_WAIT_SECONDS,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_RECONNECT_BASE_SECONDS,
    TELEGRAM_RECONNECT_JITTER_SECONDS,
    TELEGRAM_RECONNECT_MAX_SECONDS,
    TELEGRAM_RETRY_DELAY_SECONDS,
    VIEWER_TOKEN,
    WEB_AUTH_TOKEN,
    app_base_url,
    get_current_role,
    logger,
    require_admin,
    require_viewer,
    settings,
    state,
)

BASE_DIR = settings.base_dir
STATIC_DIR = settings.static_dir
LOG_FILE = settings.log_file
API_ID = settings.api_id
API_HASH = settings.api_hash
BOT_TOKEN = settings.bot_token

clients = state.clients
bot_client: Optional[TelegramClient] = None
bot_id: Optional[int] = None
bot_username: Optional[str] = None
connected_user_ids = state.connected_user_ids
pending_auths = state.pending_auths
accounts_state = state.accounts_state
scan_lock = state.scan_lock
scan_status = state.scan_status
scan_cancel_event = state.scan_cancel_event


def parse_csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_sessions_manually() -> list[str]:
    return settings.discover_sessions()


def load_usernames() -> list[str]:
    configured = [item.strip() for item in settings.usernames.split(",") if item.strip()] or list(DEFAULT_USERNAMES)
    extra = [item.strip() for item in settings.extra_usernames.split(",") if item.strip()]
    return normalize_usernames([*configured, *extra])


state.session_names = get_sessions_manually()
SESSION_NAMES = state.session_names
state.ping_usernames = load_usernames()
ping_usernames = state.ping_usernames
state.ping_regex = build_ping_regex(ping_usernames)
ping_regex = state.ping_regex
ping_user_ids = state.ping_user_ids
ping_user_ids_resolved = state.ping_user_ids_resolved

WIN_KEYWORDS = [item.strip() for item in settings.win_keywords.split(",") if item.strip()]
GIVEAWAY_KEYWORDS = [item.strip() for item in settings.giveaway_keywords.split(",") if item.strip()]
HIGH_PRIORITY_KEYWORDS = ["срочно", "важно", "winner", "победитель", "итоги", "приз", "claim", "airdrop", "ton"]
IGNORE_KEYWORDS: list[str] = []
JOIN_BUTTON_KEYWORDS = [item.strip() for item in settings.join_button_keywords.split(",") if item.strip()]
state.win_keywords = WIN_KEYWORDS
state.giveaway_keywords = GIVEAWAY_KEYWORDS
state.join_button_keywords = JOIN_BUTTON_KEYWORDS
notification_seen = state.notification_seen


CHANNEL_PROFILE_TTL_SECONDS = 6 * 60 * 60


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def start_background_task(name: str, coro) -> asyncio.Task:
    return start_tracked_task(state, logger, name, coro)


def start_supervised(name: str, coro_factory, *, backoff_base: float = 5.0, backoff_max: float = 300.0) -> asyncio.Task:
    return start_supervised_task(
        state, logger, name, coro_factory, backoff_base=backoff_base, backoff_max=backoff_max
    )


def flood_wait_seconds(raw_seconds: int) -> int:
    """Bound a FloodWait duration so we still respect Telegram's limit but cap absurd values."""
    try:
        value = int(raw_seconds)
    except (TypeError, ValueError):
        return FLOOD_WAIT_MAX_SECONDS
    if value <= 0:
        return 1
    return min(value, FLOOD_WAIT_MAX_SECONDS)


def mark_account_cooldown(session_name: str, seconds: int) -> None:
    if not session_name or seconds <= 0:
        return
    until = datetime.now() + timedelta(seconds=seconds)
    state.account_cooldown_until[session_name] = until
    account = accounts_state.get(session_name)
    if account is not None:
        account["cooldown_until"] = until.isoformat(timespec="seconds")


def account_in_cooldown(session_name: str) -> bool:
    until = state.account_cooldown_until.get(session_name)
    if not until:
        return False
    if datetime.now() >= until:
        state.account_cooldown_until.pop(session_name, None)
        account = accounts_state.get(session_name)
        if account is not None:
            account.pop("cooldown_until", None)
        return False
    return True


def telegram_client_for_session(session_name: str) -> TelegramClient:
    return TelegramClient(
        str(settings.session_path(session_name)),
        int(API_ID),
        API_HASH,
        auto_reconnect=True,
        connection_retries=5,
        retry_delay=TELEGRAM_RETRY_DELAY_SECONDS,
        request_retries=3,
        timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    )


def reconnect_delay_seconds(session_name: str, attempt: int) -> int:
    return calculate_reconnect_delay_seconds(
        session_name,
        attempt,
        base_seconds=TELEGRAM_RECONNECT_BASE_SECONDS,
        max_seconds=TELEGRAM_RECONNECT_MAX_SECONDS,
        jitter_seconds=TELEGRAM_RECONNECT_JITTER_SECONDS,
    )


async def record_app_event(level: str, source: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
    try:
        await record_event(level, source, message, context)
    except Exception:
        logger.debug("Could not persist app event", exc_info=True)


async def mark_auth_key_duplicated(session_name: str, client: Optional[TelegramClient], exc: BaseException) -> None:
    message = auth_key_duplicated_message(session_name)
    account = accounts_state.setdefault(session_name, {"session_name": session_name})
    user_id = account.get("user_id")
    if user_id in connected_user_ids:
        connected_user_ids.discard(user_id)
    account.update(
        {
            "status": AUTH_KEY_DUPLICATED_STATUS,
            "last_error": message,
            "disconnected_at": now_iso(),
            "manual_disconnect": True,
        }
    )
    if client and client in clients:
        clients.remove(client)
    if client:
        with suppress(Exception):
            await client.disconnect()
    logger.error("Telegram session %s was invalidated by two-IP use: %s", session_name, exc)
    await record_app_event(
        "ERROR",
        "telegram",
        "Telegram session invalidated by two-IP use",
        {"session_name": session_name, "status": AUTH_KEY_DUPLICATED_STATUS, "error": str(exc)},
    )


def is_pending_auth_expired(auth_data: dict[str, Any]) -> bool:
    created_at = auth_data.get("created_at")
    if not isinstance(created_at, datetime):
        return False
    return datetime.now() - created_at > timedelta(seconds=PENDING_AUTH_TTL_SECONDS)


def default_session_name_for_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    return f"session_{digits}" if digits else "session_account"


def resolve_auth_session_alias(raw_name: str) -> str:
    candidate = raw_name.strip().replace(".session", "")
    if not candidate:
        return ""
    known_names = list(dict.fromkeys([*SESSION_NAMES, *settings.discover_sessions(), *accounts_state.keys()]))
    by_lower = {name.lower(): name for name in known_names}
    exact = by_lower.get(candidate.lower())
    if exact:
        return exact
    if len(candidate) >= 3:
        prefix_matches = [name for name in known_names if name.lower().startswith(candidate.lower())]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
    return candidate


def normalize_auth_session_name(raw_name: str, phone: str) -> str:
    session_name = resolve_auth_session_alias(raw_name or "") or default_session_name_for_phone(phone)
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", session_name):
        raise HTTPException(400, "Session name must be 2-64 ASCII letters, digits, underscores or hyphens.")
    if session_name.lower() in {"pulse_bot", "pulse_desk_web"}:
        raise HTTPException(400, "This session name is reserved.")
    return session_name


def describe_sent_code_type(result: Any) -> tuple[str, str]:
    sent_type = getattr(result, "type", None)
    type_name = type(sent_type).__name__
    labels = {
        "SentCodeTypeApp": ("app", "Код отправлен в Telegram на уже авторизованное устройство."),
        "SentCodeTypeSms": ("sms", "Код отправлен SMS-сообщением."),
        "SentCodeTypeCall": ("call", "Код придет телефонным звонком."),
        "SentCodeTypeFlashCall": ("flash_call", "Telegram ожидает flash-call для подтверждения."),
        "SentCodeTypeMissedCall": ("missed_call", "Telegram ожидает подтверждение через пропущенный звонок."),
        "SentCodeTypeFragmentSms": ("fragment_sms", "Код отправлен через Fragment SMS."),
        "SentCodeTypeEmailCode": ("email", "Код отправлен на почту, привязанную к аккаунту."),
    }
    return labels.get(type_name, (type_name or "unknown", "Код запрошен у Telegram. Проверьте Telegram, SMS, звонки и почту."))


def check_is_win(text: str) -> bool:
    return is_win_text(text, WIN_KEYWORDS)


def check_is_giveaway(text: str, chat_type: str = "") -> bool:
    return matches_strict_giveaway_rule(text, chat_type, GIVEAWAY_KEYWORDS)


def priority_label(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "normal"


def apply_priority(record: dict[str, Any]) -> dict[str, Any]:
    text = (record.get("text") or "").lower()
    chat = (record.get("chat") or "").lower()
    score = 0
    if record.get("is_win"):
        score += 70
    if record.get("is_giveaway"):
        score += 45
    if record.get("chat_type") == "channel":
        score += 10
    if record.get("chat_type") == "private":
        score += 20
    score += min(len(record.get("mentions") or []) * 5, 20)
    if any(keyword.lower() in text for keyword in HIGH_PRIORITY_KEYWORDS):
        score += 20
    if any(keyword.lower() in text or keyword.lower() in chat for keyword in IGNORE_KEYWORDS):
        score -= 40
    record["priority_score"] = max(0, min(score, 100))
    record["priority_label"] = priority_label(record["priority_score"])
    return record


def apply_giveaway_state(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("is_win") and is_giveaway_outcome_text(record.get("text") or "") and giveaway_outcome_resolution(record.get("text") or "") == "missed":
        record["giveaway_status"] = "missed"
    else:
        record["giveaway_status"] = "pending" if record.get("is_giveaway") else ""
    return record


def apply_action_state(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("giveaway_status") == "missed":
        record["action_status"] = "missed"
    elif record.get("is_win"):
        record["action_status"] = "claim_prize"
    elif record.get("is_giveaway"):
        record["action_status"] = "waiting_result"
    elif record.get("priority_score", 0) >= 60:
        record["action_status"] = "to_check"
    else:
        record["action_status"] = "new"
    return record


def _profile_is_fresh(profile: Optional[dict[str, Any]]) -> bool:
    if not profile or not profile.get("fetched_at"):
        return False
    try:
        fetched_at = datetime.fromisoformat(profile["fetched_at"])
    except ValueError:
        return False
    return (datetime.now() - fetched_at).total_seconds() < CHANNEL_PROFILE_TTL_SECONDS


def record_reference_datetime(record: dict[str, Any]) -> datetime:
    for key in ("date", "detected_at"):
        value = record.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    return datetime.now()


# publish_live_event now lives in pulse_desk.live (imported above).
# The dashboard cache invalidation hook is registered after the cache is defined.


async def refresh_channel_profile(
    client: TelegramClient,
    chat_id: int,
    chat_label: str = "",
    force: bool = False,
    deadline_reference: Optional[datetime] = None,
) -> dict[str, Any]:
    cached = await get_channel_profile(chat_id)
    if cached and not force and _profile_is_fresh(cached):
        return cached
    try:
        entity = await client.get_entity(chat_id)
        if not isinstance(entity, types.Channel):
            return cached or {"chat_id": chat_id, "chat": chat_label, "last_error": "not a channel"}
        full = await client(GetFullChannelRequest(entity))
        description = getattr(getattr(full, "full_chat", None), "about", "") or ""
        match = parse_deadline(description, now=deadline_reference or datetime.now())
        deadline_at = iso_or_none(match.deadline_at if match else None)
        deadline_text = match.matched_text if match else ""
        chat_name = getattr(entity, "title", None) or chat_label or str(chat_id)
        username = getattr(entity, "username", None) or ""
        await upsert_channel_profile(
            chat_id=chat_id,
            chat=chat_name,
            username=username,
            description=description,
            deadline_at=deadline_at,
            deadline_text=deadline_text,
            last_error="",
        )
        return await get_channel_profile(chat_id) or {}
    except Exception as exc:
        await upsert_channel_profile(
            chat_id=chat_id,
            chat=(cached or {}).get("chat") or chat_label or str(chat_id),
            username=(cached or {}).get("username") or "",
            description=(cached or {}).get("description") or "",
            deadline_at=(cached or {}).get("deadline_at"),
            deadline_text=(cached or {}).get("deadline_text") or "",
            last_error=str(exc),
        )
        logger.debug("Could not refresh channel profile for %s", chat_id, exc_info=True)
        return await get_channel_profile(chat_id) or {"chat_id": chat_id, "chat": chat_label, "last_error": str(exc)}


def parse_profile_deadline(profile: dict[str, Any], reference: datetime):
    description = profile.get("description") or ""
    if not description:
        return None
    return parse_participation_deadline(description, now=reference)


async def apply_deadline_metadata(client: TelegramClient, record: dict[str, Any], chat_id: Optional[int]) -> dict[str, Any]:
    if not record.get("is_giveaway"):
        return record
    reference = record_reference_datetime(record)
    is_outcome = is_giveaway_outcome_text(record.get("text") or "")
    if is_outcome:
        match = parse_claim_deadline(record.get("text") or "", now=reference)
        if match:
            record["deadline_at"] = iso_or_none(match.deadline_at)
            record["deadline_source"] = "claim_window_text"
            record["deadline_text"] = match.matched_text
        else:
            record["deadline_at"] = None
            record["deadline_source"] = ""
            record["deadline_text"] = ""
        return record
    if record.get("chat_type") == "channel" and chat_id:
        profile = await refresh_channel_profile(client, chat_id, record.get("chat") or "", deadline_reference=reference)
        profile_match = parse_profile_deadline(profile, reference)
        if profile_match:
            record["deadline_at"] = iso_or_none(profile_match.deadline_at)
            record["deadline_source"] = "channel_description"
            record["deadline_text"] = profile_match.matched_text
        else:
            match = parse_participation_deadline(record.get("text") or "", now=reference)
            if match:
                record["deadline_at"] = iso_or_none(match.deadline_at)
                record["deadline_source"] = "channel_post_text"
                record["deadline_text"] = match.matched_text
            else:
                record["deadline_at"] = None
                record["deadline_source"] = "channel_description_missing"
                record["deadline_text"] = profile.get("last_error") or "Дедлайн не найден в описании канала или тексте поста"
        return record
    match = parse_participation_deadline(record.get("text") or "", now=reference)
    if match:
        record["deadline_at"] = iso_or_none(match.deadline_at)
        record["deadline_source"] = "message_text"
        record["deadline_text"] = match.matched_text
    return record


async def refresh_ping_deadline(client: TelegramClient, ping_id: int) -> dict[str, Any]:
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise HTTPException(404, "Ping not found")
    if not ping.get("is_giveaway") and not ping.get("is_win"):
        raise HTTPException(400, "Ping is not a giveaway or win")
    if (ping.get("deadline_source") or "") == "manual":
        return {"status": "ok", "ping": ping, "deadline_at": ping.get("deadline_at"), "deadline_source": "manual", "deadline_text": ping.get("deadline_text") or ""}

    reference = record_reference_datetime(ping)
    deadline_at: Optional[str] = None
    deadline_source = ""
    deadline_text = ""
    is_outcome = is_giveaway_outcome_text(ping.get("text") or "") or bool(ping.get("is_win"))
    if ping.get("chat_type") == "channel" and ping.get("chat_id") and not is_outcome:
        profile = await refresh_channel_profile(
            client,
            int(ping["chat_id"]),
            ping.get("chat") or "",
            force=True,
            deadline_reference=reference,
        )
        profile_match = parse_profile_deadline(profile, reference)
        if profile_match:
            deadline_at = iso_or_none(profile_match.deadline_at)
            deadline_source = "channel_description"
            deadline_text = profile_match.matched_text

    if not deadline_at:
        match = parse_claim_deadline(ping.get("text") or "", now=reference) if is_outcome else parse_participation_deadline(ping.get("text") or "", now=reference)
        if match:
            deadline_at = iso_or_none(match.deadline_at)
            deadline_source = "claim_window_text" if is_outcome else ("channel_post_text" if ping.get("chat_type") == "channel" else "message_text")
            deadline_text = match.matched_text
        else:
            deadline_source = "channel_description_missing" if ping.get("chat_type") == "channel" and not is_outcome else ""
            deadline_text = "Дедлайн не найден в описании канала или тексте поста" if deadline_source else ""

    next_action = "claim_prize" if is_outcome else "waiting_result"
    await update_ping_deadline(ping_id, deadline_at, deadline_source, deadline_text, next_action)
    await replace_ping_reminders(ping_id, deadline_at)
    updated = await get_ping_by_id(ping_id)
    await publish_live_event("deadline-updated", {"ping_id": ping_id, "deadline_at": deadline_at, "deadline_source": deadline_source})
    return {"status": "ok", "ping": updated, "deadline_at": deadline_at, "deadline_source": deadline_source, "deadline_text": deadline_text}


def default_notification_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "usernames": [],
        "chats": [],
        "keywords": [],
        "rules": [],
        "cooldown_seconds": 120,
        "include_giveaways": True,
        "include_wins": True,
    }


async def load_notification_settings() -> dict[str, Any]:
    saved = await get_setting("notifications", default_notification_settings())
    defaults = default_notification_settings()
    if isinstance(saved, dict):
        defaults.update(saved)
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


def default_keyword_settings() -> dict[str, list[str]]:
    return {
        "win_keywords": WIN_KEYWORDS,
        "giveaway_keywords": GIVEAWAY_KEYWORDS,
        "high_priority_keywords": HIGH_PRIORITY_KEYWORDS,
        "ignore_keywords": IGNORE_KEYWORDS,
    }


async def load_keyword_settings() -> dict[str, list[str]]:
    saved = await get_setting("keywords", default_keyword_settings())
    defaults = default_keyword_settings()
    if isinstance(saved, dict):
        for key in defaults:
            values = saved.get(key)
            if isinstance(values, list):
                defaults[key] = [str(item).strip() for item in values if str(item).strip()]
    return defaults


def apply_keyword_settings(values: dict[str, list[str]]) -> None:
    global WIN_KEYWORDS, GIVEAWAY_KEYWORDS, HIGH_PRIORITY_KEYWORDS, IGNORE_KEYWORDS
    WIN_KEYWORDS = values.get("win_keywords") or WIN_KEYWORDS
    GIVEAWAY_KEYWORDS = values.get("giveaway_keywords") or GIVEAWAY_KEYWORDS
    HIGH_PRIORITY_KEYWORDS = values.get("high_priority_keywords") or HIGH_PRIORITY_KEYWORDS
    IGNORE_KEYWORDS = values.get("ignore_keywords") or []


def default_tracking_settings() -> dict[str, Any]:
    return {"usernames": load_usernames()}


async def load_tracking_settings() -> dict[str, Any]:
    saved = await get_setting("tracking", None)
    if isinstance(saved, dict) and isinstance(saved.get("usernames"), list):
        usernames = normalize_usernames(saved.get("usernames") or [])
        if usernames:
            return {"usernames": usernames, "source": "saved"}
    defaults = default_tracking_settings()
    defaults["source"] = "env"
    return defaults


def apply_tracking_settings(values: dict[str, Any]) -> dict[str, Any]:
    global ping_usernames, ping_regex
    usernames = normalize_usernames(values.get("usernames") or [])
    if not usernames:
        usernames = load_usernames()
    ping_usernames = usernames
    ping_regex = build_ping_regex(ping_usernames)
    # Tracked set changed: drop resolved ids so they re-resolve for the new usernames.
    ping_user_ids.clear()
    ping_user_ids_resolved.clear()
    return {"usernames": ping_usernames}


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
        "dry_run_giveaways": DRY_RUN_GIVEAWAYS,
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
        "dry_run_giveaways": _as_bool(raw.get("dry_run_giveaways"), True),
        "giveaway_review_mode": review_mode,
        "giveaway_analyze_recent_messages": _as_int(raw.get("giveaway_analyze_recent_messages"), 50, 5, 300),
        "giveaway_inactive_channel_days": _as_int(raw.get("giveaway_inactive_channel_days"), 14, 1, 365),
        "giveaway_min_action_delay_seconds": _as_int(raw.get("giveaway_min_action_delay_seconds"), 45, 0, 3600),
    }


async def load_runtime_settings() -> dict[str, Any]:
    saved = await get_setting("runtime", None)
    return sanitize_runtime_settings(saved if isinstance(saved, dict) else None)


def apply_runtime_settings(values: dict[str, Any]) -> dict[str, Any]:
    global DRY_RUN_GIVEAWAYS, GIVEAWAY_ACTION_ACCOUNT, GIVEAWAY_REVIEW_MODE
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
    DRY_RUN_GIVEAWAYS = cleaned["dry_run_giveaways"]
    GIVEAWAY_REVIEW_MODE = cleaned["giveaway_review_mode"]
    GIVEAWAY_ANALYZE_RECENT_MESSAGES = cleaned["giveaway_analyze_recent_messages"]
    GIVEAWAY_INACTIVE_CHANNEL_DAYS = cleaned["giveaway_inactive_channel_days"]
    GIVEAWAY_MIN_ACTION_DELAY_SECONDS = cleaned["giveaway_min_action_delay_seconds"]
    return cleaned


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
    last_seen = notification_seen.get(key)
    notification_seen[key] = now
    stale_before = now - timedelta(hours=2)
    for old_key, seen_at in list(notification_seen.items()):
        if seen_at < stale_before:
            notification_seen.pop(old_key, None)
    return bool(last_seen and (now - last_seen).total_seconds() < cooldown_seconds)


def remember_message(key: str, limit: int = 5000) -> bool:
    return state.remember_message(key, limit=limit)


def _button_is_safe_join(button: Any) -> bool:
    if not isinstance(button, types.KeyboardButtonCallback):
        return False
    button_text = (getattr(button, "text", "") or "").lower()
    return any(keyword.lower() in button_text for keyword in JOIN_BUTTON_KEYWORDS)


def _find_safe_join_button(message: Any) -> Optional[tuple[int, int, str]]:
    markup = getattr(message, "reply_markup", None)
    if not isinstance(markup, types.ReplyInlineMarkup):
        return None
    for row_index, row in enumerate(markup.rows):
        for button_index, button in enumerate(row.buttons):
            if _button_is_safe_join(button):
                return row_index, button_index, getattr(button, "text", "") or "join"
    return None


async def find_giveaway_action_client() -> Optional[TelegramClient]:
    target = GIVEAWAY_ACTION_ACCOUNT.lower()
    fallback: Optional[TelegramClient] = None
    for client in list(clients):
        session_name = str(getattr(client, "_session_name_custom", "") or "").lower()
        if session_name == target:
            fallback = client
        try:
            me = await client.get_me()
        except Exception:
            continue
        username = (getattr(me, "username", "") or "").lower()
        if username == target:
            return client
        if session_name == target:
            fallback = client
    return fallback


async def analyze_and_store_giveaway(client: TelegramClient, ping_id: Optional[int], record: dict[str, Any], message: Any = None) -> Optional[dict[str, Any]]:
    if not ping_id or not record.get("is_giveaway"):
        return None
    try:
        candidate = await analyze_giveaway(
            client=client,
            ping_id=int(ping_id),
            text=record.get("text") or "",
            message=message,
            join_keywords=JOIN_BUTTON_KEYWORDS,
            recent_limit=GIVEAWAY_ANALYZE_RECENT_MESSAGES,
        )
        saved = await upsert_giveaway_candidate(candidate)
        await record_giveaway_action(int(ping_id), "analyze", saved.get("status", "pending_review"), "system", context={"score": saved.get("score")})
        await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": saved.get("status"), "score": saved.get("score")})
        return saved
    except Exception as exc:
        logger.exception("Giveaway analysis failed")
        await record_giveaway_action(int(ping_id), "analyze", "failed", "system", str(exc))
        return None


async def load_giveaway_message(client: TelegramClient, ping: dict[str, Any]):
    chat_id = ping.get("chat_id")
    message_id = ping.get("message_id")
    if not chat_id or not message_id:
        raise HTTPException(400, "Ping does not have Telegram chat/message identifiers")
    entity = await client.get_entity(int(chat_id))
    message = await client.get_messages(entity, ids=int(message_id))
    if not message:
        raise HTTPException(404, "Telegram message not found or not accessible")
    return message


async def confirm_safe_giveaway_join(ping_id: int, actor: str = "admin") -> dict[str, Any]:
    candidate = await get_giveaway_candidate(ping_id)
    if not candidate:
        raise HTTPException(404, "Giveaway candidate not found")
    if candidate.get("status") in {"manual_required", "blocked"} or candidate.get("blocked_reason"):
        await record_giveaway_action(ping_id, "confirm", "blocked", actor, candidate.get("blocked_reason") or "Manual review required")
        raise HTTPException(409, candidate.get("blocked_reason") or "Manual review required")
    client = await find_giveaway_action_client()
    if not client:
        await record_giveaway_action(ping_id, "confirm", "failed", actor, f"Action account @{GIVEAWAY_ACTION_ACCOUNT} is not online")
        raise HTTPException(400, f"Action account @{GIVEAWAY_ACTION_ACCOUNT} is not online")
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise HTTPException(404, "Ping not found")
    try:
        message = await load_giveaway_message(client, ping)
        button = _find_safe_join_button(message)
        if not button:
            await update_giveaway_candidate_status(ping_id, "manual_required", "No safe Telegram callback join button found")
            await record_giveaway_action(ping_id, "confirm", "manual_required", actor, "No safe Telegram callback join button found")
            raise HTTPException(409, "No safe Telegram callback join button found")
        row_index, button_index, button_text = button
        if DRY_RUN_GIVEAWAYS:
            dry_run_message = f"DRY_RUN_GIVEAWAYS is enabled; would click button: {button_text}"
            await record_giveaway_action(ping_id, "confirm", "dry_run", actor, dry_run_message, {"account": GIVEAWAY_ACTION_ACCOUNT})
            await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "dry_run"})
            return {"status": "dry_run", "message": dry_run_message}
        now = datetime.now()
        if state.last_giveaway_action_at:
            elapsed = (now - state.last_giveaway_action_at).total_seconds()
            remaining = GIVEAWAY_MIN_ACTION_DELAY_SECONDS - elapsed
            if remaining > 0:
                message = f"Safe delay is active; retry in {int(remaining) + 1}s"
                await record_giveaway_action(ping_id, "confirm", "delayed", actor, message)
                raise HTTPException(429, message)
        await update_giveaway_candidate_status(ping_id, "confirmed")
        await record_giveaway_action(ping_id, "confirm", "confirmed", actor, f"Clicked button: {button_text}", {"account": GIVEAWAY_ACTION_ACCOUNT})
        await message.click(row_index, button_index)
        state.last_giveaway_action_at = datetime.now()
        await update_giveaway_candidate_status(ping_id, "joined")
        await update_ping_meta(ping_id, giveaway_status="pending", action_status="waiting_result")
        await record_giveaway_action(ping_id, "join_button", "joined", actor, f"Clicked button: {button_text}", {"account": GIVEAWAY_ACTION_ACCOUNT})
        await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "joined"})
        return {"status": "joined", "message": f"Clicked safe Telegram join button as @{GIVEAWAY_ACTION_ACCOUNT}"}
    except FloodWaitError as exc:
        reason = f"Telegram FloodWait {exc.seconds}s; manual retry required"
        await update_giveaway_candidate_status(ping_id, "manual_required", reason)
        await record_giveaway_action(ping_id, "confirm", "manual_required", actor, reason, {"seconds": exc.seconds})
        raise HTTPException(429, reason) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await update_giveaway_candidate_status(ping_id, "failed", str(exc))
        await record_giveaway_action(ping_id, "confirm", "failed", actor, str(exc))
        raise HTTPException(500, str(exc)) from exc


async def ensure_bot_connected() -> bool:
    if not bot_client:
        return False
    try:
        if not bot_client.is_connected():
            await bot_client.connect()
        return True
    except Exception:
        logger.exception("Telegram bot reconnect failed")
        await record_app_event("ERROR", "notifications", "Telegram bot reconnect failed")
        return False


async def send_admin_bot_message(message: str, *, buttons: Optional[list[list[Button]]] = None) -> bool:
    if not bot_client or not ADMIN_ID:
        return False
    for attempt in range(3):
        try:
            if not await ensure_bot_connected():
                return False
            await bot_client.send_message(ADMIN_ID, message, buttons=buttons, link_preview=False)
            return True
        except FloodWaitError as exc:
            wait = flood_wait_seconds(exc.seconds)
            await record_app_event("WARNING", "notifications", "Telegram bot flood wait", {"seconds": exc.seconds})
            await asyncio.sleep(wait)
        except Exception as exc:
            logger.warning("Telegram bot notification attempt %s failed: %s", attempt + 1, exc)
            if attempt >= 2:
                await record_app_event("ERROR", "notifications", "Telegram bot notification failed", {"error": str(exc)})
                return False
            await asyncio.sleep(2 * (attempt + 1))
    return False


async def send_bot_notification(record: dict[str, Any], ping_id: Optional[int] = None, auto_joined: bool = False) -> None:
    if not bot_client:
        return
    try:
        settings = await load_notification_settings()
        if not notification_matches(record, settings):
            return
        if should_throttle_notification(record, int(settings.get("cooldown_seconds", 120) or 0)):
            await record_app_event("INFO", "notifications", "Similar notification suppressed", {"chat": record.get("chat"), "mentions": record.get("mentions")})
            return
        title = "🔔 Новое упоминание"
        if record.get("is_win"):
            title = "🏆 Похоже на победу в розыгрыше"
        elif record.get("is_giveaway"):
            title = "🎁 Найден розыгрыш"
        candidate = await get_giveaway_candidate(int(ping_id)) if ping_id and record.get("is_giveaway") else None
        candidate_line = ""
        if candidate:
            candidate_line = (
                f"\n🧮 Участие: score `{candidate.get('score', 0)}` · status `{candidate.get('status', 'pending_review')}`"
                + (f"\n✋ Manual: {candidate.get('blocked_reason')}" if candidate.get("blocked_reason") else "")
            )
        mentions = ", ".join(record.get("mentions", [])) or "—"
        msg = (
            f"**{title}**\n"
            "━━━━━━━━━━━━━━━\n"
            f"💬 Чат: `{record.get('chat', 'unknown')}` · _{record.get('chat_type', 'unknown')}_\n"
            f"👤 От: {record.get('sender', 'unknown')}\n"
            f"🏷 Упоминания: {mentions}\n"
            f"🤝 Авто-вступление: {'✅ да' if auto_joined else '❌ нет'}"
            f"{candidate_line}\n\n"
            f"{(record.get('text') or '')[:800]}"
        )
        buttons: list[list[Button]] = []
        link = record.get("link")
        if link and not link.startswith("нет "):
            buttons.append([Button.url("🔗 Открыть в Telegram", link)])
        if ping_id:
            buttons.append([
                Button.inline("⭐ В избранное", data=f"fav_{ping_id}"),
                Button.inline("✓ Прочитано", data=f"read_{ping_id}"),
            ])
        if ping_id and record.get("is_giveaway"):
            buttons.append([
                Button.inline("✅ Участвовать", data=f"gconfirm_{ping_id}"),
                Button.inline("⏭ Пропустить", data=f"gskip_{ping_id}"),
            ])
        sent = await send_admin_bot_message(msg, buttons=buttons)
        if not sent:
            logger.error("Failed to send bot notification after retries")
        # Mirror notification to viewer members (read-only, no action buttons).
        member_buttons: Optional[list[list[Button]]] = None
        if link and not link.startswith("нет "):
            member_buttons = [[Button.url("Открыть в Telegram", link)]]
        await broadcast_member_notification(msg, member_buttons)
    except Exception:
        logger.exception("Failed to send bot notification")


async def broadcast_member_notification(message: str, buttons: Optional[list[list[Button]]] = None) -> None:
    """Send a notification to every non-blocked viewer member of the bot."""
    if not bot_client:
        return
    try:
        members = await list_bot_members()
    except Exception:
        logger.exception("Failed to load bot members for broadcast")
        return
    admin_ids = {int(ADMIN_ID)} if ADMIN_ID else set()
    for member in members:
        if member.get("blocked"):
            continue
        tg_id = member.get("tg_id")
        if tg_id is None or int(tg_id) in admin_ids:
            continue  # owner already notified via send_admin_bot_message
        try:
            if not await ensure_bot_connected():
                return
            await bot_client.send_message(int(tg_id), message, buttons=buttons, link_preview=False)
        except FloodWaitError as exc:
            await asyncio.sleep(flood_wait_seconds(exc.seconds))
        except Exception as exc:
            logger.warning("Failed to notify bot member %s: %s", tg_id, exc)


async def get_chat_type(client: TelegramClient, chat_id: int) -> str:
    try:
        entity = await client.get_entity(chat_id)
        return chat_type_from_entity(entity)
    except Exception:
        logger.debug("Could not resolve chat type for %s", chat_id, exc_info=True)
    return "unknown"


async def get_message_chat_type(client: TelegramClient, message: Any) -> str:
    try:
        chat = await message.get_chat()
        resolved = chat_type_from_entity(chat)
        if resolved != "unknown":
            return resolved
    except Exception:
        logger.debug("Could not resolve chat from message", exc_info=True)
    if message_looks_like_broadcast_channel(message):
        return "channel"
    chat_id = getattr(message, "chat_id", None)
    if chat_id is not None:
        return await get_chat_type(client, chat_id)
    return "unknown"


async def resolve_ping_user_ids(client: TelegramClient) -> None:
    """Resolve tracked usernames to user ids so text-mentions (name links) match.

    Channels can ping a user by their display name instead of @username; those
    arrive as MessageEntityMentionName carrying a user_id, not text. Each username
    is looked up at most once per process to avoid repeated network calls.
    """
    pending = [u for u in ping_usernames if u.lower() not in ping_user_ids_resolved]
    for username in pending:
        ping_user_ids_resolved.add(username.lower())
        try:
            entity = await client.get_entity(username)
        except Exception:
            logger.debug("Could not resolve tracked username %s to user id", username, exc_info=True)
            continue
        uid = getattr(entity, "id", None)
        if uid is not None:
            ping_user_ids[int(uid)] = username


async def process_ping_message(
    client: TelegramClient,
    message: Any,
    *,
    account_label: str = "",
    notify: bool = True,
    source: str = "telegram",
) -> Optional[int]:
    chat_type = await get_message_chat_type(client, message)
    if chat_type != "channel":
        return None
    await resolve_ping_user_ids(client)
    record = await message_to_record(client, message, ping_regex, ping_usernames, tracked_ids=ping_user_ids or None)
    if not record:
        return None
    record["chat_type"] = chat_type
    record["detected_at"] = now_iso()
    record["is_win"] = check_is_win(record["text"])
    record["is_giveaway"] = check_is_giveaway(record["text"], record["chat_type"])
    apply_giveaway_state(record)
    await apply_deadline_metadata(client, record, getattr(message, "chat_id", None))
    record["auto_joined"] = False
    apply_priority(record)
    apply_action_state(record)

    existing = await get_ping_by_message_ref(record.get("chat_id"), record.get("message_id"))
    ping_id = await save_ping(record)
    if ping_id and record.get("deadline_at"):
        await replace_ping_reminders(int(ping_id), record.get("deadline_at"), record.get("reminder_at"))
    if ping_id and record.get("is_giveaway"):
        await analyze_and_store_giveaway(client, int(ping_id), record, message)

    if not ping_id:
        return None
    if existing is None:
        logger.info("Channel ping found by %s in %s", account_label or "unknown", record["chat"])
        await record_app_event(
            "INFO",
            source,
            "Channel ping found",
            {"account": account_label, "chat": record.get("chat"), "ping_id": ping_id},
        )
        await publish_live_event(
            "ping",
            {
                "ping_id": ping_id,
                "chat": record.get("chat"),
                "deadline_at": record.get("deadline_at"),
                "action_status": record.get("action_status"),
            },
        )
    elif source == "telegram-edit":
        await publish_live_event(
            "ping-updated",
            {
                "ping_id": ping_id,
                "chat": record.get("chat"),
                "source": source,
            },
        )
    if notify and existing is None:
        await send_bot_notification(record, ping_id=ping_id, auto_joined=record["auto_joined"])
    if notify and existing is None and ping_id:
        saved_record = {**record, "id": ping_id}
        asyncio.create_task(_fan_push_ping(saved_record))
    return int(ping_id) if existing is None else None


async def monitor_client_disconnect(client: TelegramClient, session_name: str) -> None:
    disconnect_error: Optional[str] = None
    try:
        await client.disconnected
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            await mark_auth_key_duplicated(session_name, client, exc)
            return
        disconnect_error = str(exc)
        logger.warning("Telegram client %s disconnected with error: %s", session_name, exc)
    finally:
        if client in clients:
            clients.remove(client)
        account = accounts_state.setdefault(session_name, {"session_name": session_name})
        if account.get("status") == AUTH_KEY_DUPLICATED_STATUS:
            return
        user_id = account.get("user_id")
        if user_id in connected_user_ids:
            connected_user_ids.discard(user_id)
        if state.shutting_down or account.get("manual_disconnect"):
            account.update({"status": "disconnected", "disconnected_at": now_iso()})
            return
        attempt = int(account.get("reconnect_attempts") or 0) + 1
        delay = reconnect_delay_seconds(session_name, attempt)
        account.update({
            "status": "reconnecting",
            "last_error": disconnect_error or "Telegram connection dropped",
            "disconnected_at": now_iso(),
            "reconnect_attempts": attempt,
            "next_reconnect_in_seconds": delay,
        })
        await record_app_event(
            "WARNING",
            "telegram",
            "Telegram account disconnected; reconnect scheduled",
            {"session_name": session_name, "attempt": attempt, "delay_seconds": delay, "error": disconnect_error},
        )
        await asyncio.sleep(delay)
        account["next_reconnect_in_seconds"] = 0
        await start_client(session_name)


async def start_client(session_name: str, retry_count: int = 0) -> None:
    global ping_regex
    if not API_ID or not API_HASH:
        logger.warning("Telegram API credentials are missing.")
        return

    clean_name = session_name.replace(".session", "")
    if clean_name in accounts_state and accounts_state[clean_name].get("status") == "online":
        return

    account = accounts_state.setdefault(clean_name, {"session_name": clean_name})
    account.update({"status": "connecting", "last_error": None, "manual_disconnect": False, "connecting_at": now_iso()})
    client = telegram_client_for_session(clean_name)

    try:
        await client.connect()
        if not await client.is_user_authorized():
            account.update({"status": "unauthorized", "last_error": "Session is not authorized."})
            await client.disconnect()
            logger.warning("Session %s is not authorized.", clean_name)
            return

        me = await client.get_me()
        if me.id in connected_user_ids:
            account.update({"status": "duplicate", "user_id": me.id, "username": me.username})
            await client.disconnect()
            return

        connected_user_ids.add(me.id)
        client._session_name_custom = clean_name
        client._retry_count = 0
        account.update({
            "status": "online",
            "user_id": me.id,
            "username": me.username,
            "display": f"@{me.username}" if me.username else str(me.id),
            "connected_at": now_iso(),
            "reconnect_attempts": 0,
            "next_reconnect_in_seconds": 0,
        })

        @client.on(events.NewMessage())
        async def handler(event):
            if bot_id and event.sender_id == bot_id:
                return
            if not remember_message(f"{event.chat_id}:{event.message.id}"):
                return
            await process_ping_message(client, event.message, account_label=account.get("display", clean_name), notify=True)

        @client.on(events.MessageEdited())
        async def edit_handler(event):
            if bot_id and event.sender_id == bot_id:
                return
            edit_date = getattr(event.message, "edit_date", None) or getattr(event.message, "date", None) or ""
            if not remember_message(f"edit:{event.chat_id}:{event.message.id}:{edit_date}"):
                return
            await process_ping_message(
                client,
                event.message,
                account_label=account.get("display", clean_name),
                notify=True,
                source="telegram-edit",
            )

        @client.on(events.MessageDeleted())
        async def delete_handler(event):
            for msg_id in event.deleted_ids:
                if event.chat_id:
                    await delete_ping(event.chat_id, msg_id)
                else:
                    await delete_ping_by_message_id(msg_id)

        clients.append(client)
        start_background_task(f"telegram-watch:{clean_name}", monitor_client_disconnect(client, clean_name))
        logger.info("Account connected: %s", account.get("display", clean_name))
    except FloodWaitError as exc:
        account.update({"status": "rate_limited", "last_error": f"Flood wait {exc.seconds}s"})
        await asyncio.sleep(exc.seconds)
        await start_client(session_name, retry_count + 1)
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            await mark_auth_key_duplicated(clean_name, client, exc)
            return
        account.update({"status": "error", "last_error": str(exc)})
        logger.exception("Telegram client %s failed", clean_name)
        try:
            await client.disconnect()
        except Exception:
            logger.debug("Failed to disconnect broken client %s", clean_name, exc_info=True)
        if retry_count < 5:
            wait = reconnect_delay_seconds(clean_name, retry_count + 1)
            account.update({"reconnect_attempts": retry_count + 1, "next_reconnect_in_seconds": wait})
            await asyncio.sleep(wait)
            account["next_reconnect_in_seconds"] = 0
            await start_client(session_name, retry_count + 1)


async def disconnect_account(session_name: str) -> bool:
    for client in list(clients):
        if getattr(client, "_session_name_custom", "") == session_name:
            accounts_state.setdefault(session_name, {"session_name": session_name}).update({"manual_disconnect": True})
            try:
                me = await client.get_me()
                connected_user_ids.discard(me.id)
            except Exception:
                logger.debug("Could not get account id while disconnecting %s", session_name, exc_info=True)
            await client.disconnect()
            if client in clients:
                clients.remove(client)
            accounts_state.setdefault(session_name, {"session_name": session_name})["status"] = "offline"
            return True
    return False


async def init_bot() -> None:
    global bot_client, bot_id
    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.info("Telegram bot is not configured.")
        return
    try:
        bot_client = telegram_client_for_session("pulse_bot")
        await bot_client.start(bot_token=BOT_TOKEN)
        bot_me = await bot_client.get_me()
        bot_id = bot_me.id
        bot_username = bot_me.username
        logger.info("Bot started: @%s", bot_me.username)

        # ---- access control -------------------------------------------------
        def _bot_admin_chat_ids() -> set[int]:
            ids: set[int] = set()
            if ADMIN_ID:
                ids.add(int(ADMIN_ID))
            raw = (settings.bot_admin_chats or "").strip()
            if raw:
                for chunk in raw.split(","):
                    chunk = chunk.strip()
                    if chunk:
                        try:
                            ids.add(int(chunk))
                        except ValueError:
                            pass
            return ids

        async def bot_role(sender_id: int) -> Optional[str]:
            """Resolve a Telegram user to 'admin', 'viewer', or None (no access)."""
            if sender_id in _bot_admin_chat_ids():
                return "admin"
            member = await get_bot_member(sender_id)
            if member and not member.get("blocked"):
                await touch_bot_member(sender_id)
                return member.get("role") or "viewer"
            return None

        async def deny_non_admin(event) -> bool:
            """True if the sender must be blocked from an owner-only action."""
            role = await bot_role(event.sender_id)
            if role is None:
                return True  # stranger — ignore silently
            if role != "admin":
                await event.respond("⛔ **Только для владельца.**\nВ режиме просмотра это действие недоступно.")
                return True
            return False

        def main_menu_buttons(role: str) -> list[list[Button]]:
            rows = [
                [Button.inline("📊 Статистика", b"menu_stats"), Button.inline("🎁 Розыгрыши", b"menu_giveaways")],
                [Button.inline("🕐 Последние", b"menu_recent"), Button.inline("💹 Курсы", b"menu_market")],
                [Button.inline("🛰 Статус", b"menu_status"), Button.inline("❓ Помощь", b"menu_help")],
            ]
            if role == "admin":
                rows.append([
                    Button.inline("🔑 Ключи", b"menu_keys"),
                    Button.inline("🔄 Скан", b"menu_scan"),
                    Button.inline("📜 Логи", b"menu_logs"),
                ])
            return rows

        async def safe_edit(event, *args, **kwargs) -> None:
            """Edit the callback message, ignoring 'not modified' errors."""
            try:
                await event.edit(*args, **kwargs)
            except MessageNotModifiedError:
                await event.answer()

        DIV = "━━━━━━━━━━━━━━━"

        def _fmt_dt(value: Optional[str]) -> str:
            """Trim an ISO timestamp to `MM-DD HH:MM` for compact display."""
            if not value:
                return "—"
            text = str(value).replace("T", " ")
            return text[5:16] if len(text) >= 16 else text

        def help_text(role: str) -> str:
            lines = [
                "🛰 **PULSE DESK**",
                "__Мониторинг каналов и розыгрышей__",
                DIV,
                "📋 **Команды**",
                "• /menu — главное меню",
                "• /stats — статистика",
                "• /status — состояние аккаунтов",
                "• /giveaways — розыгрыши",
                "• /recent `[N]` — последние упоминания",
                "• /search `<текст>` — поиск",
                "• /market — курсы",
                "• /ping — проверка связи",
            ]
            if role == "admin":
                lines += [
                    "",
                    "👑 **Владелец**",
                    "• /scan — скан истории",
                    "• /logs — последние логи",
                    "• /export — CSV выгрузка",
                    "• /newkey `[метка]` — создать ключ",
                    "• /keys — список ключей",
                    "• /members — пользователи",
                ]
            else:
                lines += ["", "👁 __Режим: только просмотр__"]
            return "\n".join(lines)

        # ---- shared renderers (reused by slash commands and menu callbacks) -
        async def render_stats() -> str:
            analytics = await build_analytics()
            return (
                "📊 **Статистика**\n"
                f"{DIV}\n"
                f"📨 Всего записей: `{analytics['total_pings']}`\n"
                f"🆕 Новых: `{analytics['new_pings']}`\n"
                f"⭐ Избранных: `{analytics['favorites']}`\n"
                f"🛰 Аккаунтов онлайн: `{analytics['accounts_online']}`"
            )

        async def render_status() -> str:
            accounts_online = sum(1 for acc in list(accounts_state.values()) if acc.get("status") == "online")
            account_lines = [
                f"  {'🟢' if a.get('status') == 'online' else '🔴'} `{a.get('session_name', '?')}` — {a.get('status', 'unknown')}"
                for a in list(accounts_state.values())
            ]
            try:
                db_size_mb = DB_PATH.stat().st_size / 1024 / 1024 if DB_PATH.exists() else 0
            except Exception:
                db_size_mb = 0
            uptime_sec = int((datetime.now() - state.started_at).total_seconds())
            uptime_str = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
            running_jobs = sorted(name for name, task in state.background_tasks.items() if not task.done())
            last_scan = _fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            return (
                f"🛰 **Статус** · `v{APP_VERSION}`\n"
                f"{DIV}\n"
                f"⏱ Uptime: `{uptime_str}`\n"
                f"💾 База: `{db_size_mb:.1f} MB`\n"
                f"🛰 Аккаунты: `{accounts_online}/{len(accounts_state)}` online\n"
                f"🔄 Скан: `{last_scan}` · {state.last_scan_status or '—'}\n"
                f"⚙️ Jobs ({len(running_jobs)}): `{', '.join(running_jobs) or '—'}`\n\n"
                "👤 **Аккаунты**\n" + ("\n".join(account_lines) if account_lines else "  __нет аккаунтов__")
            )

        async def render_giveaways() -> str:
            board = await get_giveaway_board(limit=10)
            buckets = board.get("buckets") or {}
            stats = board.get("stats") or {}
            lines = [
                "🎁 **Розыгрыши**",
                DIV,
                f"⏳ Ожидание: `{stats.get('waiting', 0)}`  ·  🎁 Призы: `{stats.get('to_claim', 0)}`  ·  ❗ Срочные: `{stats.get('urgent', 0)}`",
            ]
            urgent = buckets.get("urgent") or []
            if urgent:
                lines.append("\n⚠️ **Срочное**")
                for row in urgent[:5]:
                    lines.append(f"  • `{_fmt_dt(row.get('deadline_at'))}` · {row.get('chat') or '?'}\n    {(row.get('text') or '')[:110]}")
            else:
                lines.append("\n✅ __Срочных розыгрышей нет.__")
            return "\n".join(lines)

        async def render_recent(n: int = 5) -> str:
            rows = await get_pings(limit=n)
            if not rows:
                return "🕐 **Последние**\n" + DIV + "\n📭 __Упоминаний пока нет.__"
            result = [f"🕐 **Последние {len(rows)}**", DIV]
            for row in rows:
                priority = row.get("priority_label") or ""
                badge = "🔥" if priority == "critical" else "⚡" if priority == "high" else "•"
                link = row.get("link") or ""
                result.append(
                    f"{badge} `{_fmt_dt(row.get('detected_at'))}` · {row['chat']}\n"
                    f"{(row.get('text') or '')[:160]}"
                    + (f"\n🔗 {link}" if link else "")
                )
            return "\n\n".join(result)

        async def render_market() -> str:
            market = await get_market_history(limit=1)
            if not market:
                return "💹 **Курсы**\n" + DIV + "\n📉 __Данные пока недоступны.__"
            m = market[0]
            return (
                "💹 **Курсы**\n"
                f"{DIV}\n"
                f"🟠 BTC: `${m.get('bitcoin', {}).get('usd', 0):,}`\n"
                f"🔷 ETH: `${m.get('ethereum', {}).get('usd', 0):,}`\n"
                f"💎 TON: `${m.get('the-open-network', {}).get('usd', 0):.3f}`\n"
                f"🟣 SOL: `${m.get('solana', {}).get('usd', 0):.2f}`"
            )

        async def render_keys_text() -> str:
            keys = await list_bot_keys()
            if not keys:
                return "🔑 **Ключи доступа**\n" + DIV + "\n📭 __Ключей нет.__\nСоздайте: `/newkey метка`"
            lines = ["🔑 **Ключи доступа**", DIV]
            for k in keys:
                exp = _fmt_dt(k.get("expires_at")) if k.get("expires_at") else "бессрочно"
                lines.append(f"`#{k['id']}` · {k.get('label') or '—'}\n   👥 {k.get('member_count', 0)} · ⏳ {exp}")
            return "\n".join(lines)

        # ---- redeem / onboarding -------------------------------------------
        async def grant_access(event, key: dict) -> None:
            sender = await event.get_sender()
            uname = getattr(sender, "username", "") or ""
            name = " ".join(filter(None, [getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or ""])).strip()
            role = key.get("role") or "viewer"
            await upsert_bot_member(event.sender_id, uname, name, key.get("id"), role)
            greeting = f"Привет, {name}!" if name else "Привет!"
            await event.respond(
                "✅ **Доступ открыт!**\n"
                f"{greeting} Добро пожаловать в **Pulse Desk**.\n"
                + f"{DIV}\n"
                + ("👑 Доступ: __полный__" if role == "admin" else "👁 Доступ: __только просмотр__")
                + "\n\nВыберите раздел 👇",
                buttons=main_menu_buttons(role),
            )

        def menu_caption(role: str) -> str:
            badge = "👑 владелец" if role == "admin" else "👁 просмотр"
            return f"🛰 **PULSE DESK** · __{badge}__\nВыберите раздел 👇"

        locked_text = (
            "🔒 **Доступ закрыт**\n"
            f"{DIV}\n"
            "Пришлите ключ доступа, выданный владельцем, "
            "или откройте ссылку-приглашение."
        )

        @bot_client.on(events.NewMessage(pattern=r"/start(?:\s+(\S+))?"))
        async def start_handler(event):
            payload = (event.pattern_match.group(1) or "").strip()
            if payload:
                key = await get_bot_key_by_secret(payload)
                if key:
                    await grant_access(event, key)
                    return
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            role = await bot_role(event.sender_id)
            if role is None:
                await event.respond(locked_text)
                return
            await event.respond(menu_caption(role), buttons=main_menu_buttons(role))

        @bot_client.on(events.NewMessage(pattern=r"/redeem(?:\s+(\S+))?"))
        async def redeem_handler(event):
            payload = (event.pattern_match.group(1) or "").strip()
            if not payload:
                await event.respond("Использование: `/redeem <ключ>`")
                return
            key = await get_bot_key_by_secret(payload)
            if not key:
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            await grant_access(event, key)

        @bot_client.on(events.NewMessage(pattern="/menu"))
        async def menu_handler(event):
            role = await bot_role(event.sender_id)
            if role is None:
                await event.respond(locked_text)
                return
            await event.respond(menu_caption(role), buttons=main_menu_buttons(role))

        @bot_client.on(events.NewMessage(pattern="/help"))
        async def help_handler(event):
            role = await bot_role(event.sender_id)
            if role is None:
                return
            await event.respond(help_text(role))

        @bot_client.on(events.NewMessage(pattern="/stats"))
        async def stats_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond(await render_stats())

        @bot_client.on(events.NewMessage(pattern="/status"))
        async def status_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond(await render_status())

        @bot_client.on(events.NewMessage(pattern="/giveaways"))
        async def giveaways_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond(await render_giveaways(), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/recent"))
        async def recent_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            parts = (event.message.text or "").split(" ", 1)
            try:
                n = max(1, min(20, int(parts[1]))) if len(parts) > 1 else 5
            except (ValueError, IndexError):
                n = 5
            await event.respond(await render_recent(n), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/latest"))
        async def latest_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond(await render_recent(5), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/search"))
        async def search_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            parts = event.message.text.split(" ", 1)
            if len(parts) < 2:
                await event.respond("Укажите текст: `/search TON`")
                return
            rows = await get_pings(limit=5, search=parts[1])
            if not rows:
                await event.respond("🔎 __Ничего не найдено.__")
                return
            result = ["🔎 **Результаты поиска**", DIV]
            for row in rows:
                link = row.get("link") or ""
                result.append(
                    f"• `{_fmt_dt(row.get('detected_at'))}` · {row['chat']}\n{(row.get('text') or '')[:160]}"
                    + (f"\n🔗 {link}" if link else "")
                )
            await event.respond("\n\n".join(result), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/market"))
        async def market_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond(await render_market())

        @bot_client.on(events.NewMessage(pattern="/ping"))
        async def ping_handler(event):
            if await bot_role(event.sender_id) is None:
                return
            await event.respond("🏓 **Понг!** Бот на связи.")

        @bot_client.on(events.NewMessage(pattern="/logs"))
        async def logs_handler(event):
            if await deny_non_admin(event):
                return
            if not LOG_FILE.exists():
                await event.respond("Логов пока нет.")
                return
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            await event.respond("📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-3500:] + "\n```")

        @bot_client.on(events.NewMessage(pattern="/export"))
        async def export_bot_handler(event):
            if await deny_non_admin(event):
                return
            await event.respond("📤 **Экспорт CSV**\n" + DIV + "\nДоступен в веб-интерфейсе: `/api/export-csv`")

        @bot_client.on(events.NewMessage(pattern="/scan"))
        async def scan_handler(event):
            if await deny_non_admin(event):
                return
            if scan_lock.locked():
                await event.respond("⏳ Сканирование уже идёт.")
                return
            asyncio.create_task(full_history_scan())
            await event.respond("🔄 **Сканирование истории запущено.**")

        @bot_client.on(events.NewMessage(pattern=r"/newkey(?:\s+(.+))?"))
        async def newkey_handler(event):
            if await deny_non_admin(event):
                return
            label = (event.pattern_match.group(1) or "").strip()
            secret = generate_access_key()
            await create_bot_key(label, secret, "viewer", None)
            link = f"https://t.me/{bot_username}?start={secret}" if bot_username else ""
            body = (
                "🔑 **Новый ключ создан**\n"
                f"{DIV}\n"
                f"🏷 Метка: `{label or '—'}`\n"
                f"👁 Доступ: __только просмотр__\n\n"
                f"🔐 Ключ:\n`{secret}`\n"
            )
            if link:
                body += f"\n🔗 **Ссылка-приглашение:**\n{link}\n\n_Отправьте её человеку — он откроет бота и получит доступ._"
            else:
                body += "\n_Бот не настроен на ссылки — передайте ключ вручную через_ `/redeem`."
            await event.respond(body, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/keys"))
        async def keys_handler(event):
            if await deny_non_admin(event):
                return
            keys = await list_bot_keys()
            if not keys:
                await event.respond("🔑 **Ключи доступа**\n" + DIV + "\n📭 __Ключей нет.__\nСоздайте: `/newkey метка`")
                return
            await event.respond(f"🔑 **Ключи доступа** · `{len(keys)}`")
            for k in keys:
                exp = _fmt_dt(k.get("expires_at")) if k.get("expires_at") else "бессрочно"
                await event.respond(
                    f"`#{k['id']}` · **{k.get('label') or '—'}**\n👥 {k.get('member_count', 0)}  ·  ⏳ {exp}",
                    buttons=[[Button.inline("🗑 Отозвать", f"revokekey_{k['id']}".encode())]],
                )

        @bot_client.on(events.NewMessage(pattern="/members"))
        async def members_handler(event):
            if await deny_non_admin(event):
                return
            members = await list_bot_members()
            if not members:
                await event.respond("👥 **Пользователи**\n" + DIV + "\n📭 __Пока никого.__")
                return
            await event.respond(f"👥 **Пользователи** · `{len(members)}`")
            for m in members:
                uname = f"@{m['tg_username']}" if m.get("tg_username") else "—"
                badge = "🚫 заблокирован" if m.get("blocked") else "🟢 активен"
                seen = _fmt_dt(m.get("last_seen_at"))
                if m.get("blocked"):
                    btn = Button.inline("✅ Разблокировать", f"unblockmember_{m['tg_id']}".encode())
                else:
                    btn = Button.inline("🚫 Заблокировать", f"blockmember_{m['tg_id']}".encode())
                await event.respond(
                    f"👤 **{m.get('name') or '—'}** ({uname})\n🔑 {m.get('key_label') or '—'}  ·  {badge}\n🕐 {seen}",
                    buttons=[[btn]],
                )

        @bot_client.on(events.NewMessage(func=lambda e: bool(e.is_private and e.message and e.message.text and not e.message.text.startswith("/"))))
        async def freeform_handler(event):
            # Treat a bare message as a possible access key for non-members.
            if await bot_role(event.sender_id) is not None:
                return
            key = await get_bot_key_by_secret((event.message.text or "").strip())
            if key:
                await grant_access(event, key)
                return
            await event.respond(locked_text)

        @bot_client.on(events.CallbackQuery())
        async def callback_handler(event):
            data = event.data.decode("utf-8")
            role = await bot_role(event.sender_id)
            if role is None:
                await event.answer("Доступ запрещён", alert=True)
                return

            # ---- menu navigation (any authenticated role) ----
            if data == "menu_help":
                await safe_edit(event, help_text(role), buttons=main_menu_buttons(role))
                return
            if data == "menu_stats":
                await safe_edit(event, await render_stats(), buttons=main_menu_buttons(role))
                return
            if data == "menu_status":
                await safe_edit(event, await render_status(), buttons=main_menu_buttons(role))
                return
            if data == "menu_giveaways":
                await safe_edit(event, await render_giveaways(), buttons=main_menu_buttons(role), link_preview=False)
                return
            if data == "menu_recent":
                await safe_edit(event, await render_recent(5), buttons=main_menu_buttons(role), link_preview=False)
                return
            if data == "menu_market":
                await safe_edit(event, await render_market(), buttons=main_menu_buttons(role))
                return

            # ---- owner-only menu + actions ----
            admin_prefixes = ("revokekey_", "blockmember_", "unblockmember_", "fav_", "read_", "gconfirm_", "gskip_")
            if data in ("menu_keys", "menu_scan", "menu_logs") or data.startswith(admin_prefixes):
                if role != "admin":
                    await event.answer("Только владелец", alert=True)
                    return

            if data == "menu_keys":
                await safe_edit(event, await render_keys_text(), buttons=main_menu_buttons(role))
                return
            if data == "menu_scan":
                if scan_lock.locked():
                    await event.answer("Скан уже идёт")
                else:
                    asyncio.create_task(full_history_scan())
                    await event.answer("Скан запущен")
                return
            if data == "menu_logs":
                if not LOG_FILE.exists():
                    await event.answer("Логов нет")
                    return
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                await safe_edit(event, "**Последние логи:**\n\n`" + "\n".join(lines)[-3500:] + "`", buttons=main_menu_buttons(role))
                return
            if data.startswith("revokekey_"):
                await revoke_bot_key(int(data.split("_", 1)[1]))
                await event.answer("Ключ отозван")
                await safe_edit(event, await render_keys_text(), buttons=main_menu_buttons(role))
                return
            if data.startswith("blockmember_"):
                await set_bot_member_blocked(int(data.split("_", 1)[1]), True)
                await event.answer("Пользователь заблокирован")
                return
            if data.startswith("unblockmember_"):
                await set_bot_member_blocked(int(data.split("_", 1)[1]), False)
                await event.answer("Пользователь разблокирован")
                return

            # ---- legacy data-mutating actions (owner only, gated above) ----
            if data.startswith("fav_"):
                await toggle_favorite(int(data.split("_", 1)[1]))
                await event.answer("Избранное обновлено")
            elif data.startswith("read_"):
                ping_id = int(data.split("_", 1)[1])
                await mark_ping_read_db(ping_id)
                await event.answer("Отмечено как прочитанное")
                await event.delete()
            elif data.startswith("gconfirm_"):
                ping_id = int(data.split("_", 1)[1])
                try:
                    result = await confirm_safe_giveaway_join(ping_id, actor="telegram_bot")
                    await event.answer(result.get("message") or "Joined")
                except HTTPException as exc:
                    await event.answer(str(exc.detail), alert=True)
            elif data.startswith("gskip_"):
                ping_id = int(data.split("_", 1)[1])
                await update_giveaway_candidate_status(ping_id, "skipped")
                await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
                await record_giveaway_action(ping_id, "skip", "skipped", "telegram_bot")
                await event.answer("Skipped")
                await event.delete()

        # ---- register Telegram command menus (best-effort) ------------------
        try:
            from telethon.tl.functions.bots import SetBotCommandsRequest
            from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer
            viewer_cmds = [
                BotCommand("menu", "Главное меню"),
                BotCommand("stats", "Статистика"),
                BotCommand("status", "Состояние аккаунтов"),
                BotCommand("giveaways", "Розыгрыши"),
                BotCommand("recent", "Последние упоминания"),
                BotCommand("latest", "Последние 5"),
                BotCommand("search", "Поиск"),
                BotCommand("market", "Курсы"),
                BotCommand("ping", "Проверка связи"),
            ]
            await bot_client(SetBotCommandsRequest(scope=BotCommandScopeDefault(), lang_code="", commands=viewer_cmds))
            if ADMIN_ID:
                admin_cmds = viewer_cmds + [
                    BotCommand("scan", "Скан истории"),
                    BotCommand("logs", "Логи"),
                    BotCommand("export", "CSV выгрузка"),
                    BotCommand("newkey", "Создать ключ"),
                    BotCommand("keys", "Ключи доступа"),
                    BotCommand("members", "Пользователи"),
                ]
                await bot_client(SetBotCommandsRequest(
                    scope=BotCommandScopePeer(peer=await bot_client.get_input_entity(int(ADMIN_ID))),
                    lang_code="",
                    commands=admin_cmds,
                ))
        except Exception:
            logger.debug("Could not set bot command menu", exc_info=True)
    except Exception:
        logger.exception("Bot startup failed")


async def fetch_market_data() -> None:
    ids = "tether,the-open-network,bitcoin,ethereum,solana,binancecoin,notcoin,dogs-2"
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": ids, "vs_currencies": "usd,uah", "include_24hr_change": "true"}
    
    while True:
        try:
            # Retry logic for transient network issues
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        resp = await http_client.get(url, params=params)
                        resp.raise_for_status()
                        data = resp.json()
                        data["fetched_at_iso"] = now_iso()
                        await save_market_snapshot(data)
                        break  # Success
                except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 5
                        logger.warning(f"Market fetch timeout (attempt {attempt+1}/3), retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429: # Rate limit
                        wait = 60
                        logger.warning(f"Market fetch rate limited, retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
        except Exception:
            logger.exception("Market data fetch failed after retries")
        
        await asyncio.sleep(MARKET_POLL_SECONDS)


async def monitor_market_volatility() -> None:
    while True:
        try:
            rows = await get_market_history(limit=2)
            if len(rows) >= 2:
                current, previous = rows[0], rows[1]
                for asset in ("bitcoin", "the-open-network", "solana"):
                    curr_price = current.get(asset, {}).get("usd") or 0
                    prev_price = previous.get(asset, {}).get("usd") or 0
                    if prev_price:
                        change = abs((curr_price - prev_price) / prev_price) * 100
                        if change >= MARKET_ALERT_CHANGE_PCT and bot_client and ADMIN_ID:
                            await bot_client.send_message(
                                ADMIN_ID,
                                f"**Резкое движение {asset.upper()}**\nИзменение: `{change:.2f}%`\nЦена: `${curr_price:,.2f}`",
                            )
        except Exception:
            logger.exception("Market volatility monitor failed")
        await asyncio.sleep(3600)


async def reminder_loop() -> None:
    while True:
        try:
            for reminder in await get_due_reminders(limit=25):
                ping_id = reminder.get("ping_id")
                deadline_at = reminder.get("deadline_at")
                chat = reminder.get("chat") or "чат"
                text = (reminder.get("text") or "").strip().replace("\n", " ")
                message = (
                    f"Напоминание Pulse Desk\n\n"
                    f"Дедлайн: {deadline_at or 'не указан'}\n"
                    f"Источник: {chat}\n"
                    f"{text[:300]}"
                )
                if bot_client and ADMIN_ID:
                    buttons = []
                    if reminder.get("link"):
                        buttons.append([Button.url("Открыть в Telegram", reminder["link"])])
                    await bot_client.send_message(ADMIN_ID, message, buttons=buttons, link_preview=False)
                await mark_reminder_sent(int(reminder["id"]))
                await record_app_event("WARNING", "reminder", "Deadline reminder sent", {"ping_id": ping_id, "deadline_at": deadline_at})
                await publish_live_event("reminder", {"ping_id": ping_id, "chat": chat, "deadline_at": deadline_at})
        except Exception:
            logger.exception("Reminder loop failed")
        await asyncio.sleep(60)


async def source_score_loop() -> None:
    while True:
        try:
            await recalculate_source_scores()
            await cleanup_outbox(days=2, max_events=2500)
        except Exception:
            logger.exception("Source score recalculation failed")
        await asyncio.sleep(300)


async def auto_scan_loop() -> None:
    start_supervised("market-volatility", monitor_market_volatility, backoff_base=60.0, backoff_max=1800.0)
    if STARTUP_SCAN_DELAY_SECONDS:
        await asyncio.sleep(STARTUP_SCAN_DELAY_SECONDS)
    waited = 0.0
    while not clients and waited < STARTUP_SCAN_WAIT_SECONDS:
        await asyncio.sleep(1)
        waited += 1
    last_vacuum = datetime.now()
    while True:
        try:
            if clients:
                await full_history_scan()
                state.last_scan_finished_at = datetime.now()
                state.last_scan_status = "ok"
            vacuum_due = (datetime.now() - last_vacuum).total_seconds() >= VACUUM_INTERVAL_HOURS * 3600
            stats = await cleanup_old_data(
                days=MARKET_RETENTION_DAYS,
                pings_retention_days=PINGS_RETENTION_DAYS,
                vacuum=vacuum_due,
            )
            if vacuum_due:
                last_vacuum = datetime.now()
            if stats.get("pings") or stats.get("market_history") or stats.get("vacuumed"):
                await record_app_event("INFO", "maintenance", "Periodic cleanup completed", stats)
        except Exception:
            logger.exception("Automatic scan loop failed")
            state.last_scan_status = "error"
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


async def startup_maintenance() -> None:
    try:
        search_reindex = await rebuild_search_indexes()
        await record_app_event("INFO", "search", "Search indexes rebuilt on startup", search_reindex)
        giveaway_reconcile = await reconcile_giveaway_flags(GIVEAWAY_KEYWORDS)
        if giveaway_reconcile["enabled"] or giveaway_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored giveaways with channel keyword rule", giveaway_reconcile)
        win_reconcile = await reconcile_win_flags(WIN_KEYWORDS)
        if win_reconcile["enabled"] or win_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored win/result flags", win_reconcile)
        outcome_reconcile = await reconcile_giveaway_outcomes()
        if outcome_reconcile["marked"]:
            await record_app_event("INFO", "giveaway", "Marked giveaway result posts as prize claims", outcome_reconcile)
        await cleanup_outbox(days=2, max_events=2500)
        backfilled_deadlines = await backfill_deadlines_from_text()
        if backfilled_deadlines:
            await record_app_event("INFO", "deadline", "Backfilled deadlines from giveaway text", {"count": backfilled_deadlines})
    except Exception as exc:
        logger.exception("Startup maintenance failed")
        await record_app_event("ERROR", "app", "Startup maintenance failed", {"error": str(exc)})


async def _fan_push_ping(ping: dict) -> None:
    if not state.vapid_private_pem:
        return
    subscriptions = await get_push_subscriptions()
    if not subscriptions:
        return
    payload = {
        "title": f"Ping: {ping.get('chat', '?')}",
        "body": (ping.get("text") or "")[:100],
        "url": ping.get("link") or "/",
        "tag": f"ping-{ping.get('id', '')}",
    }
    claims = {"sub": "mailto:push@pulse.local"}
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        await send_push(subscription_info, payload, state.vapid_private_pem, claims)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # VAPID key lifecycle for web push
    try:
        _vapid_pem = await get_setting("vapid_private_pem", "")
        if not _vapid_pem:
            _vapid_keys = generate_vapid_keys()
            await set_setting("vapid_private_pem", _vapid_keys["private_key"])
            _vapid_pem = _vapid_keys["private_key"]
            logger.info("Generated new VAPID key pair for web push")
        state.vapid_private_pem = _vapid_pem
    except Exception:
        logger.warning("Could not initialise VAPID keys — web push will be disabled", exc_info=True)
    interrupted_scans = await interrupt_stale_scan_runs()
    if interrupted_scans:
        await record_app_event(
            "WARNING",
            "scan",
            "Interrupted stale scan runs after application restart",
            {"count": len(interrupted_scans), "ids": [row["id"] for row in interrupted_scans[:10]]},
        )
    apply_tracking_settings(await load_tracking_settings())
    apply_keyword_settings(await load_keyword_settings())
    apply_runtime_settings(await load_runtime_settings())
    if is_weak_token(ADMIN_TOKEN):
        logger.warning("ADMIN_TOKEN looks weak: %s", mask_secret(ADMIN_TOKEN))
        await record_app_event("WARNING", "auth", "ADMIN_TOKEN looks weak", {"token": mask_secret(ADMIN_TOKEN)})
    if is_weak_token(VIEWER_TOKEN):
        logger.warning("VIEWER_TOKEN looks weak: %s", mask_secret(VIEWER_TOKEN))
        await record_app_event("WARNING", "auth", "VIEWER_TOKEN looks weak", {"token": mask_secret(VIEWER_TOKEN)})
    await init_bot()
    start_supervised("market-fetch", fetch_market_data, backoff_base=30.0, backoff_max=1800.0)
    start_supervised("reminders", reminder_loop, backoff_base=10.0, backoff_max=600.0)
    start_supervised("source-scores", source_score_loop, backoff_base=30.0, backoff_max=600.0)
    start_background_task("startup-maintenance", startup_maintenance())
    logger.info("Starting monitoring: %s sessions found", len(SESSION_NAMES))
    await record_app_event("INFO", "app", "Application started", {"sessions": len(SESSION_NAMES), "version": APP_VERSION})
    for name in SESSION_NAMES:
        start_background_task(f"telegram-start:{name}", start_client(name))
    start_supervised("auto-scan", auto_scan_loop, backoff_base=30.0, backoff_max=900.0)
    yield
    state.shutting_down = True
    for task in list(state.background_tasks.values()):
        if not task.done():
            task.cancel()
    if state.background_tasks:
        with suppress(asyncio.CancelledError):
            await asyncio.gather(*list(state.background_tasks.values()), return_exceptions=True)
    for client in list(clients):
        with suppress(Exception):
            await client.disconnect()
    if bot_client:
        with suppress(Exception):
            await bot_client.disconnect()


app = FastAPI(title="Pulse Desk Multi-Account", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from routers import backups as backups_router  # noqa: E402
from routers import boards as boards_router  # noqa: E402
from routers import export as export_router  # noqa: E402
from routers import lookups as lookups_router  # noqa: E402
from routers import market as market_router  # noqa: E402
from routers import pings as pings_router  # noqa: E402
from routers import push as push_router  # noqa: E402

app.include_router(backups_router.router)
app.include_router(boards_router.router)
app.include_router(export_router.router)
app.include_router(lookups_router.router)
app.include_router(market_router.router)
app.include_router(pings_router.router)
app.include_router(push_router.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg")


@app.get("/", response_class=HTMLResponse)
async def read_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    accounts_configured = len(SESSION_NAMES) or len(accounts_state)
    accounts_online = sum(
        1 for acc in list(accounts_state.values()) if acc.get("status") == "online"
    )
    info = runtime_health(
        state,
        accounts_online=accounts_online,
        accounts_configured=accounts_configured,
    )
    try:
        db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    except Exception:
        db_size_bytes = 0
    info.update(
        {
            "status": "ok" if not info.get("missing_background_tasks") and info.get("accounts_ok") else "degraded",
            "time": now_iso(),
            "version": APP_VERSION,
            "db_size_bytes": db_size_bytes,
            "last_scan_finished_at": state.last_scan_finished_at.isoformat(timespec="seconds")
            if state.last_scan_finished_at
            else None,
            "last_scan_status": state.last_scan_status,
            "scan_running": bool(scan_status.get("running")),
            "shutting_down": state.shutting_down,
            "live_subscribers": state.live_hub.subscriber_count(),
        }
    )
    return info


@app.get("/api/session")
async def session(response: Response, x_pulse_token: Optional[str] = Header(default=None), role: str = Depends(get_current_role)):
    if x_pulse_token:
        response.set_cookie("pulse_token", x_pulse_token, httponly=False, samesite="strict", path="/")
    return {"role": role, "public_share_mode": PUBLIC_SHARE_MODE}


@app.get("/api/share-guide", dependencies=[Depends(require_admin)])
async def share_guide():
    base_url = app_base_url()
    return {
        "recommended": "cloudflare_quick_tunnel",
        "public_share_mode": PUBLIC_SHARE_MODE,
        "local_url": base_url,
        "tunnel_command": f"cloudflared tunnel --url {base_url}",
        "viewer_token_configured": bool(VIEWER_TOKEN),
        "admin_token_configured": bool(ADMIN_TOKEN),
        "viewer_token_looks_weak": is_weak_token(VIEWER_TOKEN),
        "admin_token_looks_weak": is_weak_token(ADMIN_TOKEN),
        "viewer_capabilities": ["dashboard", "pings", "market", "analytics"],
        "admin_capabilities": ["accounts", "settings", "scan", "logs", "exports"],
        "never_share": [".env", "*.session", "*.db", "app.log", "ADMIN_TOKEN"],
        "friend_message_template": (
            "Открой ссылку Pulse Desk: {tunnel_url}\n"
            "Когда попросит токен, введи VIEWER_TOKEN, который я отправлю отдельно."
        ),
    }


@app.get("/api/status")
async def status(role: str = Depends(get_current_role)):
    latest_scan = await get_latest_scan_run()
    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "status": "ok",
        "version": APP_VERSION,
        "role": role,
        "public_share_mode": PUBLIC_SHARE_MODE,
        "accounts_online": len(clients),
        "accounts_total": len(accounts_state) or len(SESSION_NAMES),
        "tracked_usernames": ping_usernames,
        "scan": scan_status,
        "last_scan": latest_scan,
        "last_scan_error": scan_status.get("last_error") or (latest_scan or {}).get("last_error"),
        "uptime_seconds": int((datetime.now() - state.started_at).total_seconds()),
        "background_tasks": sorted(state.background_task_names),
        "schema_version": await get_schema_version(),
        "db_size_bytes": db_size_bytes,
        "db_size_mb": round(db_size_bytes / 1024 / 1024, 2),
        "auto_join_giveaways": AUTO_JOIN_GIVEAWAYS,
        "dry_run_giveaways": DRY_RUN_GIVEAWAYS,
        "giveaway_action_account": f"@{GIVEAWAY_ACTION_ACCOUNT}",
        "giveaway_review_mode": GIVEAWAY_REVIEW_MODE,
        "giveaway_min_action_delay_seconds": GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
        "runtime_settings": runtime_settings_payload(),
    }


@app.get("/api/diagnostics", dependencies=[Depends(require_admin)])
async def diagnostics():
    latest_scan = await get_latest_scan_run()
    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    outbox = await get_outbox_stats()
    scan_health = await get_scan_run_health()
    problem_events = await get_recent_problem_events(limit=8)
    runtime = runtime_health(state, accounts_online=len(clients), accounts_configured=len(SESSION_NAMES))
    recommendations: list[str] = []
    if scan_health["running"]:
        recommendations.append("Есть активный scan-run. Если скан давно не движется, перезапустите приложение.")
    if scan_health["recent_interrupted"]:
        recommendations.append("Последний запуск нашел прерванные scan-runs и пометил их interrupted.")
    if outbox.get("pressure") == "high":
        recommendations.append("Live outbox большой: фоновой cleanup должен удерживать последние события, проверьте SSE/refresh если очередь снова растет.")
    if runtime["missing_background_tasks"]:
        recommendations.append("Некоторые фоновые задачи не активны: проверьте логи и перезапустите приложение.")
    if not recommendations:
        recommendations.append("Критичных проблем диагностика не видит.")
    return {
        "status": "ok",
        "version": APP_VERSION,
        "schema_version": await get_schema_version(),
        "db": {
            "path": str(DB_PATH),
            "size_bytes": db_size_bytes,
            "size_mb": round(db_size_bytes / 1024 / 1024, 2),
            "stats": await get_db_stats(),
            "backup_count": len(list_db_backups(limit=500)),
        },
        "live": {
            "outbox": outbox,
            "sse_connected_hint": "EventSource uses the pulse_token cookie after /api/session.",
        },
        "runtime": runtime,
        "scan": {
            "current": scan_status,
            "latest": latest_scan,
            "health": scan_health,
            "background_tasks": sorted(state.background_task_names),
        },
        "recent_problem_events": problem_events,
        "recommendations": recommendations,
        "accounts": {
            "online": len(clients),
            "configured": len(SESSION_NAMES),
            "known_state": accounts_state,
        },
        "security": {
            "public_share_mode": PUBLIC_SHARE_MODE,
            "admin_token_configured": bool(ADMIN_TOKEN),
            "viewer_token_configured": bool(VIEWER_TOKEN),
            "admin_token_looks_weak": is_weak_token(ADMIN_TOKEN),
            "viewer_token_looks_weak": is_weak_token(VIEWER_TOKEN),
            "query_token_enabled": ALLOW_QUERY_TOKEN,
        },
        "giveaways": {
            "auto_join": AUTO_JOIN_GIVEAWAYS,
            "dry_run": DRY_RUN_GIVEAWAYS,
            "review_mode": GIVEAWAY_REVIEW_MODE,
            "action_account": f"@{GIVEAWAY_ACTION_ACCOUNT}",
            "min_action_delay_seconds": GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
            "strict_rule": "chat_type == channel and text contains one configured giveaway keyword",
        },
        "runtime_settings": runtime_settings_payload(),
    }


@app.post("/api/auth/send-code", dependencies=[Depends(require_admin)])
async def send_code(data: AuthRequest):
    if not API_ID or not API_HASH:
        raise HTTPException(500, "Telegram API credentials are missing.")
    phone = data.phone.strip()
    session_name = normalize_auth_session_name(data.session_name, phone)
    await disconnect_account(session_name)
    existing = pending_auths.pop(phone, None)
    if existing:
        try:
            await existing["client"].disconnect()
        except Exception:
            logger.debug("Failed to close previous pending auth client", exc_info=True)
    account = accounts_state.setdefault(session_name, {"session_name": session_name})
    account.update({"status": "auth_requesting", "last_error": None, "manual_disconnect": False})
    client = telegram_client_for_session(session_name)
    try:
        await client.connect()
        result = await client.send_code_request(phone, force_sms=data.force_sms)
        delivery_type, delivery_message = describe_sent_code_type(result)
        pending_auths[phone] = {"client": client, "phone_code_hash": result.phone_code_hash, "session_name": session_name, "created_at": datetime.now()}
        account.update({
            "status": "auth_code_sent",
            "auth_delivery_type": delivery_type,
            "last_error": None,
            "auth_requested_at": now_iso(),
        })
        await record_app_event(
            "INFO",
            "auth",
            "Telegram auth code requested",
            {"session_name": session_name, "delivery_type": delivery_type, "force_sms": data.force_sms},
        )
        return {
            "status": "ok",
            "message": f"{delivery_message} Сессия: {session_name}.",
            "session_name": session_name,
            "delivery_type": delivery_type,
        }
    except FloodWaitError as exc:
        await client.disconnect()
        pending_auths.pop(phone, None)
        message = f"Telegram ограничил повторные запросы кода. Подождите {exc.seconds} секунд."
        account.update({"status": "rate_limited", "last_error": message})
        await record_app_event("WARNING", "auth", "Telegram auth code flood wait", {"session_name": session_name, "seconds": exc.seconds})
        return {"status": "error", "message": message}
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            pending_auths.pop(phone, None)
            await mark_auth_key_duplicated(session_name, client, exc)
            return {"status": "error", "message": auth_key_duplicated_message(session_name)}
        await client.disconnect()
        pending_auths.pop(phone, None)
        account.update({"status": "auth_error", "last_error": str(exc)})
        logger.exception("Auth send-code failed")
        return {"status": "error", "message": str(exc)}


@app.post("/api/auth/sign-in", dependencies=[Depends(require_admin)])
async def sign_in(data: SignInRequest, background_tasks: BackgroundTasks):
    phone = data.phone.strip()
    code = data.code.strip()
    if not code:
        return {
            "status": "error",
            "message": "Введите код из Telegram. Пустой код не отправлен, чтобы не сжигать повторные попытки Telegram.",
        }
    auth_data = pending_auths.get(phone)
    if not auth_data:
        raise HTTPException(400, "Нет активной авторизации для этого телефона.")
    if is_pending_auth_expired(auth_data):
        pending_auths.pop(phone, None)
        try:
            await auth_data["client"].disconnect()
        except Exception:
            logger.debug("Failed to close expired auth client", exc_info=True)
        raise HTTPException(400, "Код авторизации устарел. Запросите новый код.")
    client: TelegramClient = auth_data["client"]
    try:
        try:
            await client.sign_in(phone, code, phone_code_hash=auth_data["phone_code_hash"])
        except SessionPasswordNeededError:
            if not data.password:
                return {"status": "password_needed", "message": "Нужен пароль 2FA"}
            await client.sign_in(password=data.password)
        except PhoneCodeInvalidError:
            return {"status": "error", "message": "Код неверный. Проверьте служебный чат Telegram и введите код заново."}
        except PhoneCodeExpiredError:
            pending_auths.pop(phone, None)
            try:
                await client.disconnect()
            except Exception:
                logger.debug("Failed to close expired auth client", exc_info=True)
            return {"status": "error", "message": "Код устарел. Запросите новый код позже, когда Telegram снимет лимит."}
        except PhoneCodeEmptyError:
            return {"status": "error", "message": "Введите код из Telegram перед входом."}
        except SendCodeUnavailableError:
            return {
                "status": "error",
                "message": "Telegram исчерпал варианты повторной отправки кода для этого номера. Не нажимайте вход без кода; подождите и попробуйте позже.",
            }
        me = await client.get_me()
        session_name = auth_data["session_name"]
        pending_auths.pop(phone, None)
        accounts_state.setdefault(session_name, {"session_name": session_name}).update({
            "status": "connecting",
            "last_error": None,
            "username": me.username,
            "user_id": me.id,
        })
        await client.disconnect()
        background_tasks.add_task(start_client, session_name)
        return {"status": "ok", "user": me.username or str(me.id), "session_name": session_name}
    except Exception as exc:
        session_name = str(auth_data.get("session_name") or "")
        pending_auths.pop(phone, None)
        if session_name and is_auth_key_duplicated(exc):
            await mark_auth_key_duplicated(session_name, client, exc)
            return {"status": "error", "message": auth_key_duplicated_message(session_name)}
        try:
            await client.disconnect()
        except Exception:
            logger.debug("Failed to close auth client", exc_info=True)
        logger.exception("Auth sign-in failed")
        return {"status": "error", "message": str(exc)}


def channel_account_stats() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_name, account in sorted(list(accounts_state.items())):
        rows.append({
            "session_name": session_name,
            "display": account.get("display") or account.get("username") or session_name,
            "status": account.get("status") or "unknown",
            "channels": int(account.get("channels_total") or 0),
            "last_channel_scan_at": account.get("last_channel_scan_at"),
        })
    return rows


async def build_analytics() -> dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        total = (await (await db.execute("SELECT COUNT(*) AS total FROM pings")).fetchone())["total"]
        new = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'new'")).fetchone())["count"]
        favorites = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_favorite = 1")).fetchone())["count"]
        important = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE priority_score >= 60 OR status = 'important'")).fetchone())["count"]
        resolved = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'resolved'")).fetchone())["count"]
        wins = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_win = 1")).fetchone())["count"]
        giveaways = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE is_giveaway = 1")).fetchone())["count"]
        noise = (await (await db.execute("SELECT COUNT(*) AS count FROM pings WHERE status = 'ignored' OR giveaway_status = 'scam' OR action_status = 'scam'")).fetchone())["count"]
        last_24h = (await (await db.execute(
            "SELECT COUNT(*) AS count FROM pings WHERE datetime(detected_at) >= datetime('now', '-1 day')"
        )).fetchone())["count"]
        last_7d = (await (await db.execute(
            "SELECT COUNT(*) AS count FROM pings WHERE datetime(detected_at) >= datetime('now', '-7 day')"
        )).fetchone())["count"]
        priority_row = await (await db.execute(
            "SELECT COALESCE(AVG(priority_score), 0) AS avg_priority, MIN(detected_at) AS first_seen, MAX(detected_at) AS last_seen FROM pings"
        )).fetchone()
        channel_chats_total = (await (await db.execute(
            "SELECT COUNT(DISTINCT chat_id) AS count FROM pings WHERE chat_type = 'channel' AND chat_id IS NOT NULL"
        )).fetchone())["count"]
        rows = await (await db.execute("SELECT chat_type, COUNT(*) AS count FROM pings GROUP BY chat_type")).fetchall()
        by_type = {row["chat_type"] or "unknown": row["count"] for row in rows}
        statuses = {row["status"] or "unknown": row["count"] for row in await (await db.execute(
            "SELECT status, COUNT(*) AS count FROM pings GROUP BY status"
        )).fetchall()}
        daily = [dict(row) for row in await (await db.execute(
            "SELECT date(detected_at) AS day, COUNT(*) AS count FROM pings GROUP BY day ORDER BY day DESC LIMIT 7"
        )).fetchall()]
        hourly = {row["hour"]: row["count"] for row in await (await db.execute(
            "SELECT strftime('%H', detected_at) AS hour, COUNT(*) AS count FROM pings GROUP BY hour ORDER BY hour ASC"
        )).fetchall()}
        top_chats = [dict(row) for row in await (await db.execute(
            "SELECT chat, COUNT(*) AS count FROM pings GROUP BY chat ORDER BY count DESC LIMIT 10"
        )).fetchall()]
        channels_by_account = channel_account_stats()
        channel_memberships_total = sum(int(row.get("channels") or 0) for row in channels_by_account)
        return {
            "total_pings": total,
            "new_pings": new,
            "favorites": favorites,
            "important": important,
            "resolved": resolved,
            "wins": wins,
            "giveaways": giveaways,
            "noise": noise,
            "last_24h": last_24h,
            "last_7d": last_7d,
            "avg_priority": round(float(priority_row["avg_priority"] or 0), 2),
            "first_seen": priority_row["first_seen"],
            "last_seen": priority_row["last_seen"],
            "unread_rate": round((new / total * 100), 2) if total else 0,
            "win_rate": round((wins / total * 100), 2) if total else 0,
            "giveaway_rate": round((giveaways / total * 100), 2) if total else 0,
            "channel_chats_total": channel_chats_total,
            "channel_memberships_total": channel_memberships_total,
            "total_channels": channel_memberships_total or channel_chats_total,
            "channels_by_account": channels_by_account,
            "by_type": by_type,
            "statuses": statuses,
            "daily": daily,
            "hourly": hourly,
            "top_chats": top_chats,
            "accounts_online": len(clients),
        }


@app.get("/api/analytics")
async def get_analytics(role: str = Depends(get_current_role)):
    return await build_analytics()


_dashboard_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_dashboard_cache_ttl_seconds: float = 5.0
_dashboard_cache_lock = asyncio.Lock()


def invalidate_dashboard_cache() -> None:
    _dashboard_cache.clear()


# Now that the cache is defined, wire the live-event hook to clear it.
register_dashboard_invalidator(invalidate_dashboard_cache)


@app.get("/api/dashboard/summary")
async def get_dashboard_summary(role: str = Depends(get_current_role)):
    import time

    cache_key = role
    now = time.monotonic()
    cached = _dashboard_cache.get(cache_key)
    if cached and now - cached[0] < _dashboard_cache_ttl_seconds:
        return cached[1]
    async with _dashboard_cache_lock:
        cached = _dashboard_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _dashboard_cache_ttl_seconds:
            return cached[1]
        status_payload = await status(role)
        analytics = await build_analytics()
        tasks = await get_task_overview(limit=120)
        giveaway_board = await get_giveaway_board(limit=120)
        problem_events = await get_recent_problem_events(limit=5) if role == "admin" else []
        result = build_dashboard_summary(
            status=status_payload,
            analytics=analytics,
            tasks=tasks,
            giveaway_board=giveaway_board,
            problem_events=problem_events,
        )
        _dashboard_cache[cache_key] = (time.monotonic(), result)
        return result


@app.get("/api/analytics/detailed")
async def get_detailed_analytics(role: str = Depends(get_current_role)):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA busy_timeout=5000")
        conv = await (await db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(auto_joined), 0) AS joined FROM pings WHERE is_giveaway = 1"
        )).fetchone()
        heatmap = [dict(row) for row in await (await db.execute(
            "SELECT strftime('%H', detected_at) AS hour, chat_type, COUNT(*) AS count FROM pings GROUP BY hour, chat_type"
        )).fetchall()]
        senders = [dict(row) for row in await (await db.execute(
            """
            SELECT sender, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins
            FROM pings
            GROUP BY sender
            ORDER BY wins DESC, count DESC
            LIMIT 10
            """
        )).fetchall()]
        chats = [dict(row) for row in await (await db.execute(
            """
            SELECT chat, COUNT(*) AS count, COALESCE(SUM(is_win), 0) AS wins,
                   COALESCE(SUM(is_giveaway), 0) AS giveaways,
                   COALESCE(AVG(priority_score), 0) AS avg_priority
            FROM pings
            GROUP BY chat
            ORDER BY avg_priority DESC, wins DESC, count DESC
            LIMIT 12
            """
        )).fetchall()]
        priorities = [dict(row) for row in await (await db.execute(
            "SELECT priority_label, COUNT(*) AS count FROM pings GROUP BY priority_label ORDER BY count DESC"
        )).fetchall()]
        deadlines = [dict(row) for row in await (await db.execute(
            """
            SELECT
                COALESCE(deadline_source, '') AS deadline_source,
                COUNT(*) AS count
            FROM pings
            WHERE is_giveaway = 1
            GROUP BY deadline_source
            """
        )).fetchall()]
        daily_quality = [dict(row) for row in await (await db.execute(
            """
            SELECT
                date(detected_at) AS day,
                COUNT(*) AS total,
                COALESCE(SUM(is_win), 0) AS wins,
                COALESCE(SUM(is_giveaway), 0) AS giveaways,
                COALESCE(SUM(CASE WHEN priority_score >= 60 OR status = 'important' THEN 1 ELSE 0 END), 0) AS important,
                COALESCE(SUM(CASE WHEN status = 'resolved' OR action_status IN ('claimed', 'closed') THEN 1 ELSE 0 END), 0) AS resolved
            FROM pings
            GROUP BY day
            ORDER BY day DESC
            LIMIT 14
            """
        )).fetchall()]
        top_mentions = [dict(row) for row in await (await db.execute(
            """
            SELECT username, COUNT(*) AS count
            FROM ping_mentions
            GROUP BY username
            ORDER BY count DESC, username ASC
            LIMIT 12
            """
        )).fetchall()]
        status_flow = [dict(row) for row in await (await db.execute(
            """
            SELECT
                COALESCE(status, 'unknown') AS status,
                COALESCE(action_status, 'new') AS action_status,
                COUNT(*) AS count
            FROM pings
            GROUP BY status, action_status
            ORDER BY count DESC
            LIMIT 16
            """
        )).fetchall()]
        sources = await get_source_scores(limit=8)
        channels_by_account = channel_account_stats()
        return {
            "conversion": dict(conv),
            "heatmap": heatmap,
            "senders": senders,
            "chats": chats,
            "priorities": priorities,
            "deadlines": deadlines,
            "daily_quality": daily_quality,
            "top_mentions": top_mentions,
            "status_flow": status_flow,
            "sources": sources,
            "channels_by_account": channels_by_account,
            "channel_memberships_total": sum(int(row.get("channels") or 0) for row in channels_by_account),
        }


@app.get("/api/stats/detailed")
async def get_stats_detailed_alias(role: str = Depends(get_current_role)):
    return await get_detailed_analytics()


@app.post("/api/giveaways/{ping_id}/analyze", dependencies=[Depends(require_admin)])
async def analyze_giveaway_api(ping_id: int):
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise HTTPException(404, "Ping not found")
    client = await find_giveaway_action_client()
    if not client:
        raise HTTPException(400, f"Action account @{GIVEAWAY_ACTION_ACCOUNT} is not online")
    message = None
    try:
        message = await load_giveaway_message(client, ping)
    except HTTPException:
        message = None
    candidate = await analyze_and_store_giveaway(client, ping_id, ping, message)
    if not candidate:
        raise HTTPException(500, "Giveaway analysis failed")
    return {"status": "ok", "candidate": candidate}


@app.post("/api/giveaways/{ping_id}/refresh-deadline", dependencies=[Depends(require_admin)])
async def refresh_giveaway_deadline_api(ping_id: int):
    if not clients:
        raise HTTPException(400, "No connected Telegram accounts")
    return await refresh_ping_deadline(clients[0], ping_id)


@app.post("/api/giveaways/{ping_id}/confirm", dependencies=[Depends(require_admin)])
async def confirm_giveaway_api(ping_id: int):
    return await confirm_safe_giveaway_join(ping_id, actor="admin")


@app.post("/api/giveaways/{ping_id}/skip", dependencies=[Depends(require_admin)])
async def skip_giveaway_api(ping_id: int):
    if not await get_giveaway_candidate(ping_id):
        raise HTTPException(404, "Giveaway candidate not found")
    await update_giveaway_candidate_status(ping_id, "skipped")
    await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
    await record_giveaway_action(ping_id, "skip", "skipped", "admin")
    await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "skipped"})
    return {"status": "skipped"}


@app.get("/api/giveaways/cleanup-candidates")
async def read_giveaway_cleanup_candidates(role: str = Depends(get_current_role), limit: int = Query(100, ge=1, le=500)):
    client = await find_giveaway_action_client()
    if not client:
        return {"action_account": f"@{GIVEAWAY_ACTION_ACCOUNT}", "candidates": [], "warning": "Action account is not online"}
    candidates = []
    async for dialog in client.iter_dialogs(limit=limit):
        item = inactive_channel_candidate(dialog, GIVEAWAY_INACTIVE_CHANNEL_DAYS)
        if item:
            candidates.append(item)
    return {"action_account": f"@{GIVEAWAY_ACTION_ACCOUNT}", "inactive_days": GIVEAWAY_INACTIVE_CHANNEL_DAYS, "candidates": candidates}


@app.post("/api/giveaways/cleanup-candidates/{chat_id}/leave", dependencies=[Depends(require_admin)])
async def leave_inactive_channel_api(chat_id: int):
    client = await find_giveaway_action_client()
    if not client:
        raise HTTPException(400, f"Action account @{GIVEAWAY_ACTION_ACCOUNT} is not online")
    try:
        entity = await client.get_entity(chat_id)
        await client(LeaveChannelRequest(entity))
        await record_giveaway_action(None, "leave_channel", "left", "admin", f"Left channel {chat_id}", {"chat_id": chat_id})
        await record_app_event("WARNING", "giveaway", "Left inactive channel after admin confirmation", {"chat_id": chat_id})
        return {"status": "left", "chat_id": chat_id}
    except FloodWaitError as exc:
        await record_giveaway_action(None, "leave_channel", "manual_required", "admin", f"FloodWait {exc.seconds}s", {"chat_id": chat_id})
        raise HTTPException(429, f"Telegram FloodWait {exc.seconds}s") from exc
    except Exception as exc:
        await record_giveaway_action(None, "leave_channel", "failed", "admin", str(exc), {"chat_id": chat_id})
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/channels/{chat_id}/refresh-profile", dependencies=[Depends(require_admin)])
async def refresh_channel_profile_api(chat_id: int):
    if not clients:
        raise HTTPException(400, "No connected Telegram accounts")
    profile = await refresh_channel_profile(clients[0], chat_id, force=True)
    await record_app_event("INFO", "deadline", "Channel profile refreshed manually", {"chat_id": chat_id})
    await publish_live_event("channel-profile", {"chat_id": chat_id, "deadline_at": profile.get("deadline_at")})
    return {"status": "ok", "profile": profile}


@app.get("/api/settings/rules-ui", dependencies=[Depends(require_admin)])
async def get_rules_ui():
    saved = await get_setting("rules_ui", None)
    if saved is None:
        notifications = await load_notification_settings()
        saved = {
            "enabled": notifications.get("enabled", True),
            "quiet_hours": notifications.get("quiet_hours", {"enabled": False, "from": "23:00", "to": "08:00"}),
            "rules": notifications.get("rules", []),
        }
    return saved


@app.put("/api/settings/rules-ui", dependencies=[Depends(require_admin)])
async def update_rules_ui(data: RulesUiRequest):
    payload = {
        "enabled": data.enabled,
        "quiet_hours": data.quiet_hours,
        "rules": data.rules[:50],
    }
    await set_setting("rules_ui", payload)
    notifications = await load_notification_settings()
    notifications["enabled"] = data.enabled
    notifications["quiet_hours"] = data.quiet_hours
    notifications["rules"] = data.rules[:50]
    await set_setting("notifications", notifications)
    await record_app_event("INFO", "settings", "Visual notification rules updated", {"rules": len(data.rules)})
    return payload


@app.get("/api/live")
async def live_events(request: Request, role: str = Depends(get_current_role)):
    """Server-Sent Events: in-memory pub/sub with durable backfill from outbox.

    On connect:
      1. If client sent ?last_id=N, drain outbox for events with id > N (catches
         up missed events while disconnected).
      2. Subscribe to the live hub; new events are pushed without polling.

    Keepalives are sent every 20 s of inactivity so proxies don't close the
    connection.
    """
    last_id = int(request.query_params.get("last_id", "0") or 0)
    subscriber = await state.live_hub.subscribe()

    async def stream():
        nonlocal last_id
        try:
            # Backfill missed events from durable storage
            if last_id > 0:
                try:
                    backfill = await get_outbox_after(last_id, limit=200)
                    for row in backfill:
                        last_id = int(row["id"])
                        data = json.dumps(row["payload"], ensure_ascii=False)
                        yield f"id: {last_id}\nevent: {row['event_type']}\ndata: {data}\n\n"
                except Exception:
                    logger.debug("Outbox backfill failed", exc_info=True)
            yield ": connected\n\n"
            # Live phase: await pushes via the queue with periodic keepalive.
            keepalive_after = 20.0  # seconds
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=keepalive_after)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_id = event.get("id")
                if event_id is not None:
                    last_id = max(last_id, int(event_id))
                payload = event.get("payload") or {}
                if subscriber._lagged:
                    payload = dict(payload)
                    payload["_lagged"] = True
                    subscriber._lagged = False
                data = json.dumps(payload, ensure_ascii=False)
                etype = event.get("event_type") or "message"
                prefix = f"id: {event_id}\n" if event_id is not None else ""
                yield f"{prefix}event: {etype}\ndata: {data}\n\n"
        finally:
            await state.live_hub.unsubscribe(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/export-csv-legacy", include_in_schema=False, dependencies=[Depends(require_admin)])
async def _export_csv_legacy_redirect():
    return {"removed": True, "note": "use /api/export-csv"}


async def _legacy_unused_csv_DELETED():
    return None


@app.post("/api/scan-history", dependencies=[Depends(require_admin)])
async def start_scan(background_tasks: BackgroundTasks):
    if not clients:
        return {"status": "error", "message": "Нет подключенных аккаунтов"}
    if scan_lock.locked():
        return {"status": "running", "message": "Сканирование уже идет"}
    background_tasks.add_task(full_history_scan)
    return {"status": "ok", "message": "Сканирование запущено"}


@app.post("/api/backfill-mentions", dependencies=[Depends(require_admin)])
async def start_mention_backfill(background_tasks: BackgroundTasks, limit: int = 1000):
    if not clients:
        return {"status": "error", "message": "Нет подключенных аккаунтов"}
    if scan_lock.locked():
        return {"status": "running", "message": "Сканирование уже идет"}
    per_channel_limit = normalize_scan_history_limit(limit)
    background_tasks.add_task(backfill_name_mention_scan, per_channel_limit)
    scope = "вся история" if per_channel_limit <= 0 else f"до {per_channel_limit} сообщений на канал"
    return {"status": "ok", "message": f"Backfill упоминаний по имени запущен ({scope})"}


@app.post("/api/scan-history/cancel", dependencies=[Depends(require_admin)])
async def cancel_scan():
    if not scan_lock.locked():
        return {"status": "idle", "message": "Сканирование не идет"}
    scan_status["cancel_requested"] = True
    scan_cancel_event.set()
    if scan_status.get("scan_run_id"):
        await update_scan_run(int(scan_status["scan_run_id"]), cancel_requested=1)
    await record_app_event("WARNING", "scan", "Scan cancellation requested", {"scan_run_id": scan_status.get("scan_run_id")})
    return {"status": "ok", "message": "Остановка сканирования запрошена"}


@app.get("/api/scan-status")
async def get_scan_status(role: str = Depends(get_current_role)):
    return dict(scan_status)


def channel_checkpoint_key(username: str, chat_id: Any) -> str:
    return f"{username}|channel:{chat_id}"


async def list_broadcast_channel_dialogs(client: TelegramClient) -> list[Any]:
    dialogs: list[Any] = []
    async for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if chat_type_from_entity(entity) == "channel":
            dialogs.append(dialog)
    return dialogs


async def load_channel_username_checkpoints(session_name: str, chat_id: Any) -> dict[str, int]:
    checkpoint_keys = {username: channel_checkpoint_key(username, chat_id) for username in ping_usernames}
    saved = await get_checkpoints(session_name, list(checkpoint_keys.values()))
    missing_keys = [key for key in checkpoint_keys.values() if not saved.get(key)]
    seeded = await get_latest_checkpoints(missing_keys) if missing_keys else {}
    return {username: int(saved.get(key) or seeded.get(key) or 0) for username, key in checkpoint_keys.items()}


async def save_channel_username_checkpoints(
    session_name: str,
    chat_id: Any,
    checkpoint_by_username: dict[str, int],
    last_message_id: int,
) -> None:
    if last_message_id <= 0:
        return
    updates = {
        channel_checkpoint_key(username, chat_id): int(last_message_id)
        for username, current_id in checkpoint_by_username.items()
        if int(last_message_id) > int(current_id or 0)
    }
    await save_checkpoints(session_name, updates)


async def scan_single_account(client: TelegramClient, limit: Optional[int] = None) -> int:
    session_name = getattr(client, "_session_name_custom", "unknown")
    found = 0
    history_limit = SCAN_HISTORY_LIMIT if limit is None else normalize_scan_history_limit(limit)
    iter_limit = None if history_limit <= 0 else history_limit

    async def mark_processed_units(units: int = 1) -> None:
        scan_status["processed_usernames"] += units
        if scan_status.get("scan_run_id"):
            await update_scan_run(
                int(scan_status["scan_run_id"]),
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
            )

    async def scan_recent_channel_window(entity: Any, chat_id: Any, user_label: str) -> int:
        if EDIT_SCAN_RECENT_MESSAGES <= 0:
            return 0
        recent_found = 0
        scanned_messages = 0
        try:
            async for message in client.iter_messages(entity, limit=EDIT_SCAN_RECENT_MESSAGES):
                if scan_cancel_event.is_set():
                    break
                scanned_messages += 1
                if await get_ping_by_message_ref(chat_id, getattr(message, "id", None)):
                    continue
                ping_id = await process_ping_message(
                    client,
                    message,
                    account_label=user_label,
                    notify=True,
                    source="recent-edit-sweep",
                )
                if ping_id:
                    recent_found += 1
        except FloodWaitError as exc:
            logger.warning("Flood wait during recent edit sweep in %s: %s seconds", chat_id, exc.seconds)
            scan_status["last_error"] = f"Flood wait {exc.seconds}s in recent edit sweep"
            await record_app_event("WARNING", "scan", "Telegram flood wait during recent edit sweep", {"chat_id": chat_id, "seconds": exc.seconds})
            wait = flood_wait_seconds(exc.seconds)
            mark_account_cooldown(session_name, wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            if is_auth_key_duplicated(exc):
                scan_status["last_error"] = auth_key_duplicated_message(session_name)
                scan_cancel_event.set()
                await mark_auth_key_duplicated(session_name, client, exc)
                return recent_found
            scan_status["last_error"] = str(exc)
            await record_app_event("WARNING", "scan", "Recent edit sweep failed", {"chat_id": chat_id, "error": str(exc)})
            logger.warning("Recent edit sweep failed for %s: %s", chat_id, exc)
        finally:
            scan_status["edit_sweep_messages"] = int(scan_status.get("edit_sweep_messages") or 0) + scanned_messages
        return recent_found

    try:
        me = await client.get_me()
        user_label = me.username or str(me.id)
        scan_status["current_account"] = user_label
        dialogs = await list_broadcast_channel_dialogs(client)
        account_state = accounts_state.setdefault(session_name, {"session_name": session_name})
        account_state.update({
            "channels_total": len(dialogs),
            "last_channel_scan_at": now_iso(),
        })
        scan_status["total_channels"] = int(scan_status.get("total_channels") or 0) + len(dialogs)
        expected_units = len(dialogs) * len(ping_usernames)
        scan_status["total_usernames"] = int(scan_status.get("total_usernames") or 0) + expected_units
        if scan_status.get("scan_run_id"):
            await update_scan_run(
                int(scan_status["scan_run_id"]),
                total_usernames=scan_status["total_usernames"],
                last_error=scan_status.get("last_error"),
            )
        for dialog in dialogs:
            if scan_cancel_event.is_set():
                break
            entity = dialog.entity
            chat_id = getattr(entity, "id", None)
            if chat_id is None:
                continue
            latest_message_id = int(getattr(getattr(dialog, "message", None), "id", 0) or 0)
            scan_status["current_channel"] = getattr(dialog, "name", None) or str(chat_id)
            checkpoint_by_username = await load_channel_username_checkpoints(session_name, chat_id)
            sweep_start_id = channel_sweep_start_id(checkpoint_by_username)
            if sweep_start_id is not None:
                scan_status["fast_channels"] = int(scan_status.get("fast_channels") or 0) + 1
                scan_status["current_username"] = "all"
                new_last_id = max(int(sweep_start_id or 0), latest_message_id)
                try:
                    async for message in client.iter_messages(entity, min_id=sweep_start_id, limit=iter_limit):
                        if scan_cancel_event.is_set():
                            break
                        new_last_id = max(new_last_id, int(getattr(message, "id", 0) or 0))
                        ping_id = await process_ping_message(client, message, account_label=user_label, notify=True)
                        if ping_id:
                            found += 1
                            scan_status["found"] += 1
                    if not scan_cancel_event.is_set():
                        await save_channel_username_checkpoints(session_name, chat_id, checkpoint_by_username, new_last_id)
                except FloodWaitError as exc:
                    logger.warning("Flood wait during fast channel sweep in %s: %s seconds", chat_id, exc.seconds)
                    scan_status["last_error"] = f"Flood wait {exc.seconds}s in fast channel sweep"
                    await record_app_event("WARNING", "scan", "Telegram flood wait during fast channel sweep", {"chat_id": chat_id, "seconds": exc.seconds})
                    wait = flood_wait_seconds(exc.seconds)
                    mark_account_cooldown(session_name, wait)
                    await asyncio.sleep(wait)
                except Exception as exc:
                    if is_auth_key_duplicated(exc):
                        scan_status["last_error"] = auth_key_duplicated_message(session_name)
                        scan_cancel_event.set()
                        await mark_auth_key_duplicated(session_name, client, exc)
                        break
                    scan_status["last_error"] = str(exc)
                    await record_app_event("WARNING", "scan", "Fast channel sweep failed", {"session": session_name, "chat_id": chat_id, "error": str(exc)})
                    logger.warning("Fast channel sweep failed for %s in %s: %s", chat_id, session_name, exc)
                finally:
                    await mark_processed_units(len(ping_usernames))
                recent_found = await scan_recent_channel_window(entity, chat_id, user_label)
                if recent_found:
                    found += recent_found
                    scan_status["found"] += recent_found
                continue

            for username in ping_usernames:
                if scan_cancel_event.is_set():
                    break
                checkpoint_key = channel_checkpoint_key(username, chat_id)
                last_id = checkpoint_by_username.get(username, 0)
                scan_status["targeted_channels"] = int(scan_status.get("targeted_channels") or 0) + 1
                scan_status["current_username"] = username
                new_last_id = max(int(last_id or 0), latest_message_id)
                try:
                    async for message in client.iter_messages(entity, search=f"@{username}", min_id=last_id, limit=iter_limit):
                        if scan_cancel_event.is_set():
                            break
                        new_last_id = max(new_last_id, int(getattr(message, "id", 0) or 0))
                        ping_id = await process_ping_message(client, message, account_label=user_label, notify=True)
                        if ping_id:
                            found += 1
                            scan_status["found"] += 1
                    if not scan_cancel_event.is_set() and new_last_id > last_id:
                        await save_checkpoint(session_name, checkpoint_key, new_last_id)
                        checkpoint_by_username[username] = new_last_id
                except FloodWaitError as exc:
                    logger.warning("Flood wait during targeted scan in %s for @%s: %s seconds", chat_id, username, exc.seconds)
                    scan_status["last_error"] = f"Flood wait {exc.seconds}s for @{username}"
                    await record_app_event("WARNING", "scan", "Telegram flood wait during targeted scan", {"chat_id": chat_id, "username": username, "seconds": exc.seconds})
                    wait = flood_wait_seconds(exc.seconds)
                    mark_account_cooldown(session_name, wait)
                    await asyncio.sleep(wait)
                except Exception as exc:
                    if is_auth_key_duplicated(exc):
                        scan_status["last_error"] = auth_key_duplicated_message(session_name)
                        scan_cancel_event.set()
                        await mark_auth_key_duplicated(session_name, client, exc)
                        break
                    scan_status["last_error"] = str(exc)
                    await record_app_event("WARNING", "scan", "Targeted username scan failed", {"session": session_name, "chat_id": chat_id, "username": username, "error": str(exc)})
                    logger.warning("Targeted username scan failed for %s/%s in %s: %s", chat_id, username, session_name, exc)
                finally:
                    await mark_processed_units()
            if scan_cancel_event.is_set():
                break
            recent_found = await scan_recent_channel_window(entity, chat_id, user_label)
            if recent_found:
                found += recent_found
                scan_status["found"] += recent_found
        logger.info("Channel scan finished for %s, found %s", user_label, found)
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            scan_status["last_error"] = auth_key_duplicated_message(session_name)
            scan_cancel_event.set()
            await mark_auth_key_duplicated(session_name, client, exc)
            return found
        scan_status["last_error"] = str(exc)
        await record_app_event("ERROR", "scan", "Account scan failed", {"session": session_name, "error": str(exc)})
        logger.exception("Account scan failed: %s", session_name)
    return found


async def full_history_scan() -> None:
    if scan_lock.locked():
        logger.info("History scan skipped: already running.")
        return
    async with scan_lock:
        scan_cancel_event.clear()
        scan_run_id = await start_scan_run(len(clients), 0)
        scan_status.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "current_account": None,
            "current_username": None,
            "current_channel": None,
            "total_accounts": len(clients),
            "processed_accounts": 0,
            "total_channels": 0,
            "total_usernames": 0,
            "processed_usernames": 0,
            "found": 0,
            "fast_channels": 0,
            "targeted_channels": 0,
            "edit_sweep_messages": 0,
            "scan_strategy": "adaptive-fast-channel-sweep",
            "history_limit": SCAN_HISTORY_LIMIT,
            "last_error": None,
            "scan_run_id": scan_run_id,
            "cancel_requested": False,
        })
        await record_app_event("INFO", "scan", "History scan started", {"scan_run_id": scan_run_id, "accounts": len(clients)})
        try:
            semaphore = asyncio.Semaphore(max(1, min(SCAN_ACCOUNT_CONCURRENCY, len(clients))))

            async def scan_with_limit(client: TelegramClient) -> int:
                async with semaphore:
                    if scan_cancel_event.is_set():
                        return 0
                    return await scan_single_account(client)

            tasks = [asyncio.create_task(scan_with_limit(client)) for client in list(clients)]
            for task in asyncio.as_completed(tasks):
                if scan_cancel_event.is_set():
                    break
                found = await task
                scan_status["processed_accounts"] += 1
                await update_scan_run(
                    scan_run_id,
                    processed_accounts=scan_status["processed_accounts"],
                    processed_usernames=scan_status["processed_usernames"],
                    found=scan_status["found"],
                    last_error=scan_status.get("last_error"),
                )
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            status_value = "cancelled" if scan_cancel_event.is_set() else "finished"
            scan_status.update({
                "running": False,
                "finished_at": now_iso(),
                "current_account": None,
                "current_username": None,
                "current_channel": None,
                "cancel_requested": scan_cancel_event.is_set(),
            })
            await update_scan_run(
                scan_run_id,
                status=status_value,
                finished_at=scan_status["finished_at"],
                processed_accounts=scan_status["processed_accounts"],
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
                cancel_requested=1 if scan_cancel_event.is_set() else 0,
            )
            await record_app_event("INFO", "scan", f"History scan {status_value}", {"scan_run_id": scan_run_id, "found": scan_status["found"]})
            scan_cancel_event.clear()


async def backfill_account_name_mentions(client: TelegramClient, per_channel_limit: int) -> int:
    """Re-read one account's channel history without checkpoints or @username
    search, so pings delivered as text-mentions (name links) are re-evaluated.
    Messages already stored are skipped; historical hits raise no notifications.
    """
    session_name = getattr(client, "_session_name_custom", "unknown")
    found = 0
    iter_limit = None if per_channel_limit <= 0 else per_channel_limit
    try:
        me = await client.get_me()
        user_label = me.username or str(me.id)
        scan_status["current_account"] = user_label
        await resolve_ping_user_ids(client)
        dialogs = await list_broadcast_channel_dialogs(client)
        scan_status["total_channels"] = int(scan_status.get("total_channels") or 0) + len(dialogs)
        scan_status["total_usernames"] = int(scan_status.get("total_usernames") or 0) + len(dialogs)
        for dialog in dialogs:
            if scan_cancel_event.is_set():
                break
            entity = dialog.entity
            chat_id = getattr(entity, "id", None)
            if chat_id is None:
                continue
            scan_status["current_channel"] = getattr(dialog, "name", None) or str(chat_id)
            try:
                async for message in client.iter_messages(entity, limit=iter_limit):
                    if scan_cancel_event.is_set():
                        break
                    if await get_ping_by_message_ref(chat_id, getattr(message, "id", None)):
                        continue
                    ping_id = await process_ping_message(
                        client,
                        message,
                        account_label=user_label,
                        notify=False,
                        source="mention-backfill",
                    )
                    if ping_id:
                        found += 1
                        scan_status["found"] += 1
            except FloodWaitError as exc:
                logger.warning("Flood wait during mention backfill in %s: %s seconds", chat_id, exc.seconds)
                scan_status["last_error"] = f"Flood wait {exc.seconds}s in mention backfill"
                await record_app_event("WARNING", "scan", "Telegram flood wait during mention backfill", {"chat_id": chat_id, "seconds": exc.seconds})
                wait = flood_wait_seconds(exc.seconds)
                mark_account_cooldown(session_name, wait)
                await asyncio.sleep(wait)
            except Exception as exc:
                if is_auth_key_duplicated(exc):
                    scan_status["last_error"] = auth_key_duplicated_message(session_name)
                    scan_cancel_event.set()
                    await mark_auth_key_duplicated(session_name, client, exc)
                    break
                scan_status["last_error"] = str(exc)
                await record_app_event("WARNING", "scan", "Mention backfill failed", {"session": session_name, "chat_id": chat_id, "error": str(exc)})
                logger.warning("Mention backfill failed for %s in %s: %s", chat_id, session_name, exc)
            finally:
                scan_status["processed_usernames"] += 1
                if scan_status.get("scan_run_id"):
                    await update_scan_run(
                        int(scan_status["scan_run_id"]),
                        processed_usernames=scan_status["processed_usernames"],
                        found=scan_status["found"],
                        last_error=scan_status.get("last_error"),
                    )
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            scan_status["last_error"] = auth_key_duplicated_message(session_name)
            await mark_auth_key_duplicated(session_name, client, exc)
        else:
            scan_status["last_error"] = str(exc)
            await record_app_event("ERROR", "scan", "Mention backfill account failed", {"session": session_name, "error": str(exc)})
            logger.exception("Mention backfill account failed: %s", session_name)
    return found


async def backfill_name_mention_scan(per_channel_limit: int = 1000) -> None:
    """Surface pings missed before text-mention detection existed by re-reading
    channel history across all accounts. Reuses the scan lock and status so the
    dashboard shows progress and the existing cancel button works.
    """
    if scan_lock.locked():
        logger.info("Mention backfill skipped: a scan is already running.")
        return
    async with scan_lock:
        scan_cancel_event.clear()
        scan_run_id = await start_scan_run(len(clients), 0)
        scan_status.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "current_account": None,
            "current_username": None,
            "current_channel": None,
            "total_accounts": len(clients),
            "processed_accounts": 0,
            "total_channels": 0,
            "total_usernames": 0,
            "processed_usernames": 0,
            "found": 0,
            "fast_channels": 0,
            "targeted_channels": 0,
            "edit_sweep_messages": 0,
            "scan_strategy": "name-mention-backfill",
            "history_limit": per_channel_limit,
            "last_error": None,
            "scan_run_id": scan_run_id,
            "cancel_requested": False,
        })
        await record_app_event("INFO", "scan", "Name-mention backfill started", {"scan_run_id": scan_run_id, "per_channel_limit": per_channel_limit})
        try:
            for client in list(clients):
                if scan_cancel_event.is_set():
                    break
                await backfill_account_name_mentions(client, per_channel_limit)
                scan_status["processed_accounts"] += 1
                await update_scan_run(
                    scan_run_id,
                    processed_accounts=scan_status["processed_accounts"],
                    processed_usernames=scan_status["processed_usernames"],
                    found=scan_status["found"],
                    last_error=scan_status.get("last_error"),
                )
        finally:
            status_value = "cancelled" if scan_cancel_event.is_set() else "finished"
            scan_status.update({
                "running": False,
                "finished_at": now_iso(),
                "current_account": None,
                "current_username": None,
                "current_channel": None,
                "cancel_requested": scan_cancel_event.is_set(),
            })
            await update_scan_run(
                scan_run_id,
                status=status_value,
                finished_at=scan_status["finished_at"],
                processed_accounts=scan_status["processed_accounts"],
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
                cancel_requested=1 if scan_cancel_event.is_set() else 0,
            )
            await record_app_event("INFO", "scan", f"Name-mention backfill {status_value}", {"scan_run_id": scan_run_id, "found": scan_status["found"]})
            scan_cancel_event.clear()


@app.get("/api/accounts", dependencies=[Depends(require_admin)])
async def get_accounts():
    known = {name: {"session_name": name, "status": "known"} for name in SESSION_NAMES}
    known.update(accounts_state)
    return list(known.values())


@app.post("/api/accounts/{session_name}/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_account_api(session_name: str):
    disconnected = await disconnect_account(session_name)
    return {"status": "ok" if disconnected else "not_found"}


class BotKeyCreateRequest(BaseModel):
    label: str = ""
    expires_at: Optional[str] = None


class BotMemberBlockRequest(BaseModel):
    blocked: bool = True


def _bot_share_link(secret: str) -> str:
    return f"https://t.me/{bot_username}?start={secret}" if bot_username else ""


@app.get("/api/bot/access", dependencies=[Depends(require_admin)])
async def get_bot_access():
    keys = await list_bot_keys()
    for key in keys:
        key["share_link"] = _bot_share_link(key.get("secret", ""))
    members = await list_bot_members()
    return {"keys": keys, "members": members, "bot_username": bot_username}


@app.post("/api/bot/access/keys", dependencies=[Depends(require_admin)])
async def create_bot_access_key(data: BotKeyCreateRequest):
    secret = generate_access_key()
    expires_at = (data.expires_at or "").strip() or None
    key = await create_bot_key(data.label.strip(), secret, "viewer", expires_at)
    key["share_link"] = _bot_share_link(secret)
    await record_app_event("INFO", "bot", "Bot access key created", {"label": key.get("label"), "id": key.get("id")})
    return {"status": "ok", "key": key}


@app.post("/api/bot/access/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
async def revoke_bot_access_key(key_id: int):
    await revoke_bot_key(key_id)
    await record_app_event("INFO", "bot", "Bot access key revoked", {"id": key_id})
    return {"status": "ok"}


@app.post("/api/bot/access/members/{tg_id}/block", dependencies=[Depends(require_admin)])
async def block_bot_access_member(tg_id: int, data: BotMemberBlockRequest):
    await set_bot_member_blocked(tg_id, data.blocked)
    await record_app_event("INFO", "bot", "Bot member block toggled", {"tg_id": tg_id, "blocked": data.blocked})
    return {"status": "ok"}


@app.get("/api/settings/usernames", dependencies=[Depends(require_admin)])
async def get_usernames():
    tracking = await load_tracking_settings()
    return {
        "usernames": ping_usernames,
        "saved_usernames": tracking["usernames"],
        "source": tracking.get("source", "runtime"),
        "win_keywords": WIN_KEYWORDS,
        "giveaway_keywords": GIVEAWAY_KEYWORDS,
        "auto_join_giveaways": AUTO_JOIN_GIVEAWAYS,
        "dry_run_giveaways": DRY_RUN_GIVEAWAYS,
    }


@app.put("/api/settings/usernames", dependencies=[Depends(require_admin)])
async def update_usernames(data: UsernamesRequest):
    apply_tracking_settings({"usernames": data.usernames})
    await set_setting("tracking", {"usernames": ping_usernames})
    await record_app_event("INFO", "settings", "Tracked usernames updated", {"count": len(ping_usernames)})
    await publish_live_event("settings-updated", {"scope": "tracking", "usernames": len(ping_usernames)})
    return {"status": "ok", "usernames": ping_usernames}


@app.get("/api/settings/keywords", dependencies=[Depends(require_admin)])
async def get_keywords():
    return await load_keyword_settings()


@app.put("/api/settings/keywords", dependencies=[Depends(require_admin)])
async def update_keywords(data: KeywordsRequest):
    values = {
        "win_keywords": [item.strip() for item in data.win_keywords if item.strip()] or WIN_KEYWORDS,
        "giveaway_keywords": [item.strip().lower() for item in data.giveaway_keywords if item.strip()] or GIVEAWAY_KEYWORDS,
        "high_priority_keywords": [item.strip() for item in data.high_priority_keywords if item.strip()],
        "ignore_keywords": [item.strip() for item in data.ignore_keywords if item.strip()],
    }
    await set_setting("keywords", values)
    apply_keyword_settings(values)
    await record_app_event("INFO", "settings", "Keyword rules updated", {"giveaway_keywords": values["giveaway_keywords"]})
    await publish_live_event("settings-updated", {"scope": "keywords"})
    return {"status": "ok", **values}


@app.get("/api/settings/runtime", dependencies=[Depends(require_admin)])
async def get_runtime_settings():
    saved = await get_setting("runtime", None)
    return {
        "status": "ok",
        "settings": runtime_settings_payload(),
        "saved": sanitize_runtime_settings(saved if isinstance(saved, dict) else None),
        "source": "saved" if isinstance(saved, dict) else "env",
    }


@app.put("/api/settings/runtime", dependencies=[Depends(require_admin)])
async def update_runtime_settings(data: RuntimeSettingsRequest):
    cleaned = apply_runtime_settings(data.model_dump())
    await set_setting("runtime", cleaned)
    await record_app_event("INFO", "settings", "Runtime settings updated", cleaned)
    await publish_live_event("settings-updated", {"scope": "runtime"})
    return {"status": "ok", "settings": cleaned}


@app.get("/api/settings/notifications", dependencies=[Depends(require_admin)])
async def get_notification_settings():
    return await load_notification_settings()


@app.put("/api/settings/notifications", dependencies=[Depends(require_admin)])
async def update_notification_settings(data: NotificationSettingsRequest):
    settings = data.model_dump()
    await set_setting("notifications", settings)
    await record_app_event("INFO", "settings", "Notification rules updated", {"rules": settings})
    await publish_live_event("settings-updated", {"scope": "notifications"})
    return {"status": "ok", "settings": settings}


@app.get("/api/accounts/health", dependencies=[Depends(require_admin)])
async def get_accounts_health():
    ping_stats = await get_account_ping_stats()
    by_sender_id = {str(row.get("sender_id")): row for row in ping_stats if row.get("sender_id") is not None}
    items = []
    known = {name: {"session_name": name, "status": "known"} for name in SESSION_NAMES}
    known.update(accounts_state)
    for account in known.values():
        user_id = account.get("user_id")
        stats = by_sender_id.get(str(user_id), {})
        status_value = account.get("status", "unknown")
        healthy = status_value == "online" and not account.get("last_error")
        items.append({
            **account,
            "healthy": healthy,
            "health_label": "ok" if healthy else ("needs_auth" if status_value == "unauthorized" else "attention"),
            "pings_total": stats.get("total", 0),
            "wins": stats.get("wins", 0),
            "giveaways": stats.get("giveaways", 0),
            "last_ping_at": stats.get("last_ping_at"),
        })
    return {"accounts": items}


@app.get("/api/setup-check", dependencies=[Depends(require_admin)])
async def setup_check():
    checks = [
        {"key": "api_credentials", "label": "TELEGRAM_API_ID/API_HASH", "ok": bool(API_ID and API_HASH)},
        {"key": "admin_token", "label": "ADMIN_TOKEN задан и не слабый", "ok": bool(ADMIN_TOKEN and not is_weak_token(ADMIN_TOKEN))},
        {"key": "viewer_token", "label": "VIEWER_TOKEN задан", "ok": bool(VIEWER_TOKEN)},
        {"key": "sessions", "label": "Найдены Telegram-сессии", "ok": bool(SESSION_NAMES or accounts_state)},
        {"key": "database", "label": "SQLite база доступна", "ok": DB_PATH.exists()},
        {"key": "logs", "label": "Папка логов доступна", "ok": LOG_FILE.parent.exists()},
        {"key": "giveaway_rule", "label": "Розыгрыш = канал + ключевое слово", "ok": True, "details": GIVEAWAY_KEYWORDS},
    ]
    return {"ready": all(item["ok"] for item in checks), "checks": checks}


@app.get("/api/report-html", dependencies=[Depends(require_admin)], response_class=HTMLResponse)
async def report_html():
    data = await get_report_data(limit=120)
    totals = data["totals"]
    rows = "\n".join(
        f"<tr><td>{html.escape(str(row.get('detected_at') or ''))}</td>"
        f"<td>{html.escape(str(row.get('priority_score') or 0))}</td>"
        f"<td>{html.escape(str(row.get('status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('giveaway_status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('chat') or ''))}</td>"
        f"<td>{html.escape(str(row.get('sender') or ''))}</td>"
        f"<td>{html.escape((row.get('text') or '')[:220])}</td></tr>"
        for row in data["recent"]
    )
    chats = "\n".join(
        f"<li>{html.escape(str(row.get('chat') or 'unknown'))}: {row.get('count', 0)} упоминаний, "
        f"{row.get('wins', 0)} побед, средний приоритет {float(row.get('avg_priority') or 0):.1f}</li>"
        for row in data["top_chats"]
    )
    return HTMLResponse(f"""
    <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Pulse Desk Report</title>
    <style>body{{font-family:Inter,Arial,sans-serif;margin:32px;color:#181816}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:8px;padding:12px;min-width:140px}}</style>
    </head><body>
    <h1>Pulse Desk Report</h1><p>Сформировано: {html.escape(data["generated_at"])}</p>
    <div class="cards">
      <div class="card"><strong>{totals.get('total', 0)}</strong><br>Всего</div>
      <div class="card"><strong>{totals.get('new_count', 0)}</strong><br>Новые</div>
      <div class="card"><strong>{totals.get('important_count', 0)}</strong><br>Важные</div>
      <div class="card"><strong>{totals.get('wins', 0)}</strong><br>Победы</div>
      <div class="card"><strong>{totals.get('giveaways', 0)}</strong><br>Розыгрыши</div>
    </div>
    <h2>Лучшие источники</h2><ul>{chats}</ul>
    <h2>Важные упоминания</h2>
    <table><thead><tr><th>Дата</th><th>Приоритет</th><th>Статус</th><th>Розыгрыш</th><th>Чат</th><th>Автор</th><th>Текст</th></tr></thead><tbody>{rows}</tbody></table>
    </body></html>
    """)


@app.get("/api/events", dependencies=[Depends(require_admin)])
async def read_events(limit: int = Query(100, ge=1, le=500), level: Optional[str] = None):
    return {"events": await get_events(limit=limit, level=level)}


@app.get("/api/logs", dependencies=[Depends(require_admin)])
async def get_logs(limit: int = Query(50, ge=1, le=500), level: Optional[str] = None):
    if not LOG_FILE.exists():
        return {"logs": []}
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    if level:
        level_upper = level.upper()
        lines = [line for line in lines if f"[{level_upper}]" in line]
    return {"logs": lines[-limit:]}


if __name__ == "__main__":
    import uvicorn

    host = settings.host
    port = settings.port
    if host not in {"127.0.0.1", "localhost"} and not ADMIN_TOKEN:
        logger.warning("Server is exposed on %s without ADMIN_TOKEN/WEB_AUTH_TOKEN.", host)
    uvicorn.run(app, host=host, port=port, access_log=False)
