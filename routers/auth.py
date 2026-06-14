"""Telegram account auth and management endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)

from database import get_account_ping_stats
from pulse_desk.api_models import AuthRequest, SignInRequest
from pulse_desk.app_ctx import API_HASH, API_ID, logger, require_admin, state
from pulse_desk.common import now_iso, record_app_event
from pulse_desk.telegram_accounts import (
    describe_sent_code_type,
    disconnect_account,
    is_pending_auth_expired,
    mark_auth_key_duplicated,
    normalize_auth_session_name,
    start_client,
    telegram_client_for_session,
)
from pulse_desk.telegram_errors import auth_key_duplicated_message, is_auth_key_duplicated

router = APIRouter()


@router.post("/api/auth/send-code", dependencies=[Depends(require_admin)])
async def send_code(data: AuthRequest):
    if not API_ID or not API_HASH:
        raise HTTPException(500, "Telegram API credentials are missing.")
    phone = data.phone.strip()
    session_name = normalize_auth_session_name(data.session_name, phone)
    await disconnect_account(session_name)
    existing = state.pending_auths.pop(phone, None)
    if existing:
        try:
            await existing["client"].disconnect()
        except Exception:
            logger.debug("Failed to close previous pending auth client", exc_info=True)
    account = state.accounts_state.setdefault(session_name, {"session_name": session_name})
    account.update({"status": "auth_requesting", "last_error": None, "manual_disconnect": False})
    client = telegram_client_for_session(session_name)
    try:
        await client.connect()
        result = await client.send_code_request(phone, force_sms=data.force_sms)
        delivery_type, delivery_message = describe_sent_code_type(result)
        state.pending_auths[phone] = {"client": client, "phone_code_hash": result.phone_code_hash, "session_name": session_name, "created_at": datetime.now()}
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
        state.pending_auths.pop(phone, None)
        message = f"Telegram ограничил повторные запросы кода. Подождите {exc.seconds} секунд."
        account.update({"status": "rate_limited", "last_error": message})
        await record_app_event("WARNING", "auth", "Telegram auth code flood wait", {"session_name": session_name, "seconds": exc.seconds})
        return {"status": "error", "message": message}
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            state.pending_auths.pop(phone, None)
            await mark_auth_key_duplicated(session_name, client, exc)
            return {"status": "error", "message": auth_key_duplicated_message(session_name)}
        await client.disconnect()
        state.pending_auths.pop(phone, None)
        account.update({"status": "auth_error", "last_error": str(exc)})
        logger.exception("Auth send-code failed")
        return {"status": "error", "message": str(exc)}


@router.post("/api/auth/sign-in", dependencies=[Depends(require_admin)])
async def sign_in(data: SignInRequest, background_tasks: BackgroundTasks):
    phone = data.phone.strip()
    code = data.code.strip()
    if not code:
        return {
            "status": "error",
            "message": "Введите код из Telegram. Пустой код не отправлен, чтобы не сжигать повторные попытки Telegram.",
        }
    auth_data = state.pending_auths.get(phone)
    if not auth_data:
        raise HTTPException(400, "Нет активной авторизации для этого телефона.")
    if is_pending_auth_expired(auth_data):
        state.pending_auths.pop(phone, None)
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
            state.pending_auths.pop(phone, None)
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
        state.pending_auths.pop(phone, None)
        state.accounts_state.setdefault(session_name, {"session_name": session_name}).update({
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
        state.pending_auths.pop(phone, None)
        if session_name and is_auth_key_duplicated(exc):
            await mark_auth_key_duplicated(session_name, client, exc)
            return {"status": "error", "message": auth_key_duplicated_message(session_name)}
        try:
            await client.disconnect()
        except Exception:
            logger.debug("Failed to close auth client", exc_info=True)
        logger.exception("Auth sign-in failed")
        return {"status": "error", "message": str(exc)}


@router.get("/api/accounts", dependencies=[Depends(require_admin)])
async def get_accounts():
    known = {name: {"session_name": name, "status": "known"} for name in state.session_names}
    known.update(state.accounts_state)
    return list(known.values())


@router.post("/api/accounts/{session_name}/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_account_api(session_name: str):
    disconnected = await disconnect_account(session_name)
    return {"status": "ok" if disconnected else "not_found"}


@router.get("/api/accounts/health", dependencies=[Depends(require_admin)])
async def get_accounts_health():
    ping_stats = await get_account_ping_stats()
    by_sender_id = {str(row.get("sender_id")): row for row in ping_stats if row.get("sender_id") is not None}
    items = []
    known = {name: {"session_name": name, "status": "known"} for name in state.session_names}
    known.update(state.accounts_state)
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
