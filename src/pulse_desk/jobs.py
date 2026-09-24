from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .runtime import AppState


# Jobs that must be running for the app to be considered healthy. Kept in sync
# with ``watchdog.default_thresholds`` — the two lists used to disagree, so a
# job could be watched for staleness but not for existence, or vice versa.
# Only unconditionally started jobs belong here; feature-gated ones
# (obsidian-sync, salary-sync) would otherwise page about being turned off.
EXPECTED_JOBS = frozenset({
    "auto-scan",
    "broadcast-approval",
    "access-scheduler",
    "daily-digest",
    "roulette-reminder",
    "source-scores",
    "market-fetch",
    "pending-sends",
    "notify-retry",
    "maintenance",
    "bot-janitor",
    "account-health",
    "weekly-report",
})


def expected_jobs(*, bot_configured: bool) -> set[str]:
    """Jobs that must be alive given what this install actually runs.

    ``bot-connection`` is the only thing keeping *incoming* bot updates flowing,
    so it is required whenever a bot is configured — but demanding it on an
    install with no bot token would report a permanent false outage.
    """
    expected = set(EXPECTED_JOBS)
    if bot_configured:
        expected.add("bot-connection")
    return expected


def feature_job_polls(settings: Any) -> dict[str, int]:
    """Feature-gated jobs this install actually starts, with their poll seconds.

    Mirrors the conditions in ``main.lifespan``: ``obsidian-sync`` is always
    started (it checks its own switch each pass and heartbeats either way),
    ``salary-sync`` only when a workbook path is set. Fed to
    ``watchdog.default_thresholds(enabled_features=...)``.
    """
    polls = {"obsidian-sync": max(5, int(getattr(settings, "obsidian_sync_poll_seconds", 30) or 30))}
    if (getattr(settings, "salary_xlsx_path", "") or "").strip():
        polls["salary-sync"] = max(10, int(getattr(settings, "salary_sync_poll_seconds", 60) or 60))
    return polls


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


def start_supervised_task(
    state: AppState,
    logger: logging.Logger,
    name: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    backoff_base: float = 5.0,
    backoff_max: float = 300.0,
) -> asyncio.Task:
    """Run an async job with auto-restart on crash and exponential backoff.

    coro_factory must be a zero-arg callable returning a fresh coroutine each call,
    because coroutines cannot be awaited twice.
    """

    async def _runner() -> None:
        attempt = 0
        while not state.shutting_down:
            started_at = datetime.now()
            state.job_started_at[name] = started_at
            try:
                await coro_factory()
                state.job_last_ok_at[name] = datetime.now()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except (Exception, SystemExit) as exc:
                # SystemExit too: uvicorn exits on a taken port, and past this
                # point it would stop the event loop and the whole app with it.
                attempt += 1
                state.job_restart_count[name] = state.job_restart_count.get(name, 0) + 1
                state.job_last_error[name] = f"{type(exc).__name__}: {exc}"
                logger.error("Supervised job %s crashed (attempt %d)", name, attempt, exc_info=exc)
            else:
                # Job returned normally — for "run forever" jobs this is unusual; restart anyway
                # but treat it as a clean exit (no backoff escalation)
                attempt = 0
            if state.shutting_down:
                break
            delay = min(backoff_max, backoff_base * (2 ** max(0, attempt - 1)))
            delay += random.uniform(0, min(5.0, delay * 0.2))
            await asyncio.sleep(delay)

    return start_tracked_task(state, logger, name, _runner())


def runtime_health(
    state: AppState,
    *,
    accounts_online: int,
    accounts_configured: int,
    expected_tasks: set[str] | None = None,
) -> dict[str, Any]:
    expected = expected_tasks or EXPECTED_JOBS
    running_tasks = sorted(name for name, task in state.background_tasks.items() if not task.done())
    missing_tasks = sorted(expected - set(running_tasks))
    now = datetime.now()
    job_details: dict[str, dict[str, Any]] = {}
    for job_name in sorted(set(running_tasks) | set(state.job_started_at.keys())):
        started = state.job_started_at.get(job_name)
        last_ok = state.job_last_ok_at.get(job_name)
        job_details[job_name] = {
            "running": job_name in state.background_tasks
            and not state.background_tasks[job_name].done(),
            "started_at": started.isoformat(timespec="seconds") if started else None,
            "last_ok_at": last_ok.isoformat(timespec="seconds") if last_ok else None,
            "last_ok_age_seconds": int((now - last_ok).total_seconds()) if last_ok else None,
            "restarts": state.job_restart_count.get(job_name, 0),
            "last_error": state.job_last_error.get(job_name),
        }
    return {
        "uptime_seconds": int((now - state.started_at).total_seconds()),
        "background_tasks": running_tasks,
        "missing_background_tasks": missing_tasks,
        "accounts_online": accounts_online,
        "accounts_configured": accounts_configured,
        "accounts_ok": accounts_online > 0 if accounts_configured else True,
        "jobs": job_details,
    }
