"""Outbound Telegram-bot notifications (admin messages, member broadcasts)."""
from __future__ import annotations

import asyncio
import secrets as secrets_module
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from telethon import Button

from .app_ctx import ADMIN_ID, BASE_DIR, logger, state
from .bot_permissions import permission_delay_minutes
from .bot_prefs import filter_broadcast_members, notification_type_of, parse_member_prefs
from .common import flood_wait_seconds, record_app_event
from .telegram_errors import is_unreachable_recipient
from .watch_settings import (
    default_notification_settings,
    load_notification_settings,
    notification_matches,
    owner_card_muted,
    should_throttle_notification,
)

try:
    from telethon.errors import FloodWaitError
except ImportError:  # pragma: no cover
    FloodWaitError = Exception

BOT_ASSETS_DIR = BASE_DIR / "assets" / "bot"


class BotOffline(Exception):
    """The bot client is not connected, so a send could not even be attempted.

    Distinct from a failed send: the recipient did nothing wrong and the queue
    must not spend a retry attempt on it. ``pending_send_loop`` catches this and
    leaves the row due for the next poll.
    """


class RecipientUnreachable(Exception):
    """The recipient cannot receive messages (blocked the bot, never /start-ed).

    Retrying cannot fix it, so the caller drops the row immediately instead of
    burning the full attempt budget one 20-second poll at a time.
    """


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
    if record.get("is_win"):
        name = "notify_win.png"
    elif record.get("is_giveaway"):
        name = "notify_giveaway.png"
    else:
        name = "notify_mention.png"
    path = BOT_ASSETS_DIR / name
    return str(path) if path.exists() else None


def _message_id(sent: Any) -> Optional[int]:
    """Id of a sent message — an album comes back as a list of them."""
    if isinstance(sent, (list, tuple)):
        sent = sent[0] if sent else None
    return int(sent.id) if sent is not None else None


def file_field(file: Any) -> str:
    """Serialise a send's media for the `file_path` column.

    A digest goes out as an album (two cards), so a queued delayed copy has to
    remember more than one path — they are stored newline-separated.
    """
    if not file:
        return ""
    if isinstance(file, (list, tuple)):
        return "\n".join(str(part) for part in file if part)
    return str(file)


def file_from_field(raw: Any) -> Optional[Any]:
    """Inverse of :func:`file_field`: a path, a list of them, or None."""
    parts = [line for line in str(raw or "").split("\n") if line]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else parts


UNREACHABLE_PEER_COOLDOWN_SECONDS = 3600.0
# peer -> monotonic deadline until which sends to it are skipped outright.
_unreachable_peers: dict[str, float] = {}


def _peer_key(peer: Any) -> str:
    return str(peer).strip().lower()


def mark_peer_unreachable(peer: Any, now: Optional[float] = None) -> None:
    base = time.monotonic() if now is None else now
    _unreachable_peers[_peer_key(peer)] = base + UNREACHABLE_PEER_COOLDOWN_SECONDS


def peer_is_unreachable(peer: Any, now: Optional[float] = None) -> bool:
    deadline = _unreachable_peers.get(_peer_key(peer))
    if deadline is None:
        return False
    base = time.monotonic() if now is None else now
    if base >= deadline:
        _unreachable_peers.pop(_peer_key(peer), None)
        return False
    return True


def clear_peer_unreachable(peer: Any) -> None:
    _unreachable_peers.pop(_peer_key(peer), None)


def reset_unreachable_peers() -> None:
    _unreachable_peers.clear()


def _resolve_peer(target: Any) -> Any:
    """Normalise a notify target: numeric strings → int chat id, else pass through
    (a @username / channel that Telethon resolves)."""
    if isinstance(target, str):
        stripped = target.strip()
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        return stripped
    return target


