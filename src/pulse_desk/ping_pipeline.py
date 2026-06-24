"""Ping processing pipeline: classify message, score, deadlines, persist, notify."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException
from telethon import TelegramClient, types
from telethon.tl.functions.channels import GetFullChannelRequest

from telegram_ping_watcher import chat_type_from_entity, message_looks_like_broadcast_channel, message_to_record

from .app_ctx import logger, state
from .bot_notify import send_bot_notification, send_check_notification
from .common import now_iso, record_app_event
from .deadlines import iso_or_none, parse_claim_deadline, parse_deadline, parse_participation_deadline
from .giveaway_actions import analyze_and_store_giveaway
from .giveaways import giveaway_outcome_resolution, is_check_text, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule, should_analyze_giveaway
from .live import publish_live_event
from .push import send_push

CHANNEL_PROFILE_TTL_SECONDS = 6 * 60 * 60


def check_is_win(text: str) -> bool:
    return is_win_text(text, state.win_keywords)


def check_is_giveaway(text: str, chat_type: str = "") -> bool:
    return matches_strict_giveaway_rule(text, chat_type, state.giveaway_keywords)


def check_is_check(text: str) -> bool:
    return is_check_text(text, state.check_keywords)


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
    deadline_reference: Optional[datetime] = None,
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
    from database import get_ping_by_id, replace_ping_reminders, update_ping_deadline

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
    pending = [u for u in state.ping_usernames if u.lower() not in state.ping_user_ids_resolved]
    for username in pending:
        state.ping_user_ids_resolved.add(username.lower())
        try:
            entity = await client.get_entity(username)
        except Exception:
            logger.debug("Could not resolve tracked username %s to user id", username, exc_info=True)
            continue
        uid = getattr(entity, "id", None)
        if uid is not None:
            state.ping_user_ids[int(uid)] = username


async def fan_push_ping(ping: dict) -> None:
    from database import get_push_subscriptions

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


async def process_ping_message(
    client: TelegramClient,
    message: Any,
    *,
    account_label: str = "",
    notify: bool = True,
    source: str = "telegram",
) -> Optional[int]:
    from database import get_ping_by_message_ref, replace_ping_reminders, save_ping

    chat_type = await get_message_chat_type(client, message)
    if chat_type != "channel":
        return None
    await resolve_ping_user_ids(client)
    is_check = check_is_check(getattr(message, "raw_text", "") or "")
    record = await message_to_record(client, message, state.ping_regex, state.ping_usernames, tracked_ids=state.ping_user_ids or None)
    if not record:
        # Checks rarely mention a tracked username — capture them anyway.
        if not is_check:
            return None
        record = await message_to_record(
            client, message, state.ping_regex, state.ping_usernames,
            require_mentions=False, tracked_ids=state.ping_user_ids or None,
        )
        if not record:
            return None
    record["chat_type"] = chat_type
    record["detected_at"] = now_iso()
    record["is_check"] = is_check
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
    if ping_id and should_analyze_giveaway(bool(record.get("is_giveaway")), existing is None, source):
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
        if record.get("is_check"):
            await send_check_notification(record, ping_id=ping_id)
        else:
            await send_bot_notification(record, ping_id=ping_id, auto_joined=record["auto_joined"])
    if notify and existing is None and ping_id:
        saved_record = {**record, "id": ping_id}
        asyncio.create_task(fan_push_ping(saved_record))
    return int(ping_id) if existing is None else None
