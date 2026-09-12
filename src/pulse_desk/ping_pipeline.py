"""Ping processing pipeline: classify message, score, persist, notify."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Optional

from telethon import TelegramClient, types
from telethon.tl.functions.channels import GetFullChannelRequest

from telegram_ping_watcher import (
    chat_type_from_entity,
    mentions_in_text,
    message_looks_like_broadcast_channel,
    message_to_record,
)

from .analytics import invalidate_analytics_cache
from .app_ctx import logger, state
from .bot_notify import send_bot_notification
from .common import now_iso, record_app_event
from .giveaway_actions import analyze_and_store_giveaway
from .giveaways import giveaway_outcome_resolution, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule, should_analyze_giveaway
from .public_preview import chat_username, fetch_public_message_text, is_unreadable_media

CHANNEL_PROFILE_TTL_SECONDS = 6 * 60 * 60
RESOLVE_RETRY_SECONDS = 600.0
# tracked username -> monotonic deadline before the next resolve attempt.
_resolve_retry_at: dict[str, float] = {}


def check_is_win(text: str) -> bool:
    return is_win_text(text, state.win_keywords)


def check_is_giveaway(text: str, chat_type: str = "") -> bool:
    return matches_strict_giveaway_rule(text, chat_type, state.giveaway_keywords)


def classify_record(record: dict[str, Any]) -> dict[str, Any]:
    """Set is_win / is_giveaway; both require a tracked-username mention."""
    mentions_me = bool(record.get("mentions"))
    text = record.get("text") or ""
    record["is_win"] = mentions_me and check_is_win(text)
    record["is_giveaway"] = mentions_me and check_is_giveaway(text, record.get("chat_type") or "")
    return record


def upgraded_to_win(existing: Optional[dict[str, Any]], record: dict[str, Any]) -> bool:
    """True when a re-read turned an already-stored ping into a win.

    Channels often edit the original post to append the winner list, so the
    message is already in the database when it becomes a win. Without this the
    flag would flip silently and the owner would only see it on the dashboard.
    """
    if not existing:
        return False
    return bool(record.get("is_win")) and not bool(existing.get("is_win"))


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
    if any(keyword.lower() in text for keyword in state.high_priority_keywords):
        score += 20
    if any(keyword.lower() in text or keyword.lower() in chat for keyword in state.ignore_keywords):
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


async def refresh_channel_profile(
    client: TelegramClient,
    chat_id: int,
    chat_label: str = "",
    force: bool = False,
) -> dict[str, Any]:
    from database import get_channel_profile, upsert_channel_profile

    cached = await get_channel_profile(chat_id)
    if cached and not force and _profile_is_fresh(cached):
        return cached
    try:
        entity = await client.get_entity(chat_id)
        if not isinstance(entity, types.Channel):
            return cached or {"chat_id": chat_id, "chat": chat_label, "last_error": "not a channel"}
        full = await client(GetFullChannelRequest(entity))
        description = getattr(getattr(full, "full_chat", None), "about", "") or ""
        chat_name = getattr(entity, "title", None) or chat_label or str(chat_id)
        username = getattr(entity, "username", None) or ""
        await upsert_channel_profile(
            chat_id=chat_id,
            chat=chat_name,
            username=username,
            description=description,
            last_error="",
        )
        return await get_channel_profile(chat_id) or {}
    except Exception as exc:
        await upsert_channel_profile(
            chat_id=chat_id,
            chat=(cached or {}).get("chat") or chat_label or str(chat_id),
            username=(cached or {}).get("username") or "",
            description=(cached or {}).get("description") or "",
            last_error=str(exc),
        )
        logger.debug("Could not refresh channel profile for %s", chat_id, exc_info=True)
        return await get_channel_profile(chat_id) or {"chat_id": chat_id, "chat": chat_label, "last_error": str(exc)}


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
    arrive as MessageEntityMentionName carrying a user_id, not text. A username
    is looked up once on success; a failed lookup (flood wait, hiccup at start-up)
    is retried after a cooldown instead of staying blind for the whole process.
    """
    now = time.monotonic()
    for username in state.ping_usernames:
        key = username.lower()
        if key in state.ping_user_ids_resolved or _resolve_retry_at.get(key, 0.0) > now:
            continue
        try:
            entity = await client.get_entity(username)
        except Exception:
            _resolve_retry_at[key] = now + RESOLVE_RETRY_SECONDS
            logger.debug("Could not resolve tracked username %s to user id", username, exc_info=True)
            continue
        uid = getattr(entity, "id", None)
        if uid is None:
            _resolve_retry_at[key] = now + RESOLVE_RETRY_SECONDS
            continue
        state.ping_user_ids[int(uid)] = username
        state.ping_user_ids_resolved.add(key)
        _resolve_retry_at.pop(key, None)


