"""Outbound Telegram-bot notifications (admin messages, member broadcasts)."""
from __future__ import annotations

import asyncio
import secrets as secrets_module
from typing import Any, Optional

from telethon import Button

from .app_ctx import ADMIN_ID, BASE_DIR, CHECK_NOTIFY_TARGET, logger, state
from .bot_prefs import filter_broadcast_members, notification_type_of
from .common import flood_wait_seconds, record_app_event
from .watch_settings import is_quiet_time, load_notification_settings, notification_matches, should_throttle_notification

try:
    from telethon.errors import FloodWaitError
except ImportError:  # pragma: no cover
    FloodWaitError = Exception

BOT_ASSETS_DIR = BASE_DIR / "assets" / "bot"


async def ensure_bot_connected() -> bool:
    if not state.bot_client:
        return False
    try:
        if not state.bot_client.is_connected():
            await state.bot_client.connect()
        return True
    except Exception:
        logger.exception("Telegram bot reconnect failed")
        await record_app_event("ERROR", "notifications", "Telegram bot reconnect failed")
        return False


def notification_image_path(record: dict[str, Any]) -> Optional[str]:
    """Pick the branded header image for a ping notification, if present on disk."""
    if record.get("is_check"):
        name = "notify_check.png"
    elif record.get("is_win"):
        name = "notify_win.png"
    elif record.get("is_giveaway"):
        name = "notify_giveaway.png"
    else:
        name = "notify_mention.png"
    path = BOT_ASSETS_DIR / name
    return str(path) if path.exists() else None


def _resolve_peer(target: Any) -> Any:
    """Normalise a notify target: numeric strings → int chat id, else pass through
    (a @username / channel that Telethon resolves)."""
    if isinstance(target, str):
        stripped = target.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return stripped
    return target


async def _send_bot_message(peer: Any, message: str, *, buttons: Optional[list[list[Button]]] = None, file: Optional[str] = None) -> bool:
    """Send a single bot message to an arbitrary peer with flood-wait retries."""
    if not state.bot_client or peer in (None, ""):
        return False
    target = _resolve_peer(peer)
    buttons = buttons or None  # Telethon rejects an empty markup list
    for attempt in range(3):
        try:
            if not await ensure_bot_connected():
                return False
            try:
                await state.bot_client.send_message(target, message, buttons=buttons, link_preview=False, file=file)
            except FloodWaitError:
                raise
            except Exception:
                if file is None:
                    raise
                # Media upload failed — fall back to plain text once.
                file = None
                await state.bot_client.send_message(target, message, buttons=buttons, link_preview=False)
            return True
        except FloodWaitError as exc:
            wait = flood_wait_seconds(exc.seconds)
            await record_app_event("WARNING", "notifications", "Telegram bot flood wait", {"seconds": exc.seconds})
            await asyncio.sleep(wait)
        except Exception as exc:
            logger.warning("Telegram bot notification attempt %s failed: %s", attempt + 1, exc)
            if attempt >= 2:
                await record_app_event("ERROR", "notifications", "Telegram bot notification failed", {"error": str(exc), "peer": str(peer)})
                return False
            await asyncio.sleep(2 * (attempt + 1))
    return False


async def send_admin_bot_message(message: str, *, buttons: Optional[list[list[Button]]] = None, file: Optional[str] = None) -> bool:
    if not ADMIN_ID:
        return False
    return await _send_bot_message(ADMIN_ID, message, buttons=buttons, file=file)


async def broadcast_member_notification(
    message: str,
    buttons: Optional[list[list[Button]]] = None,
    file: Optional[str] = None,
    notif_type: str = "mention",
) -> list[tuple[int, int]]:
    """Send a notification to viewer members whose preferences allow `notif_type`.

    Returns (tg_id, message_id) pairs of the delivered copies so they can be
    deleted later via the admin's "hide from friends" button.
    """
    from database import list_bot_members

    delivered: list[tuple[int, int]] = []
    if not state.bot_client:
        return delivered
    try:
        members = await list_bot_members()
    except Exception:
        logger.exception("Failed to load bot members for broadcast")
        return delivered
    admin_ids = {int(ADMIN_ID)} if ADMIN_ID else set()
    # Owner is excluded here — already notified via send_admin_bot_message.
    for member in filter_broadcast_members(members, notif_type, admin_ids):
        tg_id = member.get("tg_id")
        try:
            if not await ensure_bot_connected():
                return delivered
            try:
                sent = await state.bot_client.send_message(int(tg_id), message, buttons=buttons, link_preview=False, file=file)
            except FloodWaitError:
                raise
            except Exception:
                if file is None:
                    raise
                sent = await state.bot_client.send_message(int(tg_id), message, buttons=buttons, link_preview=False)
            delivered.append((int(tg_id), int(sent.id)))
        except FloodWaitError as exc:
            await asyncio.sleep(flood_wait_seconds(exc.seconds))
        except Exception as exc:
            logger.warning("Failed to notify bot member %s: %s", tg_id, exc)
    return delivered


