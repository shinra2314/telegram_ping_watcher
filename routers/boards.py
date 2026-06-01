"""Read-only boards: tasks, debts, giveaways board/candidates/actions.

These endpoints are pure DB reads with some constant references — clean
to extract without touching main.py state.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from database import (
    get_debt_board,
    get_giveaway_actions,
    get_giveaway_board,
    get_giveaway_candidates,
    get_task_overview,
    seed_giveaway_candidates_from_pings,
)
from pulse_desk.app_ctx import (
    GIVEAWAY_ACTION_ACCOUNT,
    GIVEAWAY_REVIEW_MODE,
    get_current_role,
    state,
)


router = APIRouter()


@router.get("/api/tasks")
async def read_tasks(_: str = Depends(get_current_role), limit: int = Query(300, ge=1, le=1000)):
    return await get_task_overview(limit=limit)


@router.get("/api/debts")
async def read_debts(_: str = Depends(get_current_role), limit: int = Query(160, ge=10, le=500)):
    return await get_debt_board(state.ping_usernames, limit=limit)


@router.get("/api/giveaways/board")
async def read_giveaway_board(_: str = Depends(get_current_role), limit: int = Query(80, ge=10, le=300)):
    return await get_giveaway_board(limit=limit)


@router.get("/api/giveaways/candidates")
async def read_giveaway_candidates(
    _: str = Depends(get_current_role),
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    seeded = await seed_giveaway_candidates_from_pings(limit=limit)
    return {
        "action_account": f"@{GIVEAWAY_ACTION_ACCOUNT}",
        "review_mode": GIVEAWAY_REVIEW_MODE,
        "seeded": seeded,
        "candidates": await get_giveaway_candidates(status=status, limit=limit),
    }


@router.get("/api/giveaways/{ping_id}/actions")
async def read_giveaway_actions(ping_id: int, _: str = Depends(get_current_role)):
    return {"actions": await get_giveaway_actions(ping_id)}
