"""Scheduled per-member access management endpoints (admin only)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import (
    create_access_window,
    create_disable_until_window,
    deactivate_access_window,
    get_access_audit,
    get_bot_member,
    list_access_windows,
    list_all_access_windows,
    list_bot_members,
    record_access_audit,
    set_access_window_active,
    set_member_default_policy,
)
from pulse_desk.access_control import find_undoable, plan_undo, resolve_access, window_from_row
from pulse_desk.app_ctx import require_admin, state
from pulse_desk.common import record_app_event

router = APIRouter()


class WindowCreateRequest(BaseModel):
    enabled: bool = True
    repeat_rule: Any = None
    timezone: str = "UTC"
    priority: int = 100
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    label: str = ""


class DisableUntilRequest(BaseModel):
    until: Optional[str] = None


class PolicyRequest(BaseModel):
    policy: str = "allow"


def _decision_for(member: dict, windows: list[dict]) -> dict:
    d = resolve_access(member, [window_from_row(w) for w in windows], datetime.now(timezone.utc))
    return {"allowed": d.allowed, "reason": d.reason, "until": d.until.isoformat() if d.until else None}


@router.get("/api/access/restricted", dependencies=[Depends(require_admin)])
async def list_restricted():
    members = await list_bot_members()
    grouped = await list_all_access_windows()
    now = datetime.now(timezone.utc)
    users = []
    for m in members:
        if m.get("blocked"):
            continue
        tg = int(m["tg_id"])
        d = resolve_access(m, [window_from_row(w) for w in grouped.get(tg, [])], now)
        if not d.allowed:
            users.append({
                "tg_id": tg, "name": m.get("name"), "tg_username": m.get("tg_username"),
                "reason": d.reason, "until": d.until.isoformat() if d.until else None,
            })
    return {"count": len(users), "users": users}


@router.get("/api/access/{tg_id}", dependencies=[Depends(require_admin)])
async def get_member_access(tg_id: int):
    member = await get_bot_member(tg_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    windows = await list_access_windows(tg_id)
    return {
        "tg_id": tg_id,
        "blocked": bool(member.get("blocked")),
        "default_policy": member.get("access_default_policy", "allow"),
        "timezone": member.get("timezone") or "",
        "effective": _decision_for(member, windows),
        "windows": windows,
    }


@router.post("/api/access/{tg_id}/windows", dependencies=[Depends(require_admin)])
async def create_window(tg_id: int, data: WindowCreateRequest):
    if not await get_bot_member(tg_id):
        raise HTTPException(status_code=404, detail="Member not found")
    row = await create_access_window(
        tg_id, enabled=data.enabled, repeat_rule=data.repeat_rule, timezone=data.timezone,
        priority=data.priority, start_at=data.start_at, end_at=data.end_at, label=data.label,
    )
    await record_access_audit(tg_id, int(row["id"]), "create", "web", None, row)
    state.access_cache.pop(tg_id, None)
    await record_app_event("INFO", "access", "Access window created", {"tg_id": tg_id, "id": row["id"]})
    return {"status": "ok", "window": row}


@router.post("/api/access/{tg_id}/disable-until", dependencies=[Depends(require_admin)])
async def disable_until(tg_id: int, data: DisableUntilRequest):
    if not await get_bot_member(tg_id):
        raise HTTPException(status_code=404, detail="Member not found")
    row = await create_disable_until_window(tg_id, data.until, created_by=None)
    await record_access_audit(tg_id, int(row["id"]), "manual_off", "web", None, {"until": data.until})
    state.access_cache.pop(tg_id, None)
    return {"status": "ok", "window_id": row["id"]}


@router.post("/api/access/{tg_id}/enable", dependencies=[Depends(require_admin)])
async def enable_member(tg_id: int):
    cancelled_ids: list[int] = []
    for w in await list_access_windows(tg_id):
        if not w["enabled"] and w["priority"] >= 1000:
            await deactivate_access_window(int(w["id"]))
            cancelled_ids.append(int(w["id"]))
    await record_access_audit(tg_id, None, "manual_on", "web", None, {"cancelled_ids": cancelled_ids})
    state.access_cache.pop(tg_id, None)
    return {"status": "ok", "cancelled": len(cancelled_ids)}


@router.post("/api/access/{tg_id}/policy", dependencies=[Depends(require_admin)])
async def set_policy(tg_id: int, data: PolicyRequest):
    if data.policy not in ("allow", "deny"):
        raise HTTPException(status_code=400, detail="policy must be 'allow' or 'deny'")
    member = await get_bot_member(tg_id)
    old = (member or {}).get("access_default_policy", "allow")
    await set_member_default_policy(tg_id, data.policy)
    await record_access_audit(tg_id, None, "policy", "web", {"policy": old}, {"policy": data.policy})
    state.access_cache.pop(tg_id, None)
    return {"status": "ok", "policy": data.policy}


@router.post("/api/access/{tg_id}/undo", dependencies=[Depends(require_admin)])
async def undo_last(tg_id: int):
    target = find_undoable(await get_access_audit(tg_id, limit=50))
    if not target:
        return {"status": "ok", "undone": None}
    plan = plan_undo(target)
    op = plan.get("op")
    if op == "deactivate" and plan.get("schedule_id"):
        await deactivate_access_window(int(plan["schedule_id"]))
    elif op == "reactivate" and plan.get("schedule_id"):
        await set_access_window_active(int(plan["schedule_id"]), True)
    elif op == "reactivate_many":
        for sid in plan.get("schedule_ids", []):
            await set_access_window_active(int(sid), True)
    elif op == "set_policy" and plan.get("policy"):
        await set_member_default_policy(tg_id, plan["policy"])
    await record_access_audit(tg_id, target.get("schedule_id"), "undo", "web", None, {"undone_audit_id": int(target["id"]), "plan": plan})
    state.access_cache.pop(tg_id, None)
    return {"status": "ok", "undone": {"id": target["id"], "action": target["action"]}}


@router.delete("/api/access/windows/{window_id}", dependencies=[Depends(require_admin)])
async def delete_window(window_id: int):
    await deactivate_access_window(window_id)
    await record_app_event("INFO", "access", "Access window deactivated", {"id": window_id})
    return {"status": "ok"}


@router.get("/api/access/{tg_id}/audit", dependencies=[Depends(require_admin)])
async def get_audit(tg_id: int, limit: int = 20):
    return {"tg_id": tg_id, "audit": await get_access_audit(tg_id, limit=limit)}