async def send_check_notification(record: dict[str, Any], ping_id: Optional[int] = None) -> None:
    """Alert the single configured check target about a detected check/multicheck.

    Checks go ONLY to ``CHECK_NOTIFY_TARGET`` (default ``@w3v8f0rm``); they are
    never broadcast to opted-in members. If the target is unset it falls back to
    the admin. Unlike ``send_bot_notification`` this bypasses the username/keyword
    match filters (checks rarely satisfy them) but still honours the global on/off
    switch and quiet hours.

    Note: a Telegram bot can only deliver to a *user* who has pressed /start on it.
    If the target is a personal account that never started the bot, the send fails
    and is logged.
    """
    if not state.bot_client:
        return
    try:
        settings = await load_notification_settings()
        if not settings.get("enabled", True) or is_quiet_time(settings):
            return
        is_multi = "мультичек" in (record.get("text") or "").lower()
        title = "💸 Найден мультичек" if is_multi else "💸 Найден чек"
        header_image = notification_image_path(record)
        excerpt_limit = 600 if header_image else 800
        msg = (
            f"**{title}**\n"
            "━━━━━━━━━━━━━━━\n"
            f"💬 Чат: `{record.get('chat', 'unknown')}`\n"
            f"👤 От: {record.get('sender', 'unknown')}\n\n"
            f"{(record.get('text') or '')[:excerpt_limit]}"
        )
        link = record.get("link")
        has_link = bool(link) and not link.startswith("нет ")
        buttons: list[list[Button]] = []
        if has_link:
            buttons.append([Button.url("🔗 Открыть в Telegram", link)])
        if ping_id:
            buttons.append([
                Button.inline("⭐ В избранное", data=f"fav_{ping_id}"),
                Button.inline("✓ Прочитано", data=f"read_{ping_id}"),
            ])
        target = CHECK_NOTIFY_TARGET or ADMIN_ID
        if not target:
            logger.error("No check notify target configured (CHECK_NOTIFY_TARGET / ADMIN_ID)")
            return
        sent = await _send_bot_message(target, msg, buttons=buttons, file=header_image)
        if not sent:
            logger.error("Failed to send check notification to %s after retries", target)
    except Exception:
        logger.exception("Failed to send check notification")


async def send_bot_notification(record: dict[str, Any], ping_id: Optional[int] = None, auto_joined: bool = False) -> None:
    from database import get_giveaway_candidate, save_broadcast_messages

    if not state.bot_client:
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
        header_image = notification_image_path(record)
        # Telegram caption limit is 1024 chars — keep the excerpt shorter with media.
        excerpt_limit = 600 if header_image else 800
        msg = (
            f"**{title}**\n"
            "━━━━━━━━━━━━━━━\n"
            f"💬 Чат: `{record.get('chat', 'unknown')}` · _{record.get('chat_type', 'unknown')}_\n"
            f"👤 От: {record.get('sender', 'unknown')}\n"
            f"🏷 Упоминания: {mentions}\n"
            f"🤝 Авто-вступление: {'✅ да' if auto_joined else '❌ нет'}"
            f"{candidate_line}\n\n"
            f"{(record.get('text') or '')[:excerpt_limit]}"
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
        # Mirror notification to viewer members first (read-only, no action buttons),
        # so the admin message can carry a working "hide from friends" button.
        member_buttons: Optional[list[list[Button]]] = None
        if link and not link.startswith("нет "):
            member_buttons = [[Button.url("Открыть в Telegram", link)]]
        delivered = await broadcast_member_notification(msg, member_buttons, file=header_image, notif_type=notification_type_of(record))
        if delivered:
            token = secrets_module.token_hex(4)
            await save_broadcast_messages(token, delivered)
            buttons.append([Button.inline(f"🙈 Скрыть у друзей ({len(delivered)})", data=f"hidebc_{token}")])
        sent = await send_admin_bot_message(msg, buttons=buttons, file=header_image)
        if not sent:
            logger.error("Failed to send bot notification after retries")
    except Exception:
        logger.exception("Failed to send bot notification")
