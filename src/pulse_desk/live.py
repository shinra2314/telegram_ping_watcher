"""Live event publishing — shared between main.py and routers.

This module owns the public surface for emitting live events:
  - durable backfill via the outbox table
  - in-memory fan-out via LiveHub
  - dashboard cache invalidation hook

Routers should import `publish_live_event` from here rather than from main.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from database import enqueue_outbox_event

from .app_ctx import logger, state


# Event types that, when published, must invalidate the dashboard summary cache.
_DASHBOARD_INVALIDATING_EVENTS: set[str] = {
    "ping",
    "ping-updated",
    "giveaway-candidate",
    "deadline-updated",
}


# Optional callback used by main.py to invalidate its dashboard cache.
_dashboard_invalidator: Optional[Callable[[], None]] = None


def register_dashboard_invalidator(callback: Callable[[], None]) -> None:
    """Wire up the dashboard cache invalidation hook.

    Called once from main.py during startup so this module doesn't need to
    import main (circular).
    """
    global _dashboard_invalidator
    _dashboard_invalidator = callback


async def publish_live_event(event_type: str, payload: dict[str, Any]) -> None:
    """Publish an event to both durable outbox and in-memory subscribers."""
    event_id: Optional[int] = None
    try:
        event_id = await enqueue_outbox_event(event_type, payload)
    except Exception:
        logger.debug("Could not enqueue live event", exc_info=True)
    try:
        state.live_hub.publish(event_type, payload, event_id=event_id)
    except Exception:
        logger.debug("Live hub publish failed", exc_info=True)
    if event_type in _DASHBOARD_INVALIDATING_EVENTS and _dashboard_invalidator is not None:
        try:
            _dashboard_invalidator()
        except Exception:
            logger.debug("Dashboard cache invalidation failed", exc_info=True)
