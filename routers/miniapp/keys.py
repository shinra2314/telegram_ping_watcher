"""Ключи и участники: вся жизнь ключа и расписание доступа. Только владелец.

То же, что панель ``key:*`` и ``/access`` в боте, но одним экраном: гранты —
галочками, аккаунты — мультивыбором, задержка — ползунком, срок — днями,
окна доступа — днями недели и временем. Инлайн-кнопки делают это десятком
экранов и ``✍️``-подсказок; правила при этом одни — ``bot_permissions``
(гранты), ``bot.views`` (срок), ``bot.sections.members`` (окна доступа).

Секрет ключа никогда не лежит в ответе списка или карточки: его отдаёт
только отдельный POST (свежая сессия, строка в журнале), по нажатию.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from database import (
    create_bot_key, delete_bot_key, get_bot_key, get_bot_member, list_access_windows, list_bot_key_members,
    list_bot_keys, set_bot_key_expiry, set_bot_key_label, set_bot_key_max_uses, set_bot_key_revoked,
    set_bot_key_role, set_bot_member_blocked,
)
from pulse_desk.access_control import parse_repeat_rule, resolve_access, window_from_row
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections import members as members_section
from pulse_desk.bot.sections.keys import grantable_accounts, save_permissions
from pulse_desk.bot.views import MAX_EXPIRY_DAYS, expiry_from_days, key_expired
from pulse_desk.bot_permissions import (
    ALL_FEATURES, ALL_NOTIFY, FEATURES, MAX_DELAY_MINUTES, NOTIFY_TYPES, dump_permissions, full_permissions,
    parse_permissions, set_accounts, set_delay, set_features, set_notify,
)
from pulse_desk.bot_prefs import parse_hhmm
from pulse_desk.common import record_app_event
from pulse_desk.security import generate_access_key

from .common import Caller, admin_caller, audit, fresh_admin

router = APIRouter()


def key_state(key: dict) -> str:
    """active / revoked / expired / used — what the invite does if opened now."""
    if key.get("revoked"):
        return "revoked"
    if key_expired(key.get("expires_at")):
        return "expired"
    max_uses = int(key.get("max_uses") or 0)
    if max_uses and int(key.get("member_count") or 0) >= max_uses:
        return "used"
    return "active"


def key_row(key: dict) -> dict:
    grants = parse_permissions(key.get("permissions"))
    return {
        "id": int(key["id"]),
        "label": key.get("label") or "",
        "role": key.get("role") or "viewer",
        "state": key_state(key),
        "expires_at": key.get("expires_at"),
        "max_uses": int(key.get("max_uses") or 0),
        "members": int(key.get("member_count") or 0),
        "features": len(grants.get("features") or []),
        "accounts": list(grants.get("accounts") or []),
    }


@router.get("/api/app/keys")
async def keys(caller: Caller = Depends(admin_caller)) -> dict:
    # Revoked keys stay listed — the panel is where they come back.
    return {"items": [key_row(k) for k in await list_bot_keys(include_revoked=True)]}


async def _key_or_404(key_id: int) -> dict:
    key = await get_bot_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    return key


@router.get("/api/app/keys/{key_id}")
async def key_card(key_id: int, caller: Caller = Depends(admin_caller)) -> dict:
    key = await _key_or_404(key_id)
    members = await list_bot_key_members(key_id)
    key["member_count"] = len(members)
    grants = parse_permissions(key.get("permissions"))
    return {
        **key_row(key),
        "grants": {
            "features": grants.get("features") or [],
            "notify": grants.get("notify") or [],
            "accounts": grants.get("accounts") or [],
            "delay_minutes": int(grants.get("delay_minutes") or 0),
        },
        "options": {
            "features": [{"code": c, "label": FEATURES[c][0].split(" ", 1)[-1], "hint": FEATURES[c][1]} for c in ALL_FEATURES],
            "notify": [{"code": c, "label": NOTIFY_TYPES[c][0].split(" ", 1)[-1], "hint": NOTIFY_TYPES[c][1]} for c in ALL_NOTIFY],
            "accounts": grantable_accounts(),
            "max_delay": MAX_DELAY_MINUTES,
            "max_expiry_days": MAX_EXPIRY_DAYS,
        },
        "holders": [
            {"tg_id": int(m["tg_id"]), "name": m.get("name") or m.get("tg_username") or str(m["tg_id"]),
             "username": m.get("tg_username") or "", "blocked": bool(m.get("blocked"))}
            for m in members
        ],
    }


class KeyPatch(BaseModel):
    label: Optional[str] = Field(None, max_length=40)
    role: Optional[str] = Field(None, pattern="^(viewer|premium)$")
    expires_days: Optional[int] = Field(None, ge=0, le=MAX_EXPIRY_DAYS)
    once: Optional[bool] = None
    features: Optional[list[str]] = Field(None, max_length=len(ALL_FEATURES))
    notify: Optional[list[str]] = Field(None, max_length=len(ALL_NOTIFY))
    accounts: Optional[list[str]] = Field(None, max_length=200)
    delay_minutes: Optional[int] = Field(None, ge=0, le=MAX_DELAY_MINUTES)


def _unknown(values: list[str], allowed: list[str]) -> list[str]:
    return [v for v in values if v not in allowed]


@router.post("/api/app/keys/{key_id}")
async def update_key(key_id: int, body: KeyPatch, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    """Any subset of the key's settings; each write goes through the same setter
    the bot's key panel uses, and each lands in the event log."""
    key = await _key_or_404(key_id)
    changed = body.model_dump(exclude_none=True)
    audit(request, key_id=key_id, fields=sorted(changed))
    # Everything is checked before anything is written: a refused field must
    # not leave the others half-saved.
    allowed = grantable_accounts()
    if body.label is not None and not body.label.strip():
        raise HTTPException(status_code=422, detail="Метка не может быть пустой")
    if body.features is not None and _unknown(body.features, ALL_FEATURES):
        raise HTTPException(status_code=422, detail="Неизвестный раздел")
    if body.notify is not None and _unknown(body.notify, ALL_NOTIFY):
        raise HTTPException(status_code=422, detail="Неизвестный тип уведомлений")
    if body.accounts is not None and _unknown(body.accounts, allowed):
        raise HTTPException(status_code=422, detail="Такой аккаунт не отслеживается")

    if body.label is not None:
        await set_bot_key_label(key_id, body.label.strip())
    if body.role is not None:
        await set_bot_key_role(key_id, body.role)
    if body.expires_days is not None:
        await set_bot_key_expiry(key_id, expiry_from_days(body.expires_days))
    if body.once is not None:
        await set_bot_key_max_uses(key_id, 1 if body.once else 0)

    grants = parse_permissions(key.get("permissions"))
    touched = False
    if body.features is not None:
        grants, touched = set_features(grants, body.features), True
    if body.notify is not None:
        grants, touched = set_notify(grants, body.notify), True
    if body.accounts is not None:
        # An empty whitelist means "all": picking every account stores it that
        # way, exactly as the bot does, so a later tracked account is included.
        chosen = [] if set(body.accounts) >= set(allowed) else body.accounts
        grants, touched = set_accounts(grants, chosen), True
    if body.delay_minutes is not None:
        grants, touched = set_delay(grants, body.delay_minutes), True
    if touched:
        await save_permissions(key_id, grants)
    if changed:
        await record_app_event("INFO", "bot", "Bot key updated", {"id": key_id, "fields": sorted(changed), "via": "panel"})
    return await key_card(key_id, caller)


