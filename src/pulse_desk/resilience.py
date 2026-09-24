"""Keeping one failure from becoming a flood, and a frozen loop from going unseen.

Everything in the app — eight accounts, the bot, the panel, ``/api/health`` —
shares one asyncio event loop. Two things follow, and this module holds the
answer to both:

* a handler that raises is reported by Telethon as ``Unhandled exception on
  <handler>`` with a full traceback, once per update. A burst of deletions during
  a lock storm wrote 142 of them in two days (24.09). :func:`guard` turns that
  into one traceback a minute per handler kind, the rest counted.
* a synchronous call anywhere freezes all of it at once, and nothing inside the
  loop can say what froze it — it is frozen. :class:`LoopWatch` watches from a
  thread and, the moment the loop misses its tick, logs the loop thread's stack:
  the culprit caught in the act, instead of a watchdog restart with no cause.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import sys
import threading
import time
import traceback
from typing import Any, Awaitable, Callable, Optional


class ThrottledErrors:
    """One traceback per ``every`` seconds per kind; the rest are counted and
    reported with the next one that is let through."""

    def __init__(self, logger: logging.Logger, every: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.logger = logger
        self.every = every
        self.clock = clock
        self.counts: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._held: dict[str, int] = {}

    def report(self, kind: str, message: str, exc: BaseException) -> bool:
        """Count the error; log it (with traceback) unless one of its kind was
        logged less than ``every`` seconds ago. Returns whether it was logged."""
        self.counts[kind] = self.counts.get(kind, 0) + 1
        now = self.clock()
        if now - self._last.get(kind, -1e18) < self.every:
            self._held[kind] = self._held.get(kind, 0) + 1
            return False
        held = self._held.pop(kind, 0)
        self._last[kind] = now
        more = f" (and {held} more since the last report)" if held else ""
        self.logger.error("%s%s", message, more, exc_info=(type(exc), exc, exc.__traceback__))
        return True

    def total(self) -> int:
        return sum(self.counts.values())


def guard(kind: str, errors: ThrottledErrors, label: str = "") -> Callable:
    """Wrap an update handler so it never raises into Telethon's dispatcher."""

    def decorate(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[None]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> None:
            try:
                await func(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.report(kind, f"Handler {kind} failed{' for ' + label if label else ''}: {exc}", exc)

        return wrapper

    return decorate


class LoopWatch:
    """Notices the event loop not getting back to its own callbacks.

    The loop stamps :attr:`last_tick` every ``tick`` seconds. A daemon thread
    looks at the stamp; older than ``stall`` seconds means something is running
    synchronously on the loop, and that thread's stack is logged — once per
    stall. When the loop comes back the stall's full length is logged too.
    """

    def __init__(self, logger: logging.Logger, *, tick: float = 0.5, stall: float = 3.0,
                 window: float = 300.0, clock: Callable[[], float] = time.monotonic):
        self.logger = logger
        self.tick = tick
        self.stall = stall
        self.window = window
        self.clock = clock
        self.last_tick = clock()
        self.lag = 0.0
        self.stalls = 0
        self.longest_stall = 0.0
        self._window_started = self.last_tick
        self._window_max = 0.0
        self._prev_window_max = 0.0
        self._stall_since: Optional[float] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[int] = None
        self._handle: Optional[asyncio.TimerHandle] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- the loop side ------------------------------------------------------
    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self.stop()
        self._loop = loop or asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._stop = threading.Event()
        self.last_tick = self.clock()
        self._handle = self._loop.call_later(self.tick, self._on_tick, self.last_tick + self.tick)
        self._thread = threading.Thread(target=self._watch, name="loop-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _on_tick(self, expected: float) -> None:
        now = self.clock()
        self.note_tick(now, max(0.0, now - expected))
        if not self._stop.is_set() and self._loop is not None:
            self._handle = self._loop.call_later(self.tick, self._on_tick, now + self.tick)

    def note_tick(self, now: float, lag: float) -> None:
        """One tick came ``lag`` seconds late (pure bookkeeping, tested directly)."""
        if now - self._window_started >= self.window:
            self._prev_window_max, self._window_max = self._window_max, 0.0
            self._window_started = now
        self.lag = lag
        self._window_max = max(self._window_max, lag)
        since = self._stall_since
        if since is not None:
            self._stall_since = None
            length = now - since
            self.longest_stall = max(self.longest_stall, length)
            self.logger.warning("Event loop is back after a %.1fs stall", length)
        self.last_tick = now

    # ---- the watching thread ------------------------------------------------
    def check(self, now: float) -> Optional[float]:
        """Seconds the loop has been silent, the first time it crosses ``stall``
        (None otherwise — including while the same stall goes on)."""
        silent = now - self.last_tick
        if silent < self.stall or self._stall_since is not None:
            return None
        self._stall_since = now - silent  # the last tick: the stall began there
        self.stalls += 1
        return silent

    def _watch(self) -> None:
        while not self._stop.wait(self.tick):
            silent = self.check(self.clock())
            if silent is not None:
                self.logger.warning("Event loop blocked for %.1fs; it is running:\n%s", silent, self.loop_stack())

    def loop_stack(self) -> str:
        frame = sys._current_frames().get(self._loop_thread) if self._loop_thread is not None else None
        if frame is None:
            return "  (loop thread not found)\n"
        return "".join(traceback.format_stack(frame, limit=25))

    def stats(self) -> dict[str, Any]:
        return {
            "loop_lag_ms": int(self.lag * 1000),
            "loop_max_lag_ms": int(max(self._window_max, self._prev_window_max) * 1000),
            "loop_stalls": self.stalls,
            "loop_longest_stall_ms": int(self.longest_stall * 1000),
            "loop_stalled_now": self._stall_since is not None,
        }
