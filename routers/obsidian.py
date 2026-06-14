"""Obsidian "Долги" note sync endpoints.

A separate panel inside the Debts tab reads the parsed note snapshot; admins
can force a sync pass or toggle a checklist item (which writes the note and
reconciles the matched ping — the note is the source of truth).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pulse_desk.app_ctx import get_current_role, require_admin, settings, state
from pulse_desk.obsidian_debts import (
    apply_prefs,
    current_config,
    load_prefs,
    load_snapshot,
    normalize_tme_link,
    save_prefs,
    sync_once,
    toggle_item,
)


router = APIRouter()


def _empty_payload() -> dict[str, Any]:
    enabled = bool((settings.obsidian_debts_path or "").strip())
    return {
        "enabled": enabled,
        "reason": "empty" if enabled else "no_path",
        "groups": [],
        "overall": {"done": 0, "total": 0, "pct": 0},
    }


@router.get("/api/debts/obsidian")
async def read_obsidian_debts(_: str = Depends(get_current_role)):
    # Read-only display: never mutates ping statuses (that's the loop / sync).
    if state.obsidian_debts is None:
        await load_snapshot(state, settings)
    return state.obsidian_debts or _empty_payload()


@router.get("/api/debts/obsidian/config", dependencies=[Depends(require_admin)])
async def read_obsidian_config():
    return current_config(settings)


@router.post("/api/debts/obsidian/config", dependencies=[Depends(require_admin)])
async def update_obsidian_config(payload: dict = Body(...)):
    prefs: dict = {}
    if "enabled" in payload:
        prefs["enabled"] = bool(payload["enabled"])
    if "write" in payload:
        prefs["write"] = bool(payload["write"])
    if "path" in payload:
        prefs["path"] = str(payload["path"] or "")
    merged = {**(await load_prefs()), **prefs}
    apply_prefs(settings, merged)
    await save_prefs(merged)
    # Refresh the display snapshot under the new config (read-only, no mutation).
    await load_snapshot(state, settings)
    return {"ok": True, "config": current_config(settings)}


@router.post("/api/debts/obsidian/sync", dependencies=[Depends(require_admin)])
async def force_obsidian_sync():
    return await sync_once(state, settings, force=True)


@router.post("/api/debts/obsidian/toggle", dependencies=[Depends(require_admin)])
async def toggle_obsidian_item(payload: dict = Body(...)):
    link_norm = normalize_tme_link(payload.get("link") or "")
    if not link_norm:
        raise HTTPException(400, "A t.me link is required to identify the item")
    return await toggle_item(state, settings, link_norm, bool(payload.get("checked")))
