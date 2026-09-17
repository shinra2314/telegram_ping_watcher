"""Ping processing pipeline: classify message, score, persist, notify."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from telethon import TelegramClient, types
from telethon.tl.functions.channels import GetFullChannelRequest

from telegram_ping_watcher import (
    chat_type_from_entity,
    is_mass_tag,
    mentions_in_text,
    message_looks_like_broadcast_channel,
    message_to_record,
)

from .analytics import invalidate_analytics_cache
from .app_ctx import logger, state
from .common import now_iso, record_app_event
from .giveaway_actions import analyze_and_store_giveaway
from .giveaways import giveaway_outcome_resolution, is_giveaway_outcome_text, is_win_text, matches_strict_giveaway_rule, should_analyze_giveaway
from .ignored_chats import is_ignored
from .ping_notify import notify_detected_ping, settle_quietly
from .public_preview import chat_username, is_unreadable_media, recover_public_text

CHANNEL_PROFILE_TTL_SECONDS = 6 * 60 * 60
RESOLVE_RETRY_SECONDS = 600.0
# tracked username -> monotonic deadline before the next resolve attempt.
_resolve_retry_at: dict[str, float] = {}

# Where a mention is looked for. Groups cover supergroups and — the reason they
# were added — the discussion group behind a channel, where comments live: a
# winner tagged under a post was invisible while only channels counted.
# Private chats stay out: nobody @-mentions an account in its own DMs.
WATCHED_CHAT_TYPES = frozenset({"channel", "group"})


def own_mention(message: Any, account_username: str, tracked: list[str]) -> Optional[str]:
    """The tracked name of the receiving account when Telegram says it was mentioned.

    ``message.mentioned`` is Telegram's own flag for "this mentions *you*": an
    @mention of the account or a reply to its message — the second has no @ in
    the text at all, so the text parser alone never sees it. Only an account
    that is itself tracked counts, and its own messages never do.
    """
    if not account_username or not getattr(message, "mentioned", False) or getattr(message, "out", False):
        return None
    wanted = account_username.strip().lstrip("@").lower()
    for name in tracked:
        if name.lower() == wanted:
            return f"@{name}"
    return None


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
    score = max(0, min(score, 100))
    # The floor `database.giveaways.outcome_target` applies at startup. A channel
    # win whose text misses the strict giveaway rule scored 85 here (70 + 10 + 5),
    # so every sweep wrote 85 over the startup's 90 and every start wrote 90 back.
    if record.get("is_win") and record.get("mentions") and is_giveaway_outcome_text(record.get("text") or ""):
        score = max(score, 90)
    record["priority_score"] = score
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
    text, changed = await recover_public_text(
        username, getattr(message, "id", None), getattr(message, "edit_date", None))
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
    if changed:
        await record_app_event(
            "INFO",
            "scan",
            "Undecodable post recovered from its public page",
            {"chat": record.get("chat"), "message_id": record.get("message_id"), "mentions": mentions},
        )
    return record


async def link_win_copies(ping_id: int) -> Optional[int]:
    """Glue a win to an earlier copy of the same winners post (see dedupe.py).

    Returns the primary's id when the row turned out to be (or to have) a copy.
    Never raises into the pipeline: a failed link only leaves a duplicate row.
    """
    from database import get_ping_by_id, get_wins_for_dedupe, mark_duplicates

    from .dedupe import WINDOW, find_primary, source_rank

    try:
        row = await get_ping_by_id(ping_id)
        if not row or not row.get("is_win") or row.get("duplicate_of"):
            return None
        since = (datetime.now() - 2 * WINDOW).replace(microsecond=0).isoformat()
        primary = find_primary(row, await get_wins_for_dedupe(since, primaries_only=True))
        if primary is None:
            return None
        if source_rank(row) < source_rank(primary):
            # The new row is the better source (the channel post arrived after
            # a forwarded copy): it becomes the primary.
            await mark_duplicates(ping_id, [int(primary["id"])])
            return ping_id
        await mark_duplicates(int(primary["id"]), [ping_id])
        return int(primary["id"])
    except Exception:
        logger.debug("Win de-duplication failed for %s", ping_id, exc_info=True)
        return None


async def dedupe_existing_wins() -> int:
    """One-off pass over stored wins; returns how many rows became duplicates."""
    from database import get_wins_for_dedupe, mark_duplicates, update_ping_meta

    from .dedupe import group_duplicates

    final = {"claimed", "scam", "missed", "closed"}
    rows = await get_wins_for_dedupe()
    by_id = {int(r["id"]): r for r in rows}
    marked = 0
    for primary_id, copies in group_duplicates(rows):
        primary = by_id[primary_id]
        if (primary.get("action_status") or "new") not in final:
            # The owner may have claimed one of the copies before they were
            # glued: that decision belongs to the whole group.
            handled = next((by_id[c] for c in copies if (by_id[c].get("action_status") or "new") in final), None)
            if handled:
                await update_ping_meta(primary_id, giveaway_status=handled.get("giveaway_status") or "",
                                       action_status=handled.get("action_status"))
        marked += await mark_duplicates(primary_id, copies)
    return marked


async def process_ping_message(
    client: TelegramClient,
    message: Any,
    *,
    account_label: str = "",
    account_username: str = "",
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

    ``account_username`` is the receiving account's own @username, so a reply
    to it in a group counts as its mention (see ``own_mention``).
    """
    from database import get_ping_by_message_ref, save_ping

    if is_ignored(getattr(message, "chat_id", None)):
        # The owner muted this chat for good (🔇 on a card): nothing is read.
        return None
    chat_type = await get_message_chat_type(client, message)
    if chat_type not in WATCHED_CHAT_TYPES:
        return None
    own = None
    if chat_type == "group":
        if getattr(message, "sender_id", None) in state.connected_user_ids:
            # The owner's own accounts talking in a chat are not news.
            return None
        own = own_mention(message, account_username, state.ping_usernames)
    await resolve_ping_user_ids(client)
    record = await message_to_record(
        client,
        message,
        state.ping_regex,
        state.ping_usernames,
        require_mentions=not (search_mentions or own),
        tracked_ids=state.ping_user_ids or None,
    )
    if not record and chat_type == "channel":
        record = await undecodable_media_record(client, message)
    if not record:
        return None
    if search_mentions and not record.get("mentions") and not (record.get("text") or "").strip():
        # Nothing local to judge by: the server matched the mention, trust it.
        record["mentions"] = list(search_mentions)
        record["text"] = search_text or record.get("text") or ""
    if own and own.lower() not in {str(name).lower() for name in record.get("mentions") or []}:
        record["mentions"] = sorted([*(record.get("mentions") or []), own], key=str.lower)
    if not record.get("mentions"):
        return None
    if notify and chat_type == "group" and is_mass_tag(message, state.ping_regex, state.ping_usernames):
        # A tag bot's ad (hidden links on a row of emoji): kept in the feed, but
        # no card and no member copy — the owner asked for these not to ping.
        notify = False
        logger.debug("Mass tag stored without a card in %s", record.get("chat"))
    record["chat_type"] = chat_type
    record["detected_at"] = now_iso()
    classify_record(record)
    if search_is_win:
        record["is_win"] = True
    apply_giveaway_state(record)
    apply_priority(record)
    apply_action_state(record)

    existing = await get_ping_by_message_ref(record.get("chat_id"), record.get("message_id"))
    if existing and chat_type == "group":
        # A group mention can be per account (a reply to one of them): another
        # account's read of the same message must not overwrite it away.
        merged = {str(name).lstrip("@").lower(): f"@{str(name).lstrip('@')}"
                  for name in [*(existing.get("mentions") or []), *record["mentions"]]}
        record["mentions"] = sorted(merged.values(), key=str.lower)
    if not notify:
        # Backlog: stored without a card, and settled at birth so notify-retry
        # does not send one later either.
        record["notified_at"] = now_iso()
    ping_id = await save_ping(record)
    # Counters just moved; drop the memoised analytics so the next card is fresh.
    invalidate_analytics_cache()
    analyze = bool(ping_id) and should_analyze_giveaway(bool(record.get("is_giveaway")), existing is None, source)

    if not ping_id:
        return None
    if record.get("is_win"):
        await link_win_copies(int(ping_id))
    win_upgrade = upgraded_to_win(existing, record)
    if existing is None:
        found_label = "Channel ping found" if chat_type == "channel" else "Group ping found"
        logger.info("%s by %s in %s", found_label, account_label or "unknown", record["chat"])
        await record_app_event(
            "INFO",
            source,
            found_label,
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

    async def run_analysis() -> None:
        await analyze_and_store_giveaway(client, int(ping_id), record, message)

    announce = existing is None or win_upgrade
    if notify and announce:
        # A win edited into a known post owes the 🏆 card: the first-detection
        # card said "mention"/"giveaway". Delivery is guaranteed by the outbox
        # (ping_notify); the analysis runs between the owner's card and the
        # member broadcast, which filters on its score.
        await notify_detected_ping(int(ping_id), record, before_broadcast=run_analysis if analyze else None)
    else:
        if announce:
            await settle_quietly(int(ping_id), record)
        if analyze:
            await run_analysis()
    return int(ping_id) if existing is None else None