async def _send_bot_message(peer: Any, message: str, *, buttons: Optional[list[list[Button]]] = None,
                            file: Optional[Any] = None, silent: bool = False) -> Optional[Any]:
    """Send a single bot message to an arbitrary peer with flood-wait retries.

    `file` is a path, or a list of them for an album (the digest's two cards).
    `silent` delivers it without a sound (a muted ping card).
    Returns the sent Telethon message (callers may need ``.id``) or None."""
    if not state.bot_client or peer in (None, ""):
        return None
    if peer_is_unreachable(peer):
        # Known-dead recipient (never pressed /start, blocked, wrong username):
        # skip it instead of paying a round trip per ping until the cooldown ends.
        logger.debug("Skipping bot message to unreachable peer %s", peer)
        return None
    target = _resolve_peer(peer)
    buttons = buttons or None  # Telethon rejects an empty markup list
    extra = {"silent": True} if silent else {}
    for attempt in range(3):
        try:
            if not await ensure_bot_connected():
                return None
            try:
                sent = await state.bot_client.send_message(target, message, buttons=buttons, link_preview=False, file=file, **extra)
            except FloodWaitError:
                raise
            except Exception:
                if file is None:
                    raise
                # Media upload failed — fall back to plain text once.
                file = None
                sent = await state.bot_client.send_message(target, message, buttons=buttons, link_preview=False, **extra)
            clear_peer_unreachable(peer)
            return sent
        except FloodWaitError as exc:
            wait = flood_wait_seconds(exc.seconds)
            await record_app_event("WARNING", "notifications", "Telegram bot flood wait", {"seconds": exc.seconds})
            await asyncio.sleep(wait)
        except Exception as exc:
            if is_unreachable_recipient(exc):
                # Retries cannot fix this one, and the ping pipeline is waiting.
                mark_peer_unreachable(peer)
                logger.warning("Bot cannot reach %s (%s); muted for %.0f min", peer, exc, UNREACHABLE_PEER_COOLDOWN_SECONDS / 60)
                await record_app_event(
                    "ERROR", "notifications", "Telegram bot recipient unreachable",
                    {"error": str(exc), "peer": str(peer)},
                )
                return None
            logger.warning("Telegram bot notification attempt %s failed: %s", attempt + 1, exc)
            if attempt >= 2:
                await record_app_event("ERROR", "notifications", "Telegram bot notification failed", {"error": str(exc), "peer": str(peer)})
                return None
            await asyncio.sleep(2 * (attempt + 1))
    return None


def member_autoclean_hours(member: Optional[dict]) -> int:
    return int(parse_member_prefs((member or {}).get("notification_prefs")).get("autoclean_hours") or 0)


async def owner_autoclean_hours() -> int:
    """The owner's auto-delete choice (``autoclean`` settings key); 0 when off."""
    from database import get_setting

    from .autoclean import SETTINGS_KEY, owner_hours

    try:
        return owner_hours(await get_setting(SETTINGS_KEY, None))
    except Exception:
        return 0


async def schedule_autoclean(chat_id: Any, sent: Any, kind: str, hours: Any) -> None:
    """Queue a delivered message for deletion when its kind and the choice allow it."""
    from database import schedule_message_deletion

    from .autoclean import delete_at, should_schedule

    message_id = _message_id(sent) if not isinstance(sent, int) else sent
    if message_id is None or not should_schedule(kind, hours):
        return
    try:
        await schedule_message_deletion([(int(chat_id), int(message_id))], delete_at(datetime.now(), hours), kind)
    except Exception:
        logger.debug("Could not schedule auto-delete for %s/%s", chat_id, message_id, exc_info=True)


async def send_admin_bot_message(message: str, *, buttons: Optional[list[list[Button]]] = None,
                                 file: Optional[Any] = None, kind: str = "", want_id: bool = False) -> Any:
    """Message the owner. ``kind`` ("market", "system", "mention") marks it as
    minor, so it is deleted later if the owner switched auto-delete on.

    Returns whether it was sent — or, with ``want_id``, the owner's message id
    (None when the owner got no copy), for a card that is edited later."""
    if not ADMIN_ID:
        return None if want_id else False
    owner, delegate = await vacation_routing(kind)
    if delegate:
        # Vacation delegate gets the owner's copy (see vacation.py).
        await _send_bot_message(delegate, message, buttons=buttons, file=file)
    if not owner:
        return None if want_id else True
    sent = await _send_bot_message(ADMIN_ID, message, buttons=buttons, file=file)
    if sent is not None and kind:
        await schedule_autoclean(ADMIN_ID, sent, kind, await owner_autoclean_hours())
    if want_id:
        return getattr(sent, "id", None) if sent is not None else None
    return sent is not None


