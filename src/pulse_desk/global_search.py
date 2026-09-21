"""Global Telegram search pass — mentions the history sweep cannot see.

The channel sweep reads messages and parses their text. That misses a whole
class of posts: mini-app result cards (the @Random winner table) arrive as
``messageMediaUnsupported``, so Telethon sees ``message=''``, no entities and
no buttons — the mention parser has nothing to match, and the post is stored
(at best) as an empty ping that never becomes a win.

Telegram's own index knows better. ``messages.searchGlobal`` returns exactly
those cards for a text query: searching "Результаты розыгрыша" across all
dialogs comes back with messages whose local ``raw_text`` is empty. Per-peer
``messages.search`` does **not** cover them, which is why this pass is global
and not another per-channel query.

So a global-search hit is itself the proof of the mention: the server matched
``@username`` inside a message we cannot read. Such a card is recorded with
that mention and a placeholder text pointing the owner at Telegram. Whether it
is a *win* comes from the same oracle — a second round of global searches for
the win keywords, intersected with the mention hits by (chat_id, message_id).

Messages that do carry readable text go through the normal parser instead:
search is prefix-based, so "@name" can match "@name2", and the local parser is
the stricter judge whenever there is something to judge.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from .app_ctx import logger, settings, state
from .common import flood_wait_seconds, record_app_event
from .ping_pipeline import NOTIFY_MAX_AGE_SECONDS, process_ping_message
from .telegram_accounts import mark_account_cooldown

# Win-keyword queries are only worth running when a textless card showed up, so
# the cap is about bounding that rare burst, not the common path.
WIN_ORACLE_MAX_QUERIES = 12
WIN_ORACLE_LIMIT = 40
# Search answers with its whole backlog, not just what happened since the last
# sweep, so the first pass (and every tracked-username change) would otherwise
# fire a notification per historical mention. Old hits are still recorded — they
# land on the dashboard and the giveaway board — they just arrive quietly. The
# age limit is the pipeline's own (NOTIFY_MAX_AGE_SECONDS); this pass is stricter
# only about a hit with no date at all.


def global_search_limit() -> int:
    """Hits to pull per tracked username, 0 disabling the pass entirely."""
    try:
        return max(0, int(getattr(settings, "global_search_limit", 40)))
    except (TypeError, ValueError):
        return 40


def mention_query(username: str) -> str:
    return f"@{str(username).strip().lstrip('@')}"


def message_ref(message: Any) -> Optional[tuple[Any, int]]:
    """(chat_id, message_id) identity, or None when either half is missing."""
    chat_id = getattr(message, "chat_id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return None
    return (chat_id, int(message_id))


def has_readable_text(message: Any) -> bool:
    return bool((getattr(message, "raw_text", "") or "").strip())


def is_notifiable(message: Any, now: Optional[datetime] = None, max_age: float = NOTIFY_MAX_AGE_SECONDS) -> bool:
    """Whether a search hit is fresh enough to be worth pinging the owner about.

    Age is counted from the latest of the post and its last edit. Channels
    append the winners to the giveaway post itself, often a day or more after
    posting; judged by ``date`` alone that win was "backlog" and was stored
    with no card at all — and the sweep that would have announced it then
    found it already a win.

    A message with no usable date stays quiet: an unknown age is far more
    likely to be backlog than breaking news.
    """
    stamps = [
        stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        for stamp in (getattr(message, "date", None), getattr(message, "edit_date", None))
        if isinstance(stamp, datetime)
    ]
    if not stamps:
        return False
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - max(stamps)).total_seconds() <= float(max_age)


def textless_card_text(mentions: list[str], link: str = "") -> str:
    """Placeholder body for a card Telethon cannot read.

    Deliberately free of win keywords: the classifier runs over this text, and
    the win verdict must come from the search oracle, not from the wording of
    a placeholder we wrote ourselves.
    """
    names = ", ".join(f"@{name.lstrip('@')}" for name in mentions) or "отслеживаемый аккаунт"
    lines = [
        "🃏 Карточка mini-app: её содержимое видно только в самом Telegram.",
        f"Поиск Telegram нашёл в ней {names} — открой сообщение и проверь список.",
    ]
    if link:
        lines.append(link)
    return "\n".join(lines)


async def _global_search(client: TelegramClient, query: str, limit: int) -> list[Any]:
    return [message async for message in client.iter_messages(None, search=query, limit=limit)]


async def win_reference_set(
    client: TelegramClient,
    keywords: list[str],
    limit: int = WIN_ORACLE_LIMIT,
) -> set[tuple[Any, int]]:
    """Message refs that Telegram's index matches against the win keywords.

    This is the only way to classify a card whose text we cannot read: ask the
    server which messages contain a winner word, then check whether the card
    that mentions us is among them.
    """
    refs: set[tuple[Any, int]] = set()
    for keyword in [item for item in keywords if str(item).strip()][:WIN_ORACLE_MAX_QUERIES]:
        try:
            messages = await _global_search(client, str(keyword).strip(), limit)
        except FloodWaitError:
            raise
        except Exception as exc:
            logger.debug("Win oracle search failed for %r: %s", keyword, exc)
            continue
        for message in messages:
            ref = message_ref(message)
            if ref:
                refs.add(ref)
    return refs


async def scan_global_mentions(
    client: TelegramClient,
    *,
    account_label: str = "",
    session_name: str = "",
    account_username: str = "",
) -> int:
    """One global-search pass per tracked username for a single account."""
    limit = global_search_limit()
    if limit <= 0 or not state.ping_usernames:
        return 0
    scan_status = state.scan_status
    found = 0
    cards = 0
    win_refs: Optional[set[tuple[Any, int]]] = None
    for username in state.ping_usernames:
        if state.scan_cancel_event.is_set():
            break
        try:
            hits = await _global_search(client, mention_query(username), limit)
        except FloodWaitError as exc:
            logger.warning("Flood wait during global search for @%s: %s seconds", username, exc.seconds)
            scan_status["last_error"] = f"Flood wait {exc.seconds}s in global search"
            await record_app_event(
                "WARNING", "scan", "Telegram flood wait during global search",
                {"username": username, "seconds": exc.seconds},
            )
            wait = flood_wait_seconds(exc.seconds)
            mark_account_cooldown(session_name, wait)
            await asyncio.sleep(wait)
            break
        except Exception as exc:
            logger.warning("Global search failed for @%s in %s: %s", username, session_name, exc)
            scan_status["last_error"] = str(exc)
            continue
        for message in hits:
            if state.scan_cancel_event.is_set():
                break
            notify = is_notifiable(message)
            if has_readable_text(message):
                # There is text: let the normal parser decide, it is stricter
                # than a prefix-matching search query.
                ping_id = await process_ping_message(
                    client, message, account_label=account_label, account_username=account_username,
                    notify=notify, source="global-search",
                )
            else:
                cards += 1
                if win_refs is None:
                    try:
                        win_refs = await win_reference_set(client, state.win_keywords)
                    except FloodWaitError as exc:
                        logger.warning("Flood wait building the win oracle: %s seconds", exc.seconds)
                        wait = flood_wait_seconds(exc.seconds)
                        mark_account_cooldown(session_name, wait)
                        await asyncio.sleep(wait)
                        win_refs = set()
                ping_id = await process_ping_message(
                    client, message, account_label=account_label, account_username=account_username,
                    notify=notify, source="global-search",
                    search_mentions=[username],
                    search_text=textless_card_text([username]),
                    search_is_win=message_ref(message) in (win_refs or set()),
                )
            if ping_id:
                found += 1
                scan_status["found"] += 1
    scan_status["global_search_cards"] = int(scan_status.get("global_search_cards") or 0) + cards
    scan_status["global_search_found"] = int(scan_status.get("global_search_found") or 0) + found
    if found:
        logger.info("Global search found %s new pings for %s", found, account_label or session_name)
    return found
