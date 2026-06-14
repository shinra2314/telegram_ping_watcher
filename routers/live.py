"""Server-Sent Events endpoint for live dashboard updates."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from database import get_outbox_after
from pulse_desk.app_ctx import get_current_role, logger, state

router = APIRouter()


@router.get("/api/live")
async def live_events(request: Request, role: str = Depends(get_current_role)):
    """Server-Sent Events: in-memory pub/sub with durable backfill from outbox.

    On connect:
      1. If client sent ?last_id=N, drain outbox for events with id > N (catches
         up missed events while disconnected).
      2. Subscribe to the live hub; new events are pushed without polling.

    Keepalives are sent every 20 s of inactivity so proxies don't close the
    connection.
    """
    last_id = int(request.query_params.get("last_id", "0") or 0)
    subscriber = await state.live_hub.subscribe()

    async def stream():
        nonlocal last_id
        try:
            # Backfill missed events from durable storage
            if last_id > 0:
                try:
                    backfill = await get_outbox_after(last_id, limit=200)
                    for row in backfill:
                        last_id = int(row["id"])
                        data = json.dumps(row["payload"], ensure_ascii=False)
                        yield f"id: {last_id}\nevent: {row['event_type']}\ndata: {data}\n\n"
                except Exception:
                    logger.debug("Outbox backfill failed", exc_info=True)
            yield ": connected\n\n"
            # Live phase: await pushes via the queue with periodic keepalive.
            keepalive_after = 20.0  # seconds
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=keepalive_after)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                event_id = event.get("id")
                if event_id is not None:
                    last_id = max(last_id, int(event_id))
                payload = event.get("payload") or {}
                if subscriber._lagged:
                    payload = dict(payload)
                    payload["_lagged"] = True
                    subscriber._lagged = False
                data = json.dumps(payload, ensure_ascii=False)
                etype = event.get("event_type") or "message"
                prefix = f"id: {event_id}\n" if event_id is not None else ""
                yield f"{prefix}event: {etype}\ndata: {data}\n\n"
        finally:
            await state.live_hub.unsubscribe(subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")