class NewKey(BaseModel):
    label: str = Field("", max_length=40)


@router.post("/api/app/keys")
async def create_key(body: NewKey, caller: Caller = Depends(fresh_admin)) -> dict:
    """Same defaults as ``/newkey``: viewer, every section, no expiry."""
    key = await create_bot_key(body.label.strip(), generate_access_key(), "viewer", None,
                               dump_permissions(full_permissions()))
    await record_app_event("INFO", "bot", "Bot access key created", {"id": key["id"], "via": "panel"})
    return {"ok": True, "id": int(key["id"])}


class RevokeBody(BaseModel):
    revoked: bool


@router.post("/api/app/keys/{key_id}/revoke")
async def revoke_key(key_id: int, body: RevokeBody, caller: Caller = Depends(fresh_admin)) -> dict:
    await _key_or_404(key_id)
    await set_bot_key_revoked(key_id, body.revoked)
    await record_app_event("INFO", "bot", "Bot key revoked" if body.revoked else "Bot key restored",
                           {"id": key_id, "via": "panel"})
    return {"ok": True, "revoked": body.revoked}


@router.post("/api/app/keys/{key_id}/delete")
async def remove_key(key_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    """Drops the invite only: people who already joined keep access."""
    deleted = await delete_bot_key(key_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Ключ уже удалён")
    await record_app_event("INFO", "bot", "Bot access key deleted",
                           {"id": key_id, "label": deleted.get("label"), "via": "panel"})
    return {"ok": True, "members_kept": int(deleted.get("member_count") or 0)}


@router.post("/api/app/keys/{key_id}/link")
async def key_link(key_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    """The secret and the t.me link, on demand — never part of a list."""
    key = await _key_or_404(key_id)
    secret = key.get("secret") or ""
    username = state.bot_username
    return {"secret": secret, "link": f"https://t.me/{username}?start={secret}" if username else "",
            "state": key_state({**key, "member_count": len(await list_bot_key_members(key_id))})}


# ---- участник: доступ по расписанию ------------------------------------------
DAY_CODES = range(1, 8)


async def _member_or_404(tg_id: int) -> dict:
    member = await get_bot_member(tg_id)
    if not member:
        raise HTTPException(status_code=404, detail="Участник не найден")
    return member


@router.get("/api/app/members/{tg_id}")
async def member_card(tg_id: int, caller: Caller = Depends(admin_caller)) -> dict:
    member = await _member_or_404(tg_id)
    rows = await list_access_windows(tg_id)
    decision = resolve_access(member, [window_from_row(r) for r in rows], datetime.now(timezone.utc))
    return {
        "tg_id": tg_id,
        "name": member.get("name") or member.get("tg_username") or str(tg_id),
        "username": member.get("tg_username") or "",
        "key_id": member.get("key_id"),
        "key_label": member.get("key_label") or "",
        "blocked": bool(member.get("blocked")),
        "timezone": member.get("timezone") or "UTC",
        "policy": member.get("access_default_policy") or "allow",
        "open_now": bool(decision.allowed),
        # Without windows the resolver still names its look-ahead horizon;
        # "open until" is only true when a window will actually close it.
        "until": (decision.until.astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
                  if decision.until and rows else None),
        "zones": list(ZONES),
        "windows": [
            {"id": int(w["id"]), "allow": bool(w.get("enabled")), "manual": int(w.get("priority") or 0) >= 1000,
             "text": members_section.describe_repeat(parse_repeat_rule(w.get("repeat_rule")), w).replace("`", "")}
            for w in rows
        ],
    }


class WindowBody(BaseModel):
    kind: str = Field(..., pattern="^(work|mute)$")
    start: str = Field(..., max_length=5)
    end: str = Field(..., max_length=5)
    days: list[int] = Field(default_factory=list, max_length=7)
    # Without it the member's own zone, as /access does. The form sends it
    # explicitly: "09:00" read in UTC is noon in Kyiv.
    tz: Optional[str] = Field(None, max_length=64)


# Offered on the form; any IANA name is accepted from the API.
ZONES = ("Europe/Kyiv", "Europe/Warsaw", "Europe/Moscow", "UTC")


@router.post("/api/app/members/{tg_id}/windows")
async def add_window(tg_id: int, body: WindowBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    member = await _member_or_404(tg_id)
    frm, to = parse_hhmm(body.start), parse_hhmm(body.end)
    if not frm or not to:
        raise HTTPException(status_code=422, detail="Время — в формате 09:00")
    if any(d not in DAY_CODES for d in body.days):
        raise HTTPException(status_code=422, detail="Дни недели — от 1 (пн) до 7 (вс)")
    tz = body.tz or member.get("timezone") or "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Неизвестный часовой пояс") from exc
    await members_section.add_access_window(tg_id, body.kind, frm, to, body.days, tz, caller.tg_id)
    audit(request, tg_id=tg_id, kind=body.kind)
    return await member_card(tg_id, caller)


@router.post("/api/app/members/{tg_id}/windows/{window_id}/delete")
async def delete_window(tg_id: int, window_id: int, caller: Caller = Depends(fresh_admin)) -> dict:
    await _member_or_404(tg_id)
    if not any(int(w["id"]) == window_id for w in await list_access_windows(tg_id)):
        raise HTTPException(status_code=404, detail="Окно не найдено")
    await members_section.remove_access_window(tg_id, window_id, caller.tg_id)
    return await member_card(tg_id, caller)


class AccessBody(BaseModel):
    # "open" cancels manual blackouts; "close" closes for `hours` (0 = until reopened).
    action: str = Field(..., pattern="^(open|close|block|unblock)$")
    hours: int = Field(0, ge=0, le=24 * 30)


@router.post("/api/app/members/{tg_id}/access")
async def member_access(tg_id: int, body: AccessBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    await _member_or_404(tg_id)
    audit(request, tg_id=tg_id, action=body.action)
    if body.action == "open":
        await members_section.open_member_access(tg_id, caller.tg_id)
    elif body.action == "close":
        until = None
        if body.hours:
            until = (datetime.now(timezone.utc) + timedelta(hours=body.hours)).replace(microsecond=0, tzinfo=None).isoformat()
        await members_section.close_member_access(tg_id, until, caller.tg_id)
    else:
        blocked = body.action == "block"
        await set_bot_member_blocked(tg_id, blocked)
        state.access_cache.pop(tg_id, None)
        await record_app_event("INFO", "bot", "Member blocked" if blocked else "Member unblocked",
                               {"tg_id": tg_id, "via": "panel"})
    return await member_card(tg_id, caller)
