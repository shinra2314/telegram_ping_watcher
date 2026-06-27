"""Giveaway admin actions and channel profile endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import LeaveChannelRequest

from database import (
    get_giveaway_candidate,
    get_ping_by_id,
    record_giveaway_action,
    update_giveaway_candidate_status,
    update_ping_meta,
)
from pulse_desk import watch_settings as ws
from pulse_desk.app_ctx import get_current_role, require_admin, state
from pulse_desk.common import record_app_event
from pulse_desk.giveaway_actions import (
    analyze_and_store_giveaway,
    find_giveaway_action_client,
    load_giveaway_message,
)
from pulse_desk.giveaways import inactive_channel_candidate
from pulse_desk.live import publish_live_event
from pulse_desk.ping_pipeline import refresh_channel_profile, refresh_ping_deadline

router = APIRouter()


@router.post("/api/giveaways/{ping_id}/analyze", dependencies=[Depends(require_admin)])
async def analyze_giveaway_api(ping_id: int):
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise HTTPException(404, "Ping not found")
    client = await find_giveaway_action_client()
    if not client:
        raise HTTPException(400, f"Action account @{ws.GIVEAWAY_ACTION_ACCOUNT} is not online")
    message = None
    try:
        message = await load_giveaway_message(client, ping)
    except HTTPException:
        message = None
    candidate = await analyze_and_store_giveaway(client, ping_id, ping, message)
    if not candidate:
        raise HTTPException(500, "Giveaway analysis failed")
    return {"status": "ok", "candidate": candidate}


@router.post("/api/giveaways/{ping_id}/refresh-deadline", dependencies=[Depends(require_admin)])
async def refresh_giveaway_deadline_api(ping_id: int):
    if not state.clients:
        raise HTTPException(400, "No connected Telegram accounts")
    return await refresh_ping_deadline(state.clients[0], ping_id)


@router.post("/api/giveaways/{ping_id}/skip", dependencies=[Depends(require_admin)])
async def skip_giveaway_api(ping_id: int):
    if not await get_giveaway_candidate(ping_id):
        raise HTTPException(404, "Giveaway candidate not found")
    await update_giveaway_candidate_status(ping_id, "skipped")
    await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
    await record_giveaway_action(ping_id, "skip", "skipped", "admin")
    await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "skipped"})
    return {"status": "skipped"}


@router.get("/api/giveaways/cleanup-candidates")
async def read_giveaway_cleanup_candidates(role: str = Depends(get_current_role), limit: int = Query(100, ge=1, le=500)):
    client = await find_giveaway_action_client()
    if not client:
        return {"action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}", "candidates": [], "warning": "Action account is not online"}
    candidates = []
    async for dialog in client.iter_dialogs(limit=limit):
        item = inactive_channel_candidate(dialog, ws.GIVEAWAY_INACTIVE_CHANNEL_DAYS)
        if item:
            candidates.append(item)
    return {"action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}", "inactive_days": ws.GIVEAWAY_INACTIVE_CHANNEL_DAYS, "candidates": candidates}


@router.post("/api/giveaways/cleanup-candidates/{chat_id}/leave", dependencies=[Depends(require_admin)])
async def leave_inactive_channel_api(chat_id: int):
    client = await find_giveaway_action_client()
    if not client:
        raise HTTPException(400, f"Action account @{ws.GIVEAWAY_ACTION_ACCOUNT} is not online")
    try:
        entity = await client.get_entity(chat_id)
        await client(LeaveChannelRequest(entity))
        await record_giveaway_action(None, "leave_channel", "left", "admin", f"Left channel {chat_id}", {"chat_id": chat_id})
        await record_app_event("WARNING", "giveaway", "Left inactive channel after admin confirmation", {"chat_id": chat_id})
        return {"status": "left", "chat_id": chat_id}
    except FloodWaitError as exc:
        await record_giveaway_action(None, "leave_channel", "manual_required", "admin", f"FloodWait {exc.seconds}s", {"chat_id": chat_id})
        raise HTTPException(429, f"Telegram FloodWait {exc.seconds}s") from exc
    except Exception as exc:
        await record_giveaway_action(None, "leave_channel", "failed", "admin", str(exc), {"chat_id": chat_id})
        raise HTTPException(500, str(exc)) from exc


@router.post("/api/channels/{chat_id}/refresh-profile", dependencies=[Depends(require_admin)])
async def refresh_channel_profile_api(chat_id: int):
    if not state.clients:
        raise HTTPException(400, "No connected Telegram accounts")
    profile = await refresh_channel_profile(state.clients[0], chat_id, force=True)
    await record_app_event("INFO", "deadline", "Channel profile refreshed manually", {"chat_id": chat_id})
    await publish_live_event("channel-profile", {"chat_id": chat_id, "deadline_at": profile.get("deadline_at")})
    return {"status": "ok", "profile": profile}
