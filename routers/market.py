from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from database import get_market_history
from pulse_desk.app_ctx import get_current_role

router = APIRouter()


@router.get("/api/market")
async def read_market(role: str = Depends(get_current_role)):
    return await get_market_history(limit=1)


@router.get("/api/market-current")
async def market_current(role: str = Depends(get_current_role)):
    return await get_market_history(limit=1)


@router.get("/api/market-history-full")
async def get_market_history_full(role: str = Depends(get_current_role), limit: int = Query(24, ge=1, le=500)):
    return await get_market_history(limit=limit)