async def undecodable_media_record(client: TelegramClient, message: Any) -> Optional[dict[str, Any]]:
    """Record for a post whose media Telethon cannot decode, read from t.me.

    Telegram strips media newer than the client's layer down to
    ``messageMediaUnsupported``, leaving no text, no entities and nothing for
    server-side search to index — which is how a win inside a mini-app lottery
    result card went unnoticed. The public page still renders the card, so the
    text is recovered from there and matched like any other message: no tracked
    mention, no ping.
    """
    if not is_unreadable_media(message):
        return None
    try:
        username = chat_username(await message.get_chat())
    except Exception:
        logger.debug("Could not resolve the chat of an undecodable message", exc_info=True)
        return None
    if not username:
        # Private channel: no public page, nothing to recover from.
        return None
    try:
        text = await fetch_public_message_text(username, getattr(message, "id", None))
    except Exception:
        logger.debug("Could not read the public page of %s/%s", username, getattr(message, "id", None), exc_info=True)
        return None
    mentions = mentions_in_text(text, state.ping_regex, state.ping_usernames)
    if not mentions:
        return None
    record = await message_to_record(
        client,
        message,
        state.ping_regex,
        state.ping_usernames,
        require_mentions=False,
        tracked_ids=state.ping_user_ids or None,
    )
    if not record:
        return None
    record["text"] = text
    record["mentions"] = mentions
    await record_app_event(
        "INFO",
        "scan",
        "Undecodable post recovered from its public page",
        {"chat": record.get("chat"), "message_id": record.get("message_id"), "mentions": mentions},
    )
    return record


async def process_ping_message(
    client: TelegramClient,
    message: Any,
    *,
    account_label: str = "",
    notify: bool = True,
    source: str = "telegram",
    search_mentions: Optional[list[str]] = None,
    search_text: str = "",
    search_is_win: bool = False,
) -> Optional[int]:
    """Store one message as a ping, when it mentions a tracked username.

    ``search_*`` carry a verdict that came from Telegram's own search index
    instead of the local text — the only source for a mini-app card whose
    body Telethon cannot decode (see ``global_search``). They are ignored
    whenever the message has real text to parse.
    """
    from database import get_ping_by_message_ref, save_ping

    chat_type = await get_message_chat_type(client, message)
    if chat_type != "channel":
        return None
    await resolve_ping_user_ids(client)
    record = await message_to_record(
        client,
        message,
        state.ping_regex,
        state.ping_usernames,
        require_mentions=not search_mentions,
        tracked_ids=state.ping_user_ids or None,
    )
    if not record:
        record = await undecodable_media_record(client, message)
    if not record:
        return None
    if search_mentions and not record.get("mentions") and not (record.get("text") or "").strip():
        # Nothing local to judge by: the server matched the mention, trust it.
        record["mentions"] = list(search_mentions)
        record["text"] = search_text or record.get("text") or ""
    if not record.get("mentions"):
        return None
    record["chat_type"] = chat_type
    record["detected_at"] = now_iso()
    classify_record(record)
    if search_is_win:
        record["is_win"] = True
    apply_giveaway_state(record)
    apply_priority(record)
    apply_action_state(record)

    existing = await get_ping_by_message_ref(record.get("chat_id"), record.get("message_id"))
    ping_id = await save_ping(record)
    # Counters just moved; drop the memoised analytics so the next card is fresh.
    invalidate_analytics_cache()
    if ping_id and should_analyze_giveaway(bool(record.get("is_giveaway")), existing is None, source):
        await analyze_and_store_giveaway(client, int(ping_id), record, message)

    if not ping_id:
        return None
    win_upgrade = upgraded_to_win(existing, record)
    if existing is None:
        logger.info("Channel ping found by %s in %s", account_label or "unknown", record["chat"])
        await record_app_event(
            "INFO",
            source,
            "Channel ping found",
            {"account": account_label, "chat": record.get("chat"), "ping_id": ping_id},
        )
    elif win_upgrade:
        logger.info("Ping upgraded to win by %s in %s", account_label or "unknown", record["chat"])
        await record_app_event(
            "INFO",
            source,
            "Ping upgraded to win",
            {"account": account_label, "chat": record.get("chat"), "ping_id": ping_id},
        )
    if notify and win_upgrade:
        # The post was edited into a win — the first-detection card said
        # "mention"/"giveaway", so send the 🏆 card now.
        await send_bot_notification(record, ping_id=ping_id)
    if notify and existing is None:
        await send_bot_notification(record, ping_id=ping_id)
    return int(ping_id) if existing is None else None
