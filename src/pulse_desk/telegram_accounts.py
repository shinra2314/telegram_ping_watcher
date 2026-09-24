"""Telegram user-account lifecycle: connect, watch, reconnect, disconnect."""
from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from telethon import TelegramClient, events

from . import check_claimer
from .app_ctx import (
    API_HASH,
    API_ID,
    PENDING_AUTH_TTL_SECONDS,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_RECONNECT_BASE_SECONDS,
    TELEGRAM_RECONNECT_JITTER_SECONDS,
    TELEGRAM_RECONNECT_MAX_SECONDS,
    TELEGRAM_RETRY_DELAY_SECONDS,
    handler_errors,
    logger,
    settings,
    state,
)
from .common import flood_wait_seconds, now_iso, record_app_event, start_background_task
from .resilience import guard
from .telegram_errors import AUTH_KEY_DUPLICATED_STATUS, auth_key_duplicated_message, is_auth_key_duplicated
from .telegram_reconnect import reconnect_delay_seconds as calculate_reconnect_delay_seconds

try:
    from telethon.errors import FloodWaitError
except ImportError:  # pragma: no cover
    FloodWaitError = Exception


def deletion_key(chat_id: Any, message_ids: list[int], session_name: str) -> str:
    """Dedupe key for one deletion event.

    A channel or supergroup deletion reaches every account in the chat, and one
    of them is enough. An event without a chat (private chats, basic groups) is
    numbered in each account's own sequence, so it is keyed by the session too.
    """
    digest = hashlib.blake2b(",".join(map(str, sorted(message_ids))).encode(), digest_size=8).hexdigest()
    return f"del:{chat_id if chat_id else 'nochat:' + session_name}:{digest}"


async def on_messages_deleted(session_name: str, chat_id: Any, deleted_ids: Any) -> int:
    """Soft-delete/drop what we stored of a deletion event; how many pings it touched.

    One database call per event, not one write per deleted id per account: that
    was 400 competing write transactions for a 50-message cleanup in a channel
    eight of our accounts sit in, and the source of the 24.09 lock storms.
    """
    from database import delete_pings

    ids = [value for value in deleted_ids or [] if isinstance(value, int) and not isinstance(value, bool)]
    if not ids or not state.remember_message(deletion_key(chat_id, ids, session_name)):
        return 0
    return await delete_pings(chat_id or None, ids)


def live_message_key(message: Any, session_name: str, suffix: Any = "") -> str:
    """Dedupe key for a message every connected account receives.

    Shared by all accounts, so a channel post is processed once — except when
    Telegram flags it ``mentioned`` for this account: that flag is per account,
    and the first account to see a reply to another one would otherwise
    swallow it before the account it mentions got a look.
    """
    key = f"{getattr(message, 'chat_id', None)}:{getattr(message, 'id', None)}"
    if suffix:
        key += f":{suffix}"
    if getattr(message, "mentioned", False):
        key += f"@{session_name}"
    return key


def mark_account_cooldown(session_name: str, seconds: int) -> None:
    if not session_name or seconds <= 0:
        return
    until = datetime.now() + timedelta(seconds=seconds)
    state.account_cooldown_until[session_name] = until
    account = state.accounts_state.get(session_name)
    if account is not None:
        account["cooldown_until"] = until.isoformat(timespec="seconds")


def account_in_cooldown(session_name: str) -> bool:
    until = state.account_cooldown_until.get(session_name)
    if not until:
        return False
    if datetime.now() >= until:
        state.account_cooldown_until.pop(session_name, None)
        account = state.accounts_state.get(session_name)
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