async def vacation_routing(kind: str) -> tuple[bool, Optional[int]]:
    """(owner still receives it, delegate chat id or None) under vacation mode."""
    from database import get_setting

    from .vacation import SETTINGS_KEY, delegate_receives, normalize, owner_receives

    try:
        cfg = normalize(await get_setting(SETTINGS_KEY, None))
    except Exception:
        return True, None
    now = datetime.now()
    return owner_receives(kind, cfg, now), delegate_receives(kind, cfg, now)


async def send_member_bot_message(tg_id: int, message: str, *, buttons: Optional[list[list[Button]]] = None) -> bool:
    """One message to one member, outside the broadcast fanout.

    The fanout picks its audience from notification grants; this is for a message
    addressed to a single person by name (a salary that just got paid out), where
    there is no audience to filter.
    """
    if not tg_id:
        return False
    return await _send_bot_message(int(tg_id), message, buttons=buttons) is not None


def _first_button_url(buttons: Optional[list[list[Button]]]) -> str:
    """Pull the first URL out of a keyboard so a delayed copy can rebuild it."""
    for row in buttons or []:
        for button in row:
            url = getattr(button, "url", None)
            if url:
                return str(url)
    return ""


async def deliver_pending_send(row: dict[str, Any]) -> Optional[int]:
    """Send one queued delayed copy. Returns its message id, None on failure.

    Raises, so the caller can tell the three outcomes apart instead of spending
    a retry attempt on all of them:

    * ``BotOffline`` — nothing was attempted; leave the row due, don't count it.
    * ``RecipientUnreachable`` — retrying cannot help; drop the row now.
    * ``FloodWaitError`` — back off and keep the row pending.
    """
    if not await ensure_bot_connected():
        raise BotOffline("bot client is not connected")
    tg_id = int(row["tg_id"])
    if peer_is_unreachable(tg_id):
        # Muted by an earlier failure — skip the round trip until the cooldown ends.
        raise RecipientUnreachable(f"{tg_id} is muted until the unreachable cooldown ends")
    link = str(row.get("link") or "")
    buttons = [[Button.url("Открыть в Telegram", link)]] if link else None
    file = file_from_field(row.get("file_path"))
    message = row.get("message") or ""
    try:
        try:
            sent = await state.bot_client.send_message(tg_id, message, buttons=buttons, link_preview=False, file=file)
        except FloodWaitError:
            raise
        except Exception:
            if file is None:
                raise
            # Media upload failed — fall back to plain text once.
            sent = await state.bot_client.send_message(tg_id, message, buttons=buttons, link_preview=False)
    except FloodWaitError:
        raise
    except Exception as exc:
        if is_unreachable_recipient(exc):
            mark_peer_unreachable(tg_id)
            raise RecipientUnreachable(str(exc)) from exc
        raise
    clear_peer_unreachable(tg_id)
    return _message_id(sent)


