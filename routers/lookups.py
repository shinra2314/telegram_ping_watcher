"""Read-only lookup endpoints — tags, sources, scan-runs, saved filters, settings history.

These endpoints are mostly thin wrappers over database functions with no
dependency on main.py module state, which makes them clean to extract.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import (
    get_all_tags,
    get_channel_profile,
    get_pings,
    get_scan_runs,
    get_setting,
    get_settings_history,
    get_source_score,
    get_source_scores,
    recalculate_source_scores,
    set_setting,
)
from pulse_desk.api_models import SavedFiltersRequest
from pulse_desk.app_ctx import get_current_role, require_admin


router = APIRouter()


@router.get("/api/tags")
async def list_all_tags(_: str = Depends(get_current_role)):
    return await get_all_tags()


@router.get("/api/sources")
async def read_sources(_: str = Depends(get_current_role), limit: int = Query(50, ge=1, le=200)):
    await recalculate_source_scores()
    return {"sources": await get_source_scores(limit=limit)}


@router.get("/api/sources/{chat_id}")
async def read_source(chat_id: int, _: str = Depends(get_current_role)):
    await recalculate_source_scores()
    source = await get_source_score(chat_id)
    if not source:
        raise HTTPException(404, "Source not found")
    profile = await get_channel_profile(chat_id)
    recent = await get_pings(limit=1000, chat_type="all", sort_by="detected_at", sort_order="DESC")
    recent = [row for row in recent if int(row.get("chat_id") or 0) == chat_id]
    return {"source": source, "profile": profile, "recent": recent}


@router.get("/api/scan-runs", dependencies=[Depends(require_admin)])
async def read_scan_runs(limit: int = Query(20, ge=1, le=100)):
    return {"runs": await get_scan_runs(limit=limit)}


@router.get("/api/saved-filters")
async def get_saved_filters(_: str = Depends(get_current_role)):
    return {"filters": await get_setting("saved_filters", [])}


@router.put("/api/saved-filters", dependencies=[Depends(require_admin)])
async def update_saved_filters(data: SavedFiltersRequest):
    clean_filters = []
    for item in data.filters:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        if name and isinstance(query, dict):
            clean_filters.append({"name": name[:40], "query": query})
    await set_setting("saved_filters", clean_filters)
    return {"status": "ok", "filters": clean_filters}


@router.get("/api/settings/history")
async def get_settings_change_history(
    key: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: str = Depends(require_admin),
):
    return await get_settings_history(key=key, limit=limit)
