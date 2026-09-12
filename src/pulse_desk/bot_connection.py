"""Keep the Telegram *bot* client connected — the account clients already are.

Telethon's auto-reconnect is finite: after ``connection_retries`` failed
attempts ``MTProtoSender._reconnect`` calls ``_disconnect(error)`` and the
client stays down for good (``is_connected()`` → False, the ``disconnected``
future resolves with the error). A couple of minutes of bad network is enough,
and this box gets them regularly ("[WinError 121] The semaphore timeout period
has expired" / "Server replied with a wrong session ID" storms in app.log).

User accounts survive that because ``telegram_accounts.monitor_client_disconnect``
awaits ``client.disconnected`` and restarts them. The bot client had no such
supervisor, and the asymmetry is exactly what the owner sees as "on some days
every message in the bot is delayed":

* outgoing notifications still work — ``bot_notify.ensure_bot_connected``
  reconnects lazily right before a send;
* incoming updates do not — nothing reads them while the client is down, so a
  command or button press sits queued on Telegram's side until the next
  outgoing notification (or an app restart) happens to reconnect the client,
  at which point the whole backlog is delivered at once.

The delay is therefore however long it takes for the next ping/digest to fire —
random by nature, which is why it looks like "random days".

This module is I/O-free apart from the client calls themselves: everything it
needs (sleep, backoff, shutdown flag, state callback) is injected, so the loop
is unit-tested without a network or an event-loop clock.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Optional

from .telegram_reconnect import reconnect_delay_seconds

# Backoff for the bot client. Deliberately quicker off the mark than the
# account backoff (the bot is the owner's only interactive surface) but capped
# so a long outage doesn't reconnect in a tight loop.
BOT_RECONNECT_BASE_SECONDS = 10
BOT_RECONNECT_MAX_SECONDS = 300
BOT_RECONNECT_JITTER_SECONDS = 20

# How long to block on the ``disconnected`` future before re-checking
# ``is_connected()``. Bounded so a client that dies without ever resolving that
# future is still noticed within one poll.
BOT_CONNECTION_POLL_SECONDS = 15.0


def bot_reconnect_delay_seconds(attempt: int) -> int:
    """Seconds to wait before reconnect attempt ``attempt`` (1-based)."""
    return reconnect_delay_seconds(
        "pulse_bot",
        attempt,
        base_seconds=BOT_RECONNECT_BASE_SECONDS,
        max_seconds=BOT_RECONNECT_MAX_SECONDS,
        jitter_seconds=BOT_RECONNECT_JITTER_SECONDS,
    )


async def _wait_for_drop(client: Any, timeout: float) -> None:
    """Wait until the client reports a disconnect, or ``timeout`` elapses.

    ``client.disconnected`` is shielded by Telethon, so the timeout cancelling
    our wrapper never cancels the underlying future. A dead client resolves it
    with the error that killed the connection — that is a normal outcome here,
    not a failure, hence the broad ``except``.
    """
    try:
        await asyncio.wait_for(client.disconnected, timeout)
    except asyncio.CancelledError:
        raise
    except Exception:
        pass


async def watch_bot_connection(
    client: Any,
    *,
    poll_seconds: float = BOT_CONNECTION_POLL_SECONDS,
    sleep: Callable[[float], Any] = asyncio.sleep,
    delay_for: Callable[[int], float] = bot_reconnect_delay_seconds,
    should_stop: Optional[Callable[[], bool]] = None,
    on_change: Optional[Callable[[bool, int], None]] = None,
    on_alive: Optional[Callable[[], None]] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """Reconnect the bot client forever, whenever Telethon gives up on it.

    Runs until ``should_stop()`` returns True (shutdown). ``on_change`` is
    called with ``(connected, attempt)`` on every transition, so the caller can
    log it and expose the outage in ``/api/health``. ``on_alive`` fires once per
    pass — this loop never returns, so it is the only thing that can tell the
    watchdog the supervisor itself is still running.
    """
    log = logger or logging.getLogger(__name__)
    stop = should_stop or (lambda: False)
    attempt = 0
    connected = True

    while not stop():
        _notify_alive(on_alive, log)
        if client.is_connected():
            if not connected:
                connected = True
                log.info("Telegram bot connection restored after %d attempt(s)", attempt)
                _notify(on_change, True, attempt, log)
                attempt = 0
            await _wait_for_drop(client, poll_seconds)
            continue

        if connected:
            connected = False
            log.warning("Telegram bot client is disconnected; reconnecting")
            _notify(on_change, False, attempt, log)

        attempt += 1
        try:
            await client.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram bot reconnect attempt %d failed: %s", attempt, exc)

        if client.is_connected():
            continue
        await sleep(delay_for(attempt))


def _notify_alive(on_alive: Optional[Callable[[], None]], log: logging.Logger) -> None:
    if on_alive is None:
        return
    try:
        on_alive()
    except Exception:
        log.debug("Bot connection liveness callback failed", exc_info=True)


def _notify(
    on_change: Optional[Callable[[bool, int], None]],
    connected: bool,
    attempt: int,
    log: logging.Logger,
) -> None:
    if on_change is None:
        return
    try:
        on_change(connected, attempt)
    except Exception:
        log.debug("Bot connection state callback failed", exc_info=True)