async def broadcast_member_notification(
    message: str,
    buttons: Optional[list[list[Button]]] = None,
    file: Optional[Any] = None,
    notif_type: str = "mention",
    score: Optional[int] = None,
    premium_only: Optional[bool] = None,
    mentions: Any = None,
    token: str = "",
) -> list[tuple[int, int]]:
    """Send a notification to viewer members allowed to receive it.

    `mentions` are the tracked usernames the event is about — members whose
    access key is limited to specific accounts only get matching events.

    `score` (giveaway candidate score) feeds each member's personal min_score
    filter; `premium_only` splits the audience (True — premium members only,
    False — everyone else, None — all).

    Members whose key carries a `delay_minutes` grant are queued in
    ``bot_pending_sends`` under `token` instead of being messaged now; the
    caller can count them with ``count_pending_sends(token)``.

    Returns (tg_id, message_id) pairs of the copies delivered immediately, so
    they can be deleted later via the admin's "hide from friends" button.
    """
    from database import list_bot_members, queue_pending_send

    delivered: list[tuple[int, int]] = []
    try:
        members = await list_bot_members()
    except Exception:
        logger.exception("Failed to load bot members for broadcast")
        return delivered
    admin_ids = {int(ADMIN_ID)} if ADMIN_ID else set()
    # Owner is excluded here — already notified via send_admin_bot_message.
    eligible = filter_broadcast_members(members, notif_type, admin_ids, score=score, premium_only=premium_only, mentions=mentions)

    link = _first_button_url(buttons)
    immediate: list[dict] = []
    for member in eligible:
        minutes = permission_delay_minutes(member.get("permissions"))
        if minutes <= 0:
            immediate.append(member)
            continue
        try:
            send_at = (datetime.now() + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
            await queue_pending_send(
                int(member.get("tg_id")),
                send_at,
                message,
                token=token,
                notif_type=notif_type,
                link=link,
                file_path=file_field(file),
            )
        except Exception as exc:
            logger.warning("Failed to schedule delayed notification for %s: %s", member.get("tg_id"), exc)
            await record_app_event(
                "ERROR", "notifications", "Delayed notification could not be queued",
                {"tg_id": member.get("tg_id"), "error": str(exc)},
            )

    async def requeue(members_left: list[dict], reason: str) -> None:
        """Hand undelivered immediate copies to the outbox instead of dropping them.

        An interruption mid-fanout (bot offline, flood wait) says nothing about
        the members we had not reached yet, so they go into ``bot_pending_sends``
        due now and ``pending_send_loop`` finishes the job.
        """
        if not members_left:
            return
        send_at = datetime.now().replace(microsecond=0).isoformat()
        queued = 0
        for left in members_left:
            try:
                await queue_pending_send(
                    int(left.get("tg_id")), send_at, message, token=token,
                    notif_type=notif_type, link=link, file_path=file_field(file),
                )
                queued += 1
            except Exception as exc:
                logger.warning("Failed to requeue notification for %s: %s", left.get("tg_id"), exc)
        logger.warning("Broadcast interrupted (%s); queued %d of %d remaining", reason, queued, len(members_left))
        await record_app_event(
            "ERROR", "notifications", "Broadcast interrupted; remaining copies queued",
            {"reason": reason, "queued": queued, "remaining": len(members_left)},
        )

    if not state.bot_client:
        # The bot never started (network down at boot). Returning here used to
        # drop every copy; queued, they go out once the bot is up.
        await requeue(immediate, "bot not started")
        return delivered

    for index, member in enumerate(immediate):
        tg_id = member.get("tg_id")
        if peer_is_unreachable(tg_id):
            continue
        try:
            if not await ensure_bot_connected():
                # Returning here used to drop every remaining member silently.
                await requeue(immediate[index:], "bot offline")
                break
            try:
                sent = await state.bot_client.send_message(int(tg_id), message, buttons=buttons, link_preview=False, file=file)
            except FloodWaitError:
                raise
            except Exception:
                if file is None:
                    raise
                sent = await state.bot_client.send_message(int(tg_id), message, buttons=buttons, link_preview=False)
            clear_peer_unreachable(tg_id)
            message_id = _message_id(sent)
            if message_id is not None:
                delivered.append((int(tg_id), message_id))
                await schedule_autoclean(tg_id, message_id, notif_type,
                                         member_autoclean_hours(member))
        except FloodWaitError as exc:
            # The wait is ours, not this member's. Sleeping here would hold the
            # ping pipeline for up to 30 minutes and still lose this recipient,
            # so hand the rest of the fanout to the outbox and get out.
            await requeue(immediate[index:], f"flood wait {exc.seconds}s")
            break
        except Exception as exc:
            if is_unreachable_recipient(exc):
                mark_peer_unreachable(tg_id)
            logger.warning("Failed to notify bot member %s: %s", tg_id, exc)
    return delivered


def build_ping_card(
    record: dict[str, Any],
    candidate: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[str], Optional[str]]:
    """Build the notification card. Returns (text, link_or_none, header_image_or_none)."""
    title = "🔔 Новое упоминание"
    if record.get("chat_type") == "group":
        # A group or a channel's comment thread — often a reply, with no @ in it.
        title = "💬 Упоминание в чате"
    if record.get("is_win"):
        title = "🏆 Похоже на победу в розыгрыше"
    elif record.get("is_giveaway"):
        title = "🎁 Найден розыгрыш"
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
        f"🏷 Упоминания: {mentions}"
        f"{candidate_line}\n\n"
        f"{(record.get('text') or '')[:excerpt_limit]}"
    )
    link = record.get("link")
    if not link or link.startswith("нет "):
        link = None
    return msg, link, header_image


