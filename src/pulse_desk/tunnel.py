"""Publish the Mini App port over HTTPS through a tunnel.

Telegram loads a Mini App from the user's phone, so ``127.0.0.1`` is useless —
the page needs a public HTTPS origin. This job runs a tunnel agent as a child
process, reads the address out of its output, and puts it in
``state.public_url``, where the bot's WebApp buttons pick it up at send time.

Only the Mini App port is published. A tunnel forwards a whole origin and
cannot be narrowed to a path, so pointing it at the dashboard's port would put
every ``/api/*`` route and the SSE stream on the internet.

Three providers, chosen by ``TUNNEL_PROVIDER``:

``tailscale``
    ``tailscale funnel <port>`` in the foreground: it prints the address and
    stays up, which is exactly the shape this supervisor wants. The hostname
    (``<machine>.<tailnet>.ts.net``) is stable and needs no domain purchase.
    Requires Funnel to be enabled once for the tailnet.

``ngrok``
    A free account carries one reserved domain, so ``NGROK_DOMAIN`` gives a
    *stable* address too. Note that Windows Defender's PUA protection
    quarantines the ngrok binary on this machine (verified 2026-09-07), which
    is why it is not the default here.

``cloudflared``
    A quick tunnel needs no account but mints a new hostname every start.
    Note that its control host, ``api.trycloudflare.com``, is blocked on some
    networks — this machine's included (verified 2026-09-07) — while the rest
    of Cloudflare stays reachable. cloudflared then exits without ever printing
    an address, so its last output lines are carried into the log.

The job never raises into the bot: a missing binary or a dead tunnel just
leaves ``state.public_url`` empty and the WebApp buttons disappear.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from collections import deque
from typing import Callable, Optional

from .app_ctx import logger, settings, state
from .common import record_app_event

# cloudflared prints the address inside a box:  INF |  https://x.trycloudflare.com  |
# `api.trycloudflare.com` is the control plane, not a tunnel, so that subdomain
# is excluded rather than mistaken for an address we can hand to Telegram.
_CF_URL = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com")

# ngrok's logfmt carries the address as a `url=` key. The same line also holds
# `addr=http://localhost:8010`, which is why the key is matched and not just any
# URL on the line.
_NGROK_URL = re.compile(r"\burl=(https://[^\s\"]+)")

# `tailscale funnel <port>` prints its address under "Available on the internet",
# with the proxied target on the next line as plain http — hence the ts.net
# anchor, so the local target can never be mistaken for the public address.
_TS_URL = re.compile(r"https://[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.ts\.net")

# A dead tunnel restarts through start_supervised, which does not back off on a
# clean return. Pace it here so a permanently failing binary cannot spin.
RESTART_DELAY_SECONDS = 10.0

# How long to wait before retrying when the agent binary is missing entirely.
MISSING_BINARY_DELAY_SECONDS = RESTART_DELAY_SECONDS * 6

# How much of the agent's own output to keep for the failure log.
TAIL_LINES = 4


def extract_tunnel_url(line: Optional[str]) -> Optional[str]:
    """The cloudflared quick-tunnel URL in `line`, or None when there is none."""
    match = _CF_URL.search(line or "")
    return match.group(0) if match else None


def extract_ngrok_url(line: Optional[str]) -> Optional[str]:
    """The ngrok forwarding URL in `line`, or None when there is none."""
    match = _NGROK_URL.search(line or "")
    return match.group(1).rstrip("/") if match else None


def extract_tailscale_url(line: Optional[str]) -> Optional[str]:
    """The Tailscale Funnel URL in `line`, or None when there is none."""
    match = _TS_URL.search(line or "")
    return match.group(0).rstrip("/") if match else None


def tailscale_argv(binary: str, port: int) -> list[str]:
    """Foreground funnel: prints the address, then holds the tunnel open."""
    return [binary, "funnel", str(port)]


def cloudflared_argv(binary: str, port: int) -> list[str]:
    return [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]


def ngrok_argv(binary: str, port: int, domain: str = "") -> list[str]:
    """`ngrok http` in logfmt so the address is machine-readable on stdout.

    A reserved `domain` pins the address across restarts; without one ngrok
    mints a fresh hostname each start, exactly like a quick tunnel.
    """
    argv = [binary, "http", str(port), "--log", "stdout", "--log-format", "logfmt"]
    if domain:
        argv += ["--domain", domain]
    return argv


def provider_spec() -> tuple[str, list[str], Callable[[Optional[str]], Optional[str]]]:
    """(name, argv, url extractor) for the configured provider."""
    port = settings.miniapp_port
    provider = (settings.tunnel_provider or "").lower()
    if provider == "tailscale":
        binary = shutil.which(settings.tailscale_bin) or settings.tailscale_bin
        return "tailscale", tailscale_argv(binary, port), extract_tailscale_url
    if provider == "ngrok":
        binary = shutil.which(settings.ngrok_bin) or settings.ngrok_bin
        return "ngrok", ngrok_argv(binary, port, settings.ngrok_domain), extract_ngrok_url
    binary = shutil.which(settings.cloudflared_bin) or settings.cloudflared_bin
    return "cloudflared", cloudflared_argv(binary, port), extract_tunnel_url


async def _publish(url: str) -> None:
    if url == state.public_url:
        return
    state.public_url = url
    logger.info("Mini App tunnel is up: %s", url)
    await record_app_event("INFO", "tunnel", "Mini App tunnel is up", {"url": url})


async def tunnel_loop() -> None:
    """Run one tunnel agent; return when it dies so the supervisor respawns it.

    Started only when ``MINIAPP_ENABLED`` is true (see main.py), so there is no
    disabled-and-spinning case to guard against here.
    """
    name, argv, extract = provider_spec()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("%s could not be started (%s) — Mini App stays local", name, exc)
        await record_app_event(
            "WARNING", "tunnel", f"{name} could not be started",
            {"argv": argv, "error": str(exc)},
        )
        # A missing binary will not appear on its own; retry rarely.
        await asyncio.sleep(MISSING_BINARY_DELAY_SECONDS)
        return

    logger.info("%s started for port %s (pid %s)", name, settings.miniapp_port, proc.pid)
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
            url = extract(line)
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

    # An agent that dies without ever printing an address usually means its
    # control plane is unreachable or the account is not configured. Carry its
    # own words into the log so the cause lands in app.log rather than in a
    # console nobody sees.
    reason = " | ".join(tail) if not published else ""
    logger.warning(
        "%s exited with %s — Mini App buttons are hidden%s",
        name, proc.returncode, f": {reason}" if reason else "",
    )
    await record_app_event(
        "WARNING", "tunnel", f"{name} exited",
        {"code": proc.returncode, "published": published, "tail": list(tail)},
    )
    await asyncio.sleep(RESTART_DELAY_SECONDS)
