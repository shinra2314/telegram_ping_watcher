"""Аккаунты Telegram: состояние и управление, плюс вход по номеру.

Только владелец, и каждое действие — со свежим initData (см. ``common``).
Сборка списка — та же ``collect``, что рисует раздел бота.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from pulse_desk import account_login
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections.accounts import collect
from pulse_desk.common import start_background_task
from pulse_desk.telegram_accounts import (
    account_in_cooldown, disconnect_account, restart_monitoring, start_client,
)

from .common import Caller, admin_caller, fresh_admin

router = APIRouter()

# Поля записи аккаунта, которые не нужны странице (или не должны на неё попасть).
_HIDDEN = {"client", "phone", "auth_requested_at"}


def account_view(account: dict[str, Any]) -> dict[str, Any]:
    name = str(account.get("session_name") or "")
    view = {k: v for k, v in account.items() if k not in _HIDDEN and _jsonable(v)}
    view["cooldown"] = account_in_cooldown(name)
    return view


def _jsonable(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _known(name: str) -> bool:
    return name in state.session_names or name in state.accounts_state


@router.get("/api/app/accounts")
async def accounts(caller: Caller = Depends(admin_caller)) -> dict:
    items = [account_view(a) for a in await collect()]
    return {
        "items": items,
        "online": sum(1 for a in items if a.get("status") == "online"),
        "total": len(items),
        "pending_logins": len(state.pending_auths),
    }


@router.post("/api/app/accounts/{name}/disconnect")
async def disconnect(name: str, caller: Caller = Depends(fresh_admin)) -> dict:
    if not _known(name):
        raise HTTPException(status_code=404, detail="Нет такой сессии")
    return {"ok": await disconnect_account(name), "session_name": name}


@router.post("/api/app/accounts/{name}/reconnect")
async def reconnect(name: str, caller: Caller = Depends(fresh_admin)) -> dict:
    if not _known(name):
        raise HTTPException(status_code=404, detail="Нет такой сессии")
    if any(getattr(c, "_session_name_custom", "") == name for c in state.clients):
        return {"ok": True, "session_name": name, "message": "Уже подключён"}
    state.accounts_state.setdefault(name, {"session_name": name}).update(
        {"manual_disconnect": False, "status": "connecting", "last_error": None})
    start_background_task(f"telegram-start:{name}", start_client(name))
    return {"ok": True, "session_name": name, "message": "Подключаю…"}


@router.post("/api/app/accounts/restart")
async def restart(caller: Caller = Depends(fresh_admin)) -> dict:
    return await restart_monitoring()


class CodeBody(BaseModel):
    phone: str = Field(..., max_length=32)
    session_name: str = Field("", max_length=64)
    force_sms: bool = False


class SignInBody(BaseModel):
    phone: str = Field(..., max_length=32)
    code: str = Field(..., max_length=16)


class PasswordBody(BaseModel):
    phone: str = Field(..., max_length=32)
    password: str = Field(..., max_length=256)


class PhoneBody(BaseModel):
    phone: str = Field(..., max_length=32)


@router.post("/api/app/login/code")
async def login_code(body: CodeBody, caller: Caller = Depends(fresh_admin)) -> dict:
    result = await account_login.request_code(body.phone, body.session_name, body.force_sms)
    return result.as_dict()


@router.post("/api/app/login/sign-in")
async def login_sign_in(body: SignInBody, caller: Caller = Depends(fresh_admin)) -> dict:
    return (await account_login.submit_code(body.phone, body.code)).as_dict()


@router.post("/api/app/login/password")
async def login_password(body: PasswordBody, caller: Caller = Depends(fresh_admin)) -> dict:
    return (await account_login.submit_password(body.phone, body.password)).as_dict()


@router.post("/api/app/login/cancel")
async def login_cancel(body: PhoneBody, caller: Caller = Depends(fresh_admin)) -> dict:
    return (await account_login.cancel(body.phone)).as_dict()
