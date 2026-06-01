"""Web push subscription management.

Extracted from main.py as a self-contained slice — only depends on the
push helper module and database CRUD.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from database import (
    delete_push_subscription,
    save_push_subscription,
)
from pulse_desk.app_ctx import get_current_role, state
from pulse_desk.push import vapid_public_key_b64

router = APIRouter()


@router.get("/api/push/vapid-public-key")
async def push_vapid_public_key():
    if not state.vapid_private_pem:
        raise HTTPException(status_code=503, detail="Push not initialised")
    return {"key": vapid_public_key_b64(state.vapid_private_pem)}


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request, _: str = Depends(get_current_role)):
    body = await request.json()
    endpoint = body.get("endpoint")
    keys = body.get("keys", {})
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="endpoint, keys.p256dh and keys.auth required")
    await save_push_subscription(endpoint, keys["p256dh"], keys["auth"])
    return {"ok": True}


@router.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request, _: str = Depends(get_current_role)):
    body = await request.json()
    endpoint = body.get("endpoint")
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint required")
    await delete_push_subscription(endpoint)
    return {"ok": True}
