"""Мои уведомления: что участнику присылать и когда удалять.

То же, что ``pf_*`` в боте, в пределах ключа: типы, которых нет в гранте
(``allowed_pref_keys``), не переключаются. У владельца строки участника нет —
его уведомления настраиваются в ⚙️ бота, поэтому здесь 404, и раздел не рисуется.
Запись — ``fresh_caller``: перехваченная initData не должна сутки менять чужие
настройки.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_bot_member, set_bot_member_prefs
from pulse_desk.autoclean import CHOICES
from pulse_desk.bot_permissions import ALL_NOTIFY, NOTIFY_TYPES, allowed_pref_keys, format_delay, permission_delay_minutes
from pulse_desk.bot_prefs import apply_prefs_patch, parse_member_prefs

from .common import Caller, current_caller, fresh_caller

router = APIRouter()

OWNER_ONLY = "Личные настройки — только у участников"


def payload(prefs: dict, perms: dict) -> dict:
    allowed = allowed_pref_keys(perms)
    return {
        "prefs": prefs,
        "allowed": allowed,
        "types": [{"code": code, "label": label.split(" ", 1)[-1], "hint": hint}
                  for code, (label, hint) in NOTIFY_TYPES.items()],
        "hidden": [code for code in ALL_NOTIFY if code not in allowed],
        "accounts": list(perms.get("accounts") or []),
        "autoclean_choices": list(CHOICES),
        "delay_text": format_delay(permission_delay_minutes(perms)),
    }


async def member_of(caller: Caller) -> dict:
    member = await get_bot_member(caller.tg_id) if not caller.is_admin else None
    if not member:
        raise HTTPException(status_code=404, detail=OWNER_ONLY)
    return member


@router.get("/api/app/prefs")
async def read_prefs(caller: Caller = Depends(current_caller)) -> dict:
    member = await member_of(caller)
    return payload(parse_member_prefs(member.get("notification_prefs")), caller.perms)


class PrefsPatch(BaseModel):
    muted: Optional[bool] = None
    mentions: Optional[bool] = None
    giveaways: Optional[bool] = None
    wins: Optional[bool] = None
    digest: Optional[bool] = None
    min_score: Optional[int] = Field(default=None, ge=0, le=100)
    autoclean_hours: Optional[int] = None


@router.post("/api/app/prefs")
async def write_prefs(body: PrefsPatch, caller: Caller = Depends(fresh_caller)) -> dict:
    member = await member_of(caller)
    current = parse_member_prefs(member.get("notification_prefs"))
    try:
        updated = apply_prefs_patch(current, body.model_dump(), allowed_pref_keys(caller.perms))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Этот тип уведомлений закрыт владельцем") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Такого срока автоудаления нет") from exc
    await set_bot_member_prefs(caller.tg_id, updated)
    return payload(updated, caller.perms)
