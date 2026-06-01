"""Ping list, search, status updates, tags, favorites, deadlines.

This is the largest router slice. It depends only on:
  - database CRUD
  - pulse_desk.live.publish_live_event
  - pulse_desk.statuses constants
  - pulse_desk.api_models.PingMetaRequest
so it has no coupling to main.py module globals.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import (
    add_ping_tag,
    get_pings,
    get_pings_grouped,
    mark_ping_read as mark_ping_read_db,
    mark_pings_read as mark_pings_read_db,
    rebuild_search_indexes,
    record_event,
    remove_ping_tag,
    replace_ping_reminders,
    search_pings_fts,
    toggle_favorite,
    update_ping_meta,
)
from pulse_desk.api_models import PingMetaRequest
from pulse_desk.app_ctx import get_current_role, logger, require_admin
from pulse_desk.live import publish_live_event
from pulse_desk.statuses import ACTION_STATUSES, GIVEAWAY_STATUSES, PING_STATUSES


router = APIRouter()


async def _record_event(level: str, source: str, message: str, context: Optional[dict] = None) -> None:
    try:
        await record_event(level, source, message, context)
    except Exception:
        logger.debug("Could not persist app event", exc_info=True)


@router.get("/api/pings")
async def read_pings(
    _: str = Depends(get_current_role),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0),
    chat_type: str = "all",
    sort: str = "DESC",
    sort_by: str = "detected_at",
    grouped: bool = False,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = Query(default=None, ge=0, le=100),
    action_status: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    source_score_min: Optional[float] = None,
    tag: Optional[str] = Query(None),
):
    if grouped:
        return await get_pings_grouped(limit=limit, chat_type=chat_type, search=search, mention=mention)
    return await get_pings(
        limit=limit,
        chat_type=chat_type,
        sort_order=sort,
        offset=offset,
        sort_by=sort_by,
        status=status_filter,
        favorite=favorite,
        mention=mention,
        search=search,
        date_from=date_from,
        date_to=date_to,
        priority_min=priority_min,
        action_status=action_status,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_deadline=has_deadline,
        source_score_min=source_score_min,
        tag=tag,
    )


@router.get("/api/pings/search")
async def pings_full_text_search(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: str = Depends(get_current_role),
):
    rows = await search_pings_fts(q, limit=limit, offset=offset)
    return {
        "query": q,
        "count": len(rows),
        "limit": limit,
        "offset": offset,
        "rows": rows,
    }


@router.post("/api/search/rebuild", dependencies=[Depends(require_admin)])
async def rebuild_search_api():
    result = await rebuild_search_indexes()
    await _record_event("INFO", "search", "Search indexes rebuilt manually", result)
    return {"status": "ok", **result}


@router.post("/api/pings/mark-read/{ping_id}", dependencies=[Depends(require_admin)])
async def mark_ping_read(ping_id: int):
    await mark_ping_read_db(ping_id)
    await publish_live_event("ping-updated", {"ping_id": ping_id, "status": "read"})
    return {"status": "ok"}


@router.post("/api/pings/mark-read", dependencies=[Depends(require_admin)])
async def mark_filtered_pings_read(
    chat_type: str = "all",
    status_filter: Optional[str] = Query(default=None, alias="status"),
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = Query(default=None, ge=0, le=100),
    action_status: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    has_deadline: Optional[bool] = None,
    source_score_min: Optional[float] = None,
    only_new: bool = True,
):
    changed = await mark_pings_read_db(
        chat_type=chat_type,
        status=status_filter,
        favorite=favorite,
        mention=mention,
        search=search,
        date_from=date_from,
        date_to=date_to,
        priority_min=priority_min,
        action_status=action_status,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_deadline=has_deadline,
        source_score_min=source_score_min,
        only_new=only_new,
    )
    if changed:
        await publish_live_event("ping-updated", {"bulk": True, "changed": changed, "status": "read"})
    return {"status": "ok", "changed": changed}


@router.post("/api/pings/toggle-favorite/{ping_id}", dependencies=[Depends(require_admin)])
async def toggle_ping_favorite(ping_id: int):
    await toggle_favorite(ping_id)
    return {"status": "ok"}


@router.post("/api/pings/{ping_id}/tags/{tag}")
async def ping_add_tag(ping_id: int, tag: str, _: bool = Depends(require_admin)):
    tags = await add_ping_tag(ping_id, tag.strip().replace('"', ''))
    return {"tags": tags}


@router.delete("/api/pings/{ping_id}/tags/{tag}")
async def ping_remove_tag(ping_id: int, tag: str, _: bool = Depends(require_admin)):
    tags = await remove_ping_tag(ping_id, tag.strip().replace('"', ''))
    return {"tags": tags}


@router.put("/api/pings/{ping_id}", dependencies=[Depends(require_admin)])
async def update_ping_details(ping_id: int, data: PingMetaRequest):
    if data.status is not None and data.status not in PING_STATUSES:
        raise HTTPException(400, f"Unknown status: {data.status}")
    if data.giveaway_status is not None and data.giveaway_status not in GIVEAWAY_STATUSES:
        raise HTTPException(400, f"Unknown giveaway status: {data.giveaway_status}")
    if data.action_status is not None and data.action_status not in ACTION_STATUSES:
        raise HTTPException(400, f"Unknown action status: {data.action_status}")
    for label, value in (("deadline_at", data.deadline_at), ("reminder_at", data.reminder_at)):
        if value:
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise HTTPException(400, f"Invalid {label}: {value}") from exc
    await update_ping_meta(
        ping_id,
        status=data.status,
        note=data.note,
        is_favorite=data.is_favorite,
        giveaway_status=data.giveaway_status,
        deadline_at=data.deadline_at,
        deadline_source="manual" if data.deadline_at is not None else None,
        deadline_text="Ручной дедлайн" if data.deadline_at else None,
        reminder_at=data.reminder_at,
        action_status=data.action_status,
    )
    if data.deadline_at is not None or data.reminder_at is not None:
        await replace_ping_reminders(ping_id, data.deadline_at, data.reminder_at)
    await publish_live_event(
        "ping-updated",
        {"ping_id": ping_id, "deadline_at": data.deadline_at, "action_status": data.action_status},
    )
    return {"status": "ok"}