def _member_card_buttons(link: Optional[str], ping_id: Optional[int], notif_type: str) -> Optional[list[list[Button]]]:
    """Buttons on a member's broadcast copy: link + engagement for giveaways."""
    buttons: list[list[Button]] = []
    if link:
        buttons.append([Button.url("Открыть в Telegram", link)])
    if ping_id and notif_type == "giveaway":
        buttons.append([
            Button.inline("✅ Участвую", data=f"bcm:in:{ping_id}"),
            Button.inline("⏭ Пропустил", data=f"bcm:skip:{ping_id}"),
        ])
    return buttons or None


def _admin_card_buttons(link: Optional[str], ping_id: Optional[int], chat_type: str = "",
                        panel: Optional[str] = None) -> list[list[Button]]:
    """``panel`` — the panel screen to deep-link (``giveaway`` / ``ping``), or
    None for no «🛰 В панели» row (see ``panel_card_screen``)."""
    buttons: list[list[Button]] = []
    if link:
        buttons.append([Button.url("🔗 Открыть в Telegram", link)])
    if ping_id:
        buttons.append([
            Button.inline("⭐ В избранное", data=f"fav_{ping_id}"),
            Button.inline("✓ Прочитано", data=f"read_{ping_id}"),
        ])
        if chat_type == "group":
            # Groups are where a nickname in passing is not news (bot/sections/ignored.py).
            buttons.append([Button.inline("🔇 Не следить за чатом", data=f"igc:add:{ping_id}")])
        if panel:
            from .bot.keyboards import webapp_row

            # Empty while the tunnel is down: the card is then exactly as before.
            row = webapp_row("🛰 В панели", f"/app?s={panel}&id={int(ping_id)}")
            if row:
                buttons.append(row)
    return buttons


