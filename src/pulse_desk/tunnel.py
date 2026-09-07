"""Publish the Mini App port over HTTPS with a Cloudflare quick tunnel.

Telegram loads a Mini App from the user's phone, so ``127.0.0.1`` is useless —
the page needs a public HTTPS origin. A quick tunnel gives one with no domain
and no account, at the cost of a fresh hostname on every start. That is fine
here because WebApp buttons are built at send time from ``state.public_url``
and never baked into a callback payload.

Only the Mini App port is published. A quick tunnel forwards a whole origin and
cannot be narrowed to a path, so pointing it at the dashboard's port would put
every ``/api/*`` route and the SSE stream on the internet.

The job never raises into the bot: a missing binary or a dead tunnel just
leaves ``state.public_url`` empty, and the WebApp buttons disappear.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from collections import deque
from typing import Optional

from .app_ctx import logger, settings, state
from .common import record_app_event

# The banner line looks like:  INF |  https://busy-fox.trycloudflare.com  |
# `api.trycloudflare.com` is the control plane, not a tunnel, so that subdomain
# is excluded rather than mistaken for an address we can hand to Telegram.
_TUNNEL_URL = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com")

# A dead tunnel restarts through start_supervised, which does not back off on a
# clean return. Pace it here so a permanently failing binary cannot spin.
RESTART_DELAY_SECONDS = 10.0

# How much of cloudflared's own output to keep for the failure log.
TAIL_LINES = 4


def extract_tunnel_url(line: Optional[str]) -> Optional[str]:
    """The quick-tunnel URL in `line`, or None when there is none."""
    match = _TUNNEL_URL.search(line or "")
    return match.group(0) if match else None


async def _publish(url: str) -> None:
    if url == state.public_url:
        return
    state.public_url = url
    logger.info("Mini App tunnel is up: %s", url)
    await record_app_event("INFO", "tunnel", "Mini App tunnel is up", {"url": url})


async def tunnel_loop() -> None:
    """Run one cloudflared process; return when it dies so the supervisor respawns.

    Started only when ``MINIAPP_ENABLED`` is true (see main.py), so there is no
    disabled-and-spinning case to guard against here.
    """
    binary = shutil.which(settings.cloudflared_bin) or settings.cloudflared_bin
    local = f"http://127.0.0.1:{settings.miniapp_port}"
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "tunnel", "--url", local, "--no-autoupdate",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("cloudflared could not be started (%s) — Mini App stays local", exc)
        await record_app_event(
            "WARNING", "tunnel", "cloudflared could not be started",
            {"binary": binary, "error": str(exc)},
        )
        # A missing binary will not appear on its own; retry rarely.
        await asyncio.sleep(RESTART_DELAY_SECONDS * 6)
        return

    logger.info("cloudflared started for %s (pid %s)", local, proc.pid)
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    published = False
    try:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            tail.append(line)
            url = extract_tunnel_url(line)
            if url:
                published = True
                await _publish(url)
        await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        raise
    finally:
        state.public_url = None
        if proc.returncode is None:
            proc.terminate()

    # A tunnel that dies without ever printing an address usually means the
    # quick-tunnel API is unreachable — some networks block
    # api.trycloudflare.com outright. Carry the child's own words into the log
    # so the cause is in app.log rather than only in a console nobody sees.
    reason = " | ".join(tail) if not published else ""
    logger.warning(
        "cloudflared exited with %s — Mini App buttons are hidden%s",
        proc.returncode, f": {reason}" if reason else "",
    )
    await record_app_event(
        "WARNING", "tunnel", "cloudflared exited",
        {"code": proc.returncode, "published": published, "tail": list(tail)},
    )
    await asyncio.sleep(RESTART_DELAY_SECONDS)
