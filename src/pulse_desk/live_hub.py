"""In-memory pub/sub for /api/live Server-Sent Events.

Replaces the per-client SQLite polling pattern. The outbox table stays in place
as a durable backfill channel (so clients reconnecting with a known last_id
still receive missed events), but the steady-state path is a per-subscriber
asyncio.Queue fed by the producer side.

Design notes:
- Subscribers are short-lived (one per HTTP /api/live connection).
- We deliberately use bounded queues; when a slow client falls behind, we drop
  the oldest events and signal `_lagged=True` on the next yield so the client
  can choose to do a full refresh.
- Events are kept simple dicts; the SSE serialiser lives in main.py.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator


class LiveSubscriber:
    __slots__ = ("queue", "_lagged")

    def __init__(self, maxsize: int = 200) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._lagged: bool = False

    def publish_nowait(self, event: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest to make room — newer state is more interesting than
            # arbitrarily-old missed events.
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(event)
            self._lagged = True

    async def aiter(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self.queue.get()
            if self._lagged:
                event = dict(event)
                event["_lagged"] = True
                self._lagged = False
            yield event


class LiveHub:
    """Fan-out broker. Subscribers are added at connect, removed at disconnect."""

    def __init__(self) -> None:
        self._subscribers: set[LiveSubscriber] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self, maxsize: int = 200) -> LiveSubscriber:
        sub = LiveSubscriber(maxsize=maxsize)
        async with self._lock:
            self._subscribers.add(sub)
        return sub

    async def unsubscribe(self, sub: LiveSubscriber) -> None:
        async with self._lock:
            self._subscribers.discard(sub)

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any], *, event_id: int | None = None) -> int:
        """Push to all current subscribers. Returns count of subscribers delivered to.

        This is intentionally synchronous so callers in async hot paths don't
        await on the broadcast — each subscriber's queue absorbs the event.
        """
        event = {
            "id": event_id,
            "event_type": event_type,
            "payload": payload,
        }
        # Snapshot to avoid mutation during iteration
        targets = list(self._subscribers)
        for sub in targets:
            sub.publish_nowait(event)
        return len(targets)
