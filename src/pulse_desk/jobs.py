from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime
from typing import Any

from .runtime import AppState


def start_tracked_task(state: AppState, logger: logging.Logger, name: str, coro: Awaitable[Any]) -> asyncio.Task:
    previous = state.background_tasks.get(name)
    if previous and not previous.done():
        previous.cancel()
    state.background_task_names.add(name)
    task = asyncio.create_task(coro, name=name)
    state.background_tasks[name] = task

    def _done(_task: asyncio.Task) -> None:
        state.background_task_names.discard(name)
        if state.background_tasks.get(name) is _task:
            state.background_tasks.pop(name, None)
        if _task.cancelled():
            return
        exc = _task.exception()
        if exc:
            logger.error("Background task %s crashed", name, exc_info=(type(exc), exc, exc.__traceback__))

    task.add_done_callback(_done)
    return task


def runtime_health(state: AppState, *, accounts_online: int, accounts_configured: int) -> dict[str, Any]:
    running_tasks = sorted(name for name, task in state.background_tasks.items() if not task.done())
    expected_tasks = {"auto-scan", "reminders", "source-scores"}
    missing_tasks = sorted(expected_tasks - set(running_tasks))
    return {
        "uptime_seconds": int((datetime.now() - state.started_at).total_seconds()),
        "background_tasks": running_tasks,
        "missing_background_tasks": missing_tasks,
        "accounts_online": accounts_online,
        "accounts_configured": accounts_configured,
        "accounts_ok": accounts_online > 0 if accounts_configured else True,
    }
