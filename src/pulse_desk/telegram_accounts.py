"""Telegram user-account lifecycle: connect, watch, reconnect, disconnect."""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException
from telethon import TelegramClient, events

from .app_ctx import (
    API_HASH,
    API_ID,
    PENDING_AUTH_TTL_SECONDS,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_RECONNECT_BASE_SECONDS,
    TELEGRAM_RECONNECT_JITTER_SECONDS,
    TELEGRAM_RECONNECT_MAX_SECONDS,
    TELEGRAM_RETRY_DELAY_SECONDS,
    logger,
    settings,
    state,
)
from .common import now_iso, record_app_event, start_background_task
from .telegram_errors import AUTH_KEY_DUPLICATED_STATUS, auth_key_duplicated_message, is_auth_key_duplicated
from .telegram_reconnect import reconnect_delay_seconds as calculate_reconnect_delay_seconds

try:
    from telethon.errors import FloodWaitError
except ImportError:  # pragma: no cover
    FloodWaitError = Exception


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

    from database import delete_ping, delete_ping_by_message_id

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
        async def handler(event):
            if state.bot_id and event.sender_id == state.bot_id:
                return
            if not state.remember_message(f"{event.chat_id}:{event.message.id}"):
                return
            await process_ping_message(client, event.message, account_label=account.get("display", clean_name), notify=True)

        @client.on(events.MessageEdited())
        async def edit_handler(event):
            if state.bot_id and event.sender_id == state.bot_id:
                return
            edit_date = getattr(event.message, "edit_date", None) or getattr(event.message, "date", None) or ""
            if not state.remember_message(f"edit:{event.chat_id}:{event.message.id}:{edit_date}"):
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

        state.clients.append(client)
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
