"""Small shared helpers used across the service modules extracted from main.py."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Optional

from .app_ctx import FLOOD_WAIT_MAX_SECONDS, logger, state
from .jobs import start_supervised_task, start_tracked_task


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def parse_csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def start_background_task(name: str, coro) -> asyncio.Task:
    return start_tracked_task(state, logger, name, coro)


def start_supervised(name: str, coro_factory, *, backoff_base: float = 5.0, backoff_max: float = 300.0) -> asyncio.Task:
    return start_supervised_task(
        state, logger, name, coro_factory, backoff_base=backoff_base, backoff_max=backoff_max
    )


def flood_wait_seconds(raw_seconds: int) -> int:
    """Bound a FloodWait duration so we still respect Telegram's limit but cap absurd values."""
    try:
        value = int(raw_seconds)
    except (TypeError, ValueError):
        return FLOOD_WAIT_MAX_SECONDS
    if value <= 0:
        return 1
    return min(value, FLOOD_WAIT_MAX_SECONDS)


async def record_app_event(level: str, source: str, message: str, context: Optional[dict[str, Any]] = None) -> None:
    from database import record_event

    try:
        await record_event(level, source, message, context)
    except Exception:
        logger.debug("Could not persist app event", exc_info=True)
