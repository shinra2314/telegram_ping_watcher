"""Bot multi-user access keys and member management endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from database import create_bot_key, list_bot_keys, list_bot_members, revoke_bot_key, set_bot_member_blocked
from pulse_desk.app_ctx import require_admin, state
from pulse_desk.common import record_app_event
from pulse_desk.security import generate_access_key

router = APIRouter()


class BotKeyCreateRequest(BaseModel):
    label: str = ""
    expires_at: Optional[str] = None


class BotMemberBlockRequest(BaseModel):
    blocked: bool = True


def _bot_share_link(secret: str) -> str:
    return f"https://t.me/{state.bot_username}?start={secret}" if state.bot_username else ""


@router.get("/api/bot/access", dependencies=[Depends(require_admin)])
async def get_bot_access():
    keys = await list_bot_keys()
    for key in keys:
        key["share_link"] = _bot_share_link(key.get("secret", ""))
    members = await list_bot_members()
    return {"keys": keys, "members": members, "bot_username": state.bot_username}


@router.post("/api/bot/access/keys", dependencies=[Depends(require_admin)])
async def create_bot_access_key(data: BotKeyCreateRequest):
    secret = generate_access_key()
    expires_at = (data.expires_at or "").strip() or None
    key = await create_bot_key(data.label.strip(), secret, "viewer", expires_at)
    key["share_link"] = _bot_share_link(secret)
    await record_app_event("INFO", "bot", "Bot access key created", {"label": key.get("label"), "id": key.get("id")})
    return {"status": "ok", "key": key}


@router.post("/api/bot/access/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
async def revoke_bot_access_key(key_id: int):
    await revoke_bot_key(key_id)
    await record_app_event("INFO", "bot", "Bot access key revoked", {"id": key_id})
    return {"status": "ok"}


@router.post("/api/bot/access/members/{tg_id}/block", dependencies=[Depends(require_admin)])
async def block_bot_access_member(tg_id: int, data: BotMemberBlockRequest):
    await set_bot_member_blocked(tg_id, data.blocked)
    await record_app_event("INFO", "bot", "Bot member block toggled", {"tg_id": tg_id, "blocked": data.blocked})
    return {"status": "ok"}
