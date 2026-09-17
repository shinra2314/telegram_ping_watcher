"""Every detected mention reaches the owner.

A ping card used to be one shot. ``process_ping_message`` sent it only when the
ping was new (``existing is None``), so anything that got in the way — the bot
offline or never started, three failed attempts, an exception, the process
restarted between saving and sending — left a stored ping that no later pass
would ever announce. On 15.08 the bot failed to start at boot and stayed silent
for 3 h 20 min.

The owner's cards are now an outbox on the ``pings`` row (schema 24):
``notified_at`` / ``win_notified_at`` are set once the card really went out.
The pipeline delivers straight away as before; ``notify-retry`` sends whatever
is still owed. Member copies are not part of this — they go out once, at
detection, and have their own queue (``bot_pending_sends``).

Two guards keep a card from going out twice: ``_inflight`` (ping ids being sent
right now — claimed before the row is read, so a live update and a sweep racing
on the same post cannot both see it owed) and ``_broadcasted`` (pings whose
member broadcast already ran in this process).
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, Optional

from .app_ctx import logger, state
from .common import record_app_event

RETRY_POLL_SECONDS = 30
# A ping saved a moment ago is still in the hands of the pipeline that found it.
RETRY_GRACE_SECONDS = 90
# Past this a card per ping is stale news; they are settled with one summary.
RETRY_WINDOW_HOURS = 24
RETRY_BATCH = 20
BROADCAST_MEMORY = 5000

_inflight: set[int] = set()
_broadcasted: "OrderedDict[str, None]" = OrderedDict()


def owed_cards(row: Optional[dict[str, Any]]) -> tuple[bool, bool]:
    """(first card owed, 🏆 card owed) for a ``pings`` row."""
    if not row:
        return False, False
    first = not row.get("notified_at")
    win = bool(row.get("is_win")) and not row.get("win_notified_at")
    return first, win


def claim_broadcast(ping_id: int, is_win: bool) -> bool:
    """True exactly once per (ping, first/win) in this process."""
    key = f"{int(ping_id)}:{'win' if is_win else 'ping'}"
    if key in _broadcasted:
        return False
    _broadcasted[key] = None
    while len(_broadcasted) > BROADCAST_MEMORY:
        _broadcasted.popitem(last=False)
    return True


def reset_for_tests() -> None:
    _inflight.clear()
    _broadcasted.clear()


async def deliver_owner_card(
    ping_id: int,
    record: Optional[dict[str, Any]] = None,
    *,
    settings: Optional[dict[str, Any]] = None,
    throttled: bool = False,
    late: bool = False,
) -> tuple[bool, Any]:
    """Send the owner's card for ``ping_id`` if one is owed.

    Returns ``(settled, sent_message)``: ``settled`` is True when nothing is
    owed any more (just delivered, or someone else already did), ``sent_message``
    is set only when this call sent it. ``record`` is the pipeline's fresh read;
    the retry loop passes None and the stored row is used.
    """
    from database import get_ping_by_id, mark_ping_notified

    from .bot_notify import owner_card_silent, send_owner_ping_card
    from .watch_settings import default_notification_settings, load_notification_settings

    ping_id = int(ping_id)
    if ping_id in _inflight:
        return False, None
    _inflight.add(ping_id)
    try:
        row = await get_ping_by_id(ping_id)
        first, win = owed_cards(row)
        if not (first or win):
            return True, None
        card = dict(row)
        if record:
            # The fresh read has the live chat title and text; the row has the
            # sticky flags (a win is never un-won by a later pass).
            card.update({key: record[key] for key in ("chat", "chat_type", "sender", "text", "link", "mentions") if record.get(key)})
        # Stored mentions come back without "@"; the card prints them as names.
        card["mentions"] = [name if str(name).startswith("@") else f"@{name}" for name in card.get("mentions") or []]
        if settings is None:
            try:
                settings = await load_notification_settings()
            except Exception:
                settings = default_notification_settings()
        silent = await owner_card_silent(card, settings, throttled=throttled)
        late_since = None
        if late:
            late_since = (row.get("win_detected_at") if win else None) or row.get("detected_at")
        sent = await send_owner_ping_card(card, ping_id, silent=silent, late_since=late_since)
        if sent is None:
            return False, None
        await mark_ping_notified(ping_id, win=bool(row.get("is_win")))
        return True, sent
    finally:
        _inflight.discard(ping_id)


async def notify_detected_ping(
    ping_id: int,
    record: dict[str, Any],
    *,
    before_broadcast: Optional[Callable[[], Awaitable[Any]]] = None,
) -> bool:
    """A ping the pipeline just found (or just saw become a win).

    Owner's card first; then ``before_broadcast`` (the giveaway analysis, whose
    score the member broadcast filters on); then the one-time broadcast, which
    edits the owner's card with what it produced. Returns True when the owner's
    card is settled. Never raises into the pipeline.
    """
    from .bot_notify import broadcast_ping
    from .watch_settings import (
        default_notification_settings,
        load_notification_settings,
        should_throttle_notification,
    )

    try:
        settings = await load_notification_settings()
    except Exception:
        logger.exception("Could not load notification settings; using defaults")
        settings = default_notification_settings()
    throttled = should_throttle_notification(record, int(settings.get("cooldown_seconds", 120) or 0))
    settled, sent = False, None
    try:
        settled, sent = await deliver_owner_card(ping_id, record, settings=settings, throttled=throttled)
    except Exception:
        logger.exception("Owner's ping card failed for %s; notify-retry will resend it", ping_id)
    if before_broadcast is not None:
        try:
            await before_broadcast()
        except Exception:
            logger.exception("Pre-broadcast step failed for ping %s", ping_id)
    if claim_broadcast(ping_id, bool(record.get("is_win"))):
        try:
            await broadcast_ping(record, ping_id, settings=settings, throttled=throttled, owner_message=sent)
        except Exception:
            logger.exception("Member broadcast failed for ping %s", ping_id)
    return settled


async def settle_quietly(ping_id: int, record: dict[str, Any]) -> None:
    """A pass that deliberately sends no card (backlog) settles what it saw."""
    from database import mark_ping_notified

    try:
        await mark_ping_notified(int(ping_id), win=bool(record.get("is_win")))
    except Exception:
        logger.debug("Could not settle ping %s quietly", ping_id, exc_info=True)


def retry_bounds(now: datetime) -> tuple[str, str]:
    """(oldest detection still worth a card, newest one old enough to retry)."""
    since = (now - timedelta(hours=RETRY_WINDOW_HOURS)).replace(microsecond=0).isoformat()
    before = (now - timedelta(seconds=RETRY_GRACE_SECONDS)).replace(microsecond=0).isoformat()
    return since, before


async def retry_owed_notifications(now: Optional[datetime] = None) -> dict[str, int]:
    """One pass of the outbox: expire what is too old, deliver what is owed.

    Stops at the first failed send — the bot is down or throttled, and the rest
    of the batch would fail the same way.
    """
    from database import expire_owed_ping_notifications, list_owed_ping_notifications

    from .bot_notify import ensure_bot_connected, send_admin_bot_message

    stats = {"sent": 0, "expired": 0, "failed": 0}
    if not state.bot_client or not await ensure_bot_connected():
        return stats
    now = now or datetime.now()
    since, before = retry_bounds(now)
    expired = await expire_owed_ping_notifications(before=since)
    if expired:
        stats["expired"] = expired
        await record_app_event("WARNING", "notifications", "Owed ping cards expired", {"count": expired})
        await send_admin_bot_message(
            f"⚠️ Не доставлено уведомлений об упоминаниях: {expired}. "
            "Они старше суток — бот был недоступен. Все они в ленте: /recent"
        )
    for row in await list_owed_ping_notifications(since=since, before=before, limit=RETRY_BATCH):
        ping_id = int(row["id"])
        if ping_id in _inflight:
            continue  # the pipeline is sending it right now
        settled, sent = await deliver_owner_card(ping_id, late=True)
        if sent is not None:
            stats["sent"] += 1
        elif not settled:
            stats["failed"] += 1
            break
    if stats["sent"]:
        logger.info("notify-retry delivered %s owed ping cards", stats["sent"])
        await record_app_event("INFO", "notifications", "Owed ping cards delivered", {"count": stats["sent"]})
    return stats


async def notify_retry_loop() -> None:
    while True:
        try:
            state.heartbeat("notify-retry")
            await retry_owed_notifications()
        except Exception:
            logger.exception("notify-retry pass failed")
        await asyncio.sleep(RETRY_POLL_SECONDS)