async def panel_card_screen(record: dict[str, Any], settings: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Which panel screen the owner's card links to, or None when the owner
    has not switched «🛰 В панели» on (notification setting ``panel_button``).

    Off by default: the panel was pulled once over a Telegram client crash,
    and a web_app button on every card is the widest exposure it can have.
    """
    try:
        notif = settings if settings is not None else await load_notification_settings()
    except Exception:
        return None
    if not notif.get("panel_button"):
        return None
    return "giveaway" if (record.get("is_win") or record.get("is_giveaway")) else "ping"


async def execute_pending_broadcast(row: dict[str, Any]) -> tuple[int, Optional[str]]:
    """Broadcast an approved/expired pending row to non-premium members
    (premium copies went out at enqueue time under the row's bc_token).

    Returns (delivered_count, hidebc_token_or_none)."""
    from database import count_pending_sends, get_giveaway_candidate, get_ping_by_id, save_broadcast_messages

    link = row.get("link") or None
    ping_id = int(row["ping_id"]) if row.get("ping_id") else None
    notif_type = row.get("notif_type") or "mention"
    score: Optional[int] = None
    if ping_id and notif_type == "giveaway":
        candidate = await get_giveaway_candidate(ping_id)
        if candidate:
            score = int(candidate.get("score") or 0)
    # The pending row does not carry mentions — account-scoped keys need them.
    mentions = None
    if ping_id:
        ping = await get_ping_by_id(ping_id)
        mentions = (ping or {}).get("mentions")
    # Minted before the broadcast: copies held back by a per-key delay are
    # stamped with it as they are queued, so "hide from friends" covers them too.
    token = row.get("bc_token") or secrets_module.token_hex(4)  # bc_token set when premium copies went out
    delivered = await broadcast_member_notification(
        row.get("message") or "",
        _member_card_buttons(link, ping_id, notif_type),
        file=row.get("file_path") or None,
        notif_type=notif_type,
        score=score,
        premium_only=False,
        mentions=mentions,
        token=token,
    )
    if delivered:
        await save_broadcast_messages(token, delivered)
    scheduled = await count_pending_sends(token)
    if not delivered and not scheduled and not row.get("bc_token"):
        return 0, None
    return len(delivered) + scheduled, token


async def edit_pending_admin_card(
    row: dict[str, Any],
    footer: str,
    token: Optional[str] = None,
    delivered_count: int = 0,
) -> None:
    """Update the admin's moderation card in place after a decision/auto-send."""
    admin_message_id = row.get("admin_message_id")
    if not state.bot_client or not ADMIN_ID or not admin_message_id:
        return
    link = row.get("link") or None
    ping_id = row.get("ping_id")
    buttons = _admin_card_buttons(link, int(ping_id) if ping_id else None)
    if token:
        buttons.append([Button.inline(f"🙈 Скрыть у друзей ({delivered_count})", data=f"hidebc_{token}")])
    text = f"{row.get('message') or ''}\n\n{footer}"
    try:
        await state.bot_client.edit_message(int(ADMIN_ID), int(admin_message_id), text, buttons=buttons or None, link_preview=False)
    except Exception as exc:
        logger.warning("Failed to edit moderation card %s: %s", row.get("id"), exc)


LATE_CARD_MINUTES = 2


def late_card_footer(detected_at: Any, now: Optional[datetime] = None) -> str:
    """Footer for a card delivered well after its ping was found; '' when on time."""
    try:
        found = datetime.fromisoformat(str(detected_at)).replace(tzinfo=None)
    except (TypeError, ValueError):
        return ""
    now = now or datetime.now()
    if (now - found).total_seconds() < LATE_CARD_MINUTES * 60:
        return ""
    when = f"{found:%H:%M}" if found.date() == now.date() else f"{found:%d.%m %H:%M}"
    return f"\n\n⏳ С опозданием: найдено в {when}"


async def owner_card_silent(record: dict[str, Any], settings: dict[str, Any], *, throttled: bool = False) -> bool:
    """A muted ping card still reaches the owner — without a sound.

    Quiet hours, the notification filters and rules, vacation mode and the
    repeat throttle all used to *drop* the owner's card, and a dropped card is
    gone for good: the ping is already stored, so no later pass sends it.
    """
    if throttled or owner_card_muted(record, settings):
        return True
    owner_loud, _ = await vacation_routing(notification_type_of(record))
    return not owner_loud


async def send_owner_ping_card(
    record: dict[str, Any],
    ping_id: Optional[int] = None,
    *,
    silent: bool = False,
    late_since: Any = None,
) -> Optional[Any]:
    """The owner's card for one ping. Returns the sent message or None.

    Never filtered — see ``owner_card_silent``. ``late_since`` (the ping's
    detection time) adds a "delivered late" footer to a retried card.
    """
    if not ADMIN_ID:
        return None
    msg, link, header_image = build_ping_card(record)
    if late_since:
        msg += late_card_footer(late_since)
    buttons = _admin_card_buttons(link, ping_id, record.get("chat_type") or "", await panel_card_screen(record))
    sent = await _send_bot_message(ADMIN_ID, msg, buttons=buttons, file=header_image, silent=silent)
    if sent is not None:
        # Only a plain mention is minor; wins and giveaways are never auto-deleted.
        await schedule_autoclean(ADMIN_ID, sent, notification_type_of(record), await owner_autoclean_hours())
    return sent


async def _edit_owner_card(owner_message: Any, text: str, buttons: list[list[Button]]) -> None:
    message_id = _message_id(owner_message)
    if not state.bot_client or not ADMIN_ID or message_id is None:
        return
    try:
        await state.bot_client.edit_message(int(ADMIN_ID), int(message_id), text, buttons=buttons or None, link_preview=False)
    except Exception as exc:
        logger.warning("Failed to update the owner's ping card %s: %s", message_id, exc)


async def broadcast_ping(
    record: dict[str, Any],
    ping_id: Optional[int] = None,
    *,
    settings: Optional[dict[str, Any]] = None,
    throttled: bool = False,
    owner_message: Any = None,
) -> None:
    """The once-per-ping part of a notification, after the owner's card is out.

    The vacation delegate's copy, the member broadcast (or its moderation hold),
    then the owner's card is edited to carry what that produced: the giveaway
    score, "🙈 Скрыть у друзей", the moderation buttons. The owner no longer
    waits for every member's copy before seeing their own.
    """
    from database import (
        count_pending_sends,
        create_pending_broadcast,
        get_giveaway_candidate,
        save_broadcast_messages,
        set_pending_broadcast_admin_message,
    )

    settings = settings if settings is not None else await load_notification_settings()
    notif_type = notification_type_of(record)
    candidate = await get_giveaway_candidate(int(ping_id)) if ping_id and record.get("is_giveaway") else None
    msg, link, header_image = build_ping_card(record, candidate=candidate)
    owner_buttons = _admin_card_buttons(link, ping_id, record.get("chat_type") or "",
                                        await panel_card_screen(record, settings))
    owner_text = msg
    changed = candidate is not None

    _, delegate = await vacation_routing(notif_type)
    if delegate:
        # Vacation delegate gets the owner's copy (see vacation.py).
        await _send_bot_message(delegate, msg, buttons=owner_buttons, file=header_image)

    if throttled:
        await record_app_event("INFO", "notifications", "Similar notification suppressed",
                               {"chat": record.get("chat"), "mentions": record.get("mentions")})
    elif notification_matches(record, settings):
        score = int(candidate.get("score") or 0) if candidate else None
        member_buttons = _member_card_buttons(link, ping_id, notif_type)
        if str(settings.get("moderation_mode", "auto")) == "moderated" and ADMIN_ID:
            # Hold the member broadcast until the owner approves (or the timeout
            # fires). Premium members are the exception — they get it right away.
            bc_token = secrets_module.token_hex(4)
            premium_delivered = await broadcast_member_notification(
                msg, member_buttons, file=header_image, notif_type=notif_type, score=score,
                premium_only=True, mentions=record.get("mentions"), token=bc_token,
            )
            if premium_delivered:
                await save_broadcast_messages(bc_token, premium_delivered)
            elif not await count_pending_sends(bc_token):
                bc_token = ""
            timeout = int(settings.get("approval_timeout_seconds", 300) or 300)
            expires_at = (datetime.now() + timedelta(seconds=timeout)).replace(microsecond=0).isoformat()
            pb_id = await create_pending_broadcast(
                ping_id, notif_type, msg, link=link or "", file_path=header_image or "",
                expires_at=expires_at, bc_token=bc_token,
            )
            owner_buttons.append([
                Button.inline("📣 Разослать", data=f"bc:ok:{pb_id}"),
                Button.inline("🚫 Отклонить", data=f"bc:no:{pb_id}"),
            ])
            footer = f"🛡 Рассылка друзьям на модерации · авто-отправка в {expires_at[11:16]}"
            if premium_delivered:
                footer += f"\n⚡ Премиум уже получили: {len(premium_delivered)}"
            owner_text = msg + "\n\n" + footer
            changed = True
            owner_message_id = _message_id(owner_message) if owner_message is not None else None
            if owner_message_id is not None:
                await set_pending_broadcast_admin_message(pb_id, owner_message_id)
            else:
                # The retried card will be a plain one; the hold still auto-sends.
                logger.error("Owner card missing for pending broadcast %s", pb_id)
        else:
            token = secrets_module.token_hex(4)
            delivered = await broadcast_member_notification(
                msg, member_buttons, file=header_image, notif_type=notif_type, score=score,
                mentions=record.get("mentions"), token=token,
            )
            if delivered:
                await save_broadcast_messages(token, delivered)
            scheduled = await count_pending_sends(token)
            if delivered or scheduled:
                label = f"🙈 Скрыть у друзей ({len(delivered) + scheduled})"
                if scheduled and not delivered:
                    label = f"🙈 Отменить отправку ({scheduled})"
                owner_buttons.append([Button.inline(label, data=f"hidebc_{token}")])
                changed = True

    if owner_message is not None and changed:
        await _edit_owner_card(owner_message, owner_text, owner_buttons)


async def send_bot_notification(record: dict[str, Any], ping_id: Optional[int] = None) -> bool:
    """Owner's card first, then the member broadcast. True when the owner got it.

    The pipeline goes through ``ping_notify``, which adds the outbox bookkeeping
    around these two steps; this stays as the plain one-call form.
    """
    try:
        settings = await load_notification_settings()
    except Exception:
        logger.exception("Could not load notification settings; using defaults")
        settings = default_notification_settings()
    throttled = should_throttle_notification(record, int(settings.get("cooldown_seconds", 120) or 0))
    sent = None
    try:
        silent = await owner_card_silent(record, settings, throttled=throttled)
        sent = await send_owner_ping_card(record, ping_id, silent=silent)
        if sent is None:
            logger.error("Failed to send the owner's ping card after retries")
    except Exception:
        logger.exception("Failed to send the owner's ping card")
    try:
        await broadcast_ping(record, ping_id, settings=settings, throttled=throttled, owner_message=sent)
    except Exception:
        logger.exception("Failed to broadcast ping notification")
    return sent is not None