async def mark_auth_key_duplicated(session_name: str, client: Optional[TelegramClient], exc: BaseException) -> None:
    message = auth_key_duplicated_message(session_name)
    account = state.accounts_state.setdefault(session_name, {"session_name": session_name})
    user_id = account.get("user_id")
    if user_id in state.connected_user_ids:
        state.connected_user_ids.discard(user_id)
    account.update(
        {
            "status": AUTH_KEY_DUPLICATED_STATUS,
            "last_error": message,
            "disconnected_at": now_iso(),
            "manual_disconnect": True,
        }
    )
    if client and client in state.clients:
        state.clients.remove(client)
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
    known_names = list(dict.fromkeys([*state.session_names, *settings.discover_sessions(), *state.accounts_state.keys()]))
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
        if client in state.clients:
            state.clients.remove(client)
        account = state.accounts_state.setdefault(session_name, {"session_name": session_name})
        if account.get("status") == AUTH_KEY_DUPLICATED_STATUS:
            return
        user_id = account.get("user_id")
        if user_id in state.connected_user_ids:
            state.connected_user_ids.discard(user_id)
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
    from .ping_pipeline import process_ping_message

    if not API_ID or not API_HASH:
        logger.warning("Telegram API credentials are missing.")
        return

    clean_name = session_name.replace(".session", "")
    if clean_name in state.accounts_state and state.accounts_state[clean_name].get("status") == "online":
        return

    account = state.accounts_state.setdefault(clean_name, {"session_name": clean_name})
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
        if me.id in state.connected_user_ids:
            account.update({"status": "duplicate", "user_id": me.id, "username": me.username})
            await client.disconnect()
            return

        state.connected_user_ids.add(me.id)
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
        @guard("new-message", handler_errors, clean_name)
        async def handler(event):
            # Proof the account still receives updates (account_health: "silent").
            account["last_update_at"] = now_iso()
            if state.bot_id and event.sender_id == state.bot_id:
                return
            # Before the shared dedupe: each account's own view of a check post
            # counts (`out`, the addressee); the claimer picks the one that presses.
            check_claimer.on_message(client, clean_name, account, event.message)
            if not state.remember_message(live_message_key(event.message, clean_name)):
                return
            await process_ping_message(client, event.message, account_label=account.get("display", clean_name),
                                       account_username=account.get("username") or "", notify=True)

        @client.on(events.MessageEdited())
        @guard("edited-message", handler_errors, clean_name)
        async def edit_handler(event):
            account["last_update_at"] = now_iso()
            if state.bot_id and event.sender_id == state.bot_id:
                return
            check_claimer.on_message(client, clean_name, account, event.message)
            edit_date = getattr(event.message, "edit_date", None) or getattr(event.message, "date", None) or ""
            if not state.remember_message("edit:" + live_message_key(event.message, clean_name, edit_date)):
                return
            await process_ping_message(
                client,
                event.message,
                account_label=account.get("display", clean_name),
                account_username=account.get("username") or "",
                notify=True,
                source="telegram-edit",
            )

        @client.on(events.MessageDeleted())
        @guard("deleted-messages", handler_errors, clean_name)
        async def delete_handler(event):
            await on_messages_deleted(clean_name, event.chat_id, event.deleted_ids)

        state.clients.append(client)
        start_background_task(f"telegram-watch:{clean_name}", monitor_client_disconnect(client, clean_name))
        start_background_task(f"check-warmup:{clean_name}", check_claimer.warm_up(client, clean_name))
        logger.info("Account connected: %s", account.get("display", clean_name))
    except FloodWaitError as exc:
        # Capped like every other flood wait: a raw FLOOD_WAIT_86400 here parked
        # the account start for a day with nothing to show for it.
        wait = flood_wait_seconds(exc.seconds)
        account.update({"status": "rate_limited", "last_error": f"Flood wait {exc.seconds}s",
                        "status_since": now_iso()})
        await asyncio.sleep(wait)
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
    for client in list(state.clients):
        if getattr(client, "_session_name_custom", "") == session_name:
            state.accounts_state.setdefault(session_name, {"session_name": session_name}).update({"manual_disconnect": True})
            try:
                me = await client.get_me()
                state.connected_user_ids.discard(me.id)
            except Exception:
                logger.debug("Could not get account id while disconnecting %s", session_name, exc_info=True)
            await client.disconnect()
            if client in state.clients:
                state.clients.remove(client)
            state.accounts_state.setdefault(session_name, {"session_name": session_name})["status"] = "offline"
            return True
    return False


async def restart_monitoring() -> dict[str, Any]:
    """Disconnect every Telegram user-client and reconnect from the discovered
    sessions. Re-establishes monitoring in-process — it does NOT reload code or
    restart the Python process. Picks up newly added/removed ``.session`` files;
    the auto-scan loop resumes against the fresh clients on its next cycle.
    """
    for client in list(state.clients):
        name = getattr(client, "_session_name_custom", "")
        if name:
            await disconnect_account(name)
    state.session_names = settings.discover_sessions()
    for name in state.session_names:
        start_background_task(f"telegram-start:{name}", start_client(name))
    await record_app_event("INFO", "telegram", "Monitoring restarted", {"sessions": len(state.session_names)})
    return {"status": "ok", "restarted": len(state.session_names), "sessions": state.session_names}
