"""Настройки владельца: тонкие поля из схемы, уведомления, игнор-чаты.

Форма «Работа» строится из ``bot/settings_schema.RUNTIME_FIELDS`` — ни одного
поля руками: новая строка схемы появляется и в боте, и здесь. Значение
проверяет ``Field.parse`` (тот же текст ошибки, что в боте) и записывает
``bot.sections.settings.apply_field`` → ``persist.save_runtime``: сохранить,
применить к живому ``watch_settings``, записать в историю.

Уведомления, тихие часы, дайджест, автоудаление и игнор-чаты пишутся теми
же ``persist``/``ignored_chats``, что кнопки ``st_n_*``/``igc:*`` бота.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from database import get_setting, set_setting
from pulse_desk import autoclean, ignored_chats
from pulse_desk import watch_settings as ws
from pulse_desk.bot.persist import save_digest, save_notifications
from pulse_desk.bot.sections.settings import apply_field, runtime_values
from pulse_desk.bot.settings_schema import RUNTIME_FIELDS
from pulse_desk.bot_prefs import parse_hhmm
from pulse_desk.common import record_app_event

from .common import Caller, admin_caller, audit, fresh_admin

router = APIRouter()


def field_view(index: int, field, values: dict[str, Any]) -> dict[str, Any]:
    value = values.get(field.key)
    return {
        "index": index,
        "key": field.key,
        "label": field.label,
        "kind": field.kind,
        "unit": field.unit,
        "min": field.minimum,
        "max": field.maximum,
        "presets": list(field.presets),
        "choices": list(field.choices),
        # The schema's hints are written for the bot's Markdown.
        "hint": field.hint.replace("`", ""),
        "value": value,
        "text": field.format(value),
    }


async def settings_payload() -> dict[str, Any]:
    values = await runtime_values()
    notif = await ws.load_notification_settings()
    digest = await ws.load_digest_settings()
    quiet = notif.get("quiet_hours") or {}
    ignored = await ignored_chats.load()
    return {
        "fields": [field_view(i, f, values) for i, f in enumerate(RUNTIME_FIELDS)],
        "notifications": {
            "enabled": bool(notif.get("enabled", True)),
            "include_giveaways": bool(notif.get("include_giveaways", True)),
            "include_wins": bool(notif.get("include_wins", True)),
            "moderated": notif.get("moderation_mode") == "moderated",
            "quiet_enabled": bool(quiet.get("enabled")),
            "quiet_from": quiet.get("from") or "23:00",
            "quiet_to": quiet.get("to") or "08:00",
            "cooldown_seconds": int(notif.get("cooldown_seconds") or 0),
            "digest_enabled": bool(digest.get("enabled", True)),
            "digest_time": digest.get("time") or "10:00",
            "autoclean_hours": autoclean.owner_hours(await get_setting(autoclean.SETTINGS_KEY, None)),
            "autoclean_choices": list(autoclean.CHOICES),
        },
        "ignored": [{"chat_id": int(cid), "title": title} for cid, title in ignored.items()],
    }


@router.get("/api/app/settings")
async def settings(caller: Caller = Depends(admin_caller)) -> dict:
    return await settings_payload()


class FieldBody(BaseModel):
    index: int = Field(..., ge=0, lt=len(RUNTIME_FIELDS))
    value: Any


@router.post("/api/app/settings/field")
async def set_field(body: FieldBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    field = RUNTIME_FIELDS[body.index]
    audit(request, field=field.key)
    raw = "да" if body.value is True else ("нет" if body.value is False else str(body.value))
    ok, note = await apply_field(body.index, raw)
    if not ok:
        raise HTTPException(status_code=422, detail=note.replace("❌", "").replace("`", "").strip())
    return {"ok": True, "field": field_view(body.index, field, await runtime_values())}


class NotifyPatch(BaseModel):
    enabled: Optional[bool] = None
    include_giveaways: Optional[bool] = None
    include_wins: Optional[bool] = None
    moderated: Optional[bool] = None
    quiet_enabled: Optional[bool] = None
    quiet_from: Optional[str] = Field(None, max_length=5)
    quiet_to: Optional[str] = Field(None, max_length=5)
    cooldown_seconds: Optional[int] = Field(None, ge=0, le=3600)
    digest_enabled: Optional[bool] = None
    digest_time: Optional[str] = Field(None, max_length=5)
    autoclean_hours: Optional[int] = None


def _time(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    parsed = parse_hhmm(raw)
    if not parsed:
        raise HTTPException(status_code=422, detail="Время — в формате 09:00")
    return parsed


@router.post("/api/app/settings/notifications")
async def set_notifications(body: NotifyPatch, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    patch = body.model_dump(exclude_none=True)
    audit(request, fields=sorted(patch))
    quiet_from, quiet_to, digest_time = _time(body.quiet_from), _time(body.quiet_to), _time(body.digest_time)
    if body.autoclean_hours is not None and body.autoclean_hours not in autoclean.CHOICES:
        raise HTTPException(status_code=422, detail="Такого срока автоудаления нет")

    notif_keys = {"enabled", "include_giveaways", "include_wins", "moderated", "quiet_enabled",
                  "quiet_from", "quiet_to", "cooldown_seconds"}
    if notif_keys & set(patch):
        notif = await ws.load_notification_settings()
        for key in ("enabled", "include_giveaways", "include_wins"):
            if key in patch:
                notif[key] = patch[key]
        if body.moderated is not None:
            notif["moderation_mode"] = "moderated" if body.moderated else "auto"
        if body.cooldown_seconds is not None:
            notif["cooldown_seconds"] = body.cooldown_seconds
        if {"quiet_enabled", "quiet_from", "quiet_to"} & set(patch):
            quiet = dict(notif.get("quiet_hours") or {})
            quiet.setdefault("from", "23:00")
            quiet.setdefault("to", "08:00")
            if body.quiet_enabled is not None:
                quiet["enabled"] = body.quiet_enabled
            if quiet_from:
                quiet["from"] = quiet_from
            if quiet_to:
                quiet["to"] = quiet_to
            notif["quiet_hours"] = quiet
        await save_notifications(notif)
    if body.digest_enabled is not None or digest_time:
        digest = await ws.load_digest_settings()
        if body.digest_enabled is not None:
            digest["enabled"] = body.digest_enabled
        if digest_time:
            digest["time"] = digest_time
        await save_digest(digest)
    if body.autoclean_hours is not None:
        await set_setting(autoclean.SETTINGS_KEY, {"hours": body.autoclean_hours})
    return await settings_payload()


class RestoreBody(BaseModel):
    chat_ids: list[int] = Field(..., min_length=1, max_length=200)


@router.post("/api/app/settings/ignored/restore")
async def restore_ignored(body: RestoreBody, request: Request, caller: Caller = Depends(fresh_admin)) -> dict:
    """«Снова следить» for several chats at once — ``igc:del`` for each."""
    cfg = await ignored_chats.load()
    restored = []
    for chat_id in dict.fromkeys(body.chat_ids):
        if str(chat_id) in cfg:
            title = cfg.get(str(chat_id), str(chat_id))
            cfg = ignored_chats.remove(cfg, chat_id)
            restored.append(chat_id)
            await record_app_event("INFO", "notifications", "Chat watched again",
                                   {"chat_id": chat_id, "chat": title, "via": "panel"})
    await ignored_chats.save(cfg)
    audit(request, chat_ids=restored)
    return {"ok": True, "restored": len(restored), **(await settings_payload())}
