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
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
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

# The app runs under pythonw, which has no console, so Windows gives every
# console child (tailscale.exe, powershell.exe) a visible window of its own —
# an empty terminal that sat open for as long as the tunnel was up. Output is
# read through pipes and the agent is stopped with TerminateProcess, so nothing
# needs that console.
_NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)

# A dead tunnel restarts through start_supervised, which does not back off on a
# clean return. Pace it here so a permanently failing binary cannot spin.
RESTART_DELAY_SECONDS = 10.0

# How long to wait before retrying when the agent binary is missing entirely.
MISSING_BINARY_DELAY_SECONDS = RESTART_DELAY_SECONDS * 6

# How much of the agent's own output to keep for the failure log.
TAIL_LINES = 4

# Repeated failures back off up to this, on top of the supervisor's own pause.
MAX_RETRY_DELAY_SECONDS = 120.0

# The panel has been unreachable this long → tell the owner, once per outage.
DOWN_ALERT_SECONDS = 15 * 60

# How often a running agent that has not printed an address yet is re-checked
# against the outage alert. `tailscale funnel` on a tailnet without Funnel
# prints an enable-link and then waits forever without exiting.
URL_WAIT_SECONDS = 60.0


@dataclass
class TunnelHealth:
    """Outage bookkeeping that outlives one agent run (module-level, in memory).

    After the PC boots, tailscaled needs a few minutes ("unexpected state:
    NoState"), and each ~20 s retry used to write the same warning into
    app_events and pay for a PowerShell orphan scan — a dozen of each every
    morning — while an outage that never ended told nobody at all.
    """

    failures: int = 0
    last_reason: str = ""
    down_since: Optional[datetime] = None
    alerted: bool = False


_health = TunnelHealth()


def retry_delay(failures: int) -> float:
    """Pause after the n-th consecutive failed run: 10, 20, 40 … 120 s."""
    if failures <= 1:
        return RESTART_DELAY_SECONDS
    return min(MAX_RETRY_DELAY_SECONDS, RESTART_DELAY_SECONDS * 2 ** (failures - 1))


def failure_reason(tail: list[str], code: Optional[int]) -> str:
    """The agent's last words, or its exit code when it said nothing."""
    for line in reversed(tail):
        if line.strip():
            return line.strip()[:200]
    return f"exit code {code}"


def note_failure(health: TunnelHealth, reason: str) -> bool:
    """Count a failed run; True when it is worth an app event (new streak or new cause)."""
    health.failures += 1
    changed = reason != health.last_reason
    health.last_reason = reason
    return health.failures == 1 or changed


def note_up(health: TunnelHealth) -> bool:
    """Reset after an address was published; True when the owner was told it was down."""
    was_alerted = health.alerted
    health.failures = 0
    health.last_reason = ""
    health.down_since = None
    health.alerted = False
    return was_alerted


def orphan_scan_due(health: TunnelHealth) -> bool:
    """Scan for orphans on a fresh start, or when the port is held by someone."""
    return health.failures == 0 or _ALREADY_EXISTS in health.last_reason


def down_alert_due(health: TunnelHealth, now: datetime) -> bool:
    return (
        not health.alerted
        and health.down_since is not None
        and (now - health.down_since).total_seconds() >= DOWN_ALERT_SECONDS
    )


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
    if note_up(_health):
        await _tell_owner("✅ **Панель снова доступна** — кнопка «🛰 Панель» вернулась.")


async def _tell_owner(text: str) -> None:
    try:
        from .bot_notify import send_admin_bot_message

        await send_admin_bot_message(text, kind="system")
    except Exception:
        logger.debug("Could not tell the owner about the tunnel", exc_info=True)


async def _check_down_alert(name: str) -> None:
    if not down_alert_due(_health, datetime.now()):
        return
    _health.alerted = True
    minutes = int((datetime.now() - _health.down_since).total_seconds() // 60)
    reason = _health.last_reason or "агент запущен, но адрес не выдал"
    await record_app_event("WARNING", "tunnel", "Mini App tunnel is down",
                           {"provider": name, "minutes": minutes, "reason": reason})
    await _tell_owner(
        f"🛰 **Панель недоступна уже {minutes} мин** — туннель `{name}` не поднимается.\n"
        f"Причина: `{reason}`\n"
        "Бот работает как обычно; кнопка «🛰 Панель» вернётся сама, когда туннель поднимется."
    )


# --------------------------------------------------------------------------- #
# Orphaned agents                                                               #
# --------------------------------------------------------------------------- #
# Windows does not kill a child when its parent dies. `restart_app.ps1` and the
# watchdog stop python.exe with a force-kill, so the `tailscale funnel 8010` it
# had started lived on, kept the port-443 listener, and every new agent failed
# with "listener already exists for port 443" — the panel stayed hidden until
# someone killed the orphan by hand. Two defences: the agent is put in a
# kill-on-close job object, so it dies with the app however the app dies; and
# before starting, an agent for our port whose parent is gone is terminated.

_AGENT_EXES = {"tailscale": "tailscale.exe", "ngrok": "ngrok.exe", "cloudflared": "cloudflared.exe"}
_ALREADY_EXISTS = "listener already exists"
_job_handle = None


def is_agent_for_port(command_line: str, provider: str, port: int) -> bool:
    """Does this command line run the tunnel agent for our port?"""
    text = (command_line or "").lower()
    port_s = str(port)
    if provider == "tailscale":
        return "funnel" in text and re.search(rf"(?<!\d){port_s}(?!\d)", text) is not None
    if provider == "ngrok":
        return " http " in f" {text} " and re.search(rf"(?<!\d){port_s}(?!\d)", text) is not None
    return "tunnel" in text and f":{port_s}" in text


def orphaned_agents(processes: list[dict], provider: str, port: int) -> list[int]:
    """PIDs of our-port agents whose parent process no longer exists.

    ``processes`` rows: ``{"pid", "command_line", "parent_alive"}``. An agent
    with a live parent belongs to a running app (maybe this one) and is left
    alone.
    """
    return [int(p["pid"]) for p in processes
            if not p.get("parent_alive") and is_agent_for_port(str(p.get("command_line") or ""), provider, port)]


async def _list_agent_processes(provider: str) -> list[dict]:
    """Agent processes with whether their parent is still alive (Windows only)."""
    exe = _AGENT_EXES.get(provider)
    if sys.platform != "win32" or not exe:
        return []
    script = (
        f"Get-CimInstance Win32_Process -Filter \"Name='{exe}'\" | ForEach-Object {{ "
        # A reused PID is not the parent: the parent must predate the child.
        "$p = Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue; "
        "$alive = $false; if ($p) { try { $alive = $p.StartTime -le $_.CreationDate } catch { $alive = $true } }; "
        "[pscustomobject]@{ pid = $_.ProcessId; command_line = $_.CommandLine; parent_alive = $alive } "
        "} | ConvertTo-Json -Compress"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            **_NO_WINDOW,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception:
        logger.debug("Could not list %s processes", exe, exc_info=True)
        return []
    raw = out.decode("utf-8", "replace").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


async def kill_orphaned_agents(provider: str, port: int) -> list[int]:
    killed: list[int] = []
    for pid in orphaned_agents(await _list_agent_processes(provider), provider, port):
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            logger.debug("Could not stop orphaned %s pid %s", provider, pid, exc_info=True)
    if killed:
        logger.warning("Stopped orphaned %s agent(s) left by a previous run: %s", provider, killed)
        await record_app_event("WARNING", "tunnel", "Orphaned tunnel agent stopped",
                               {"provider": provider, "pids": killed})
        # Give the tailscale daemon a moment to drop the dead agent's listener.
        await asyncio.sleep(2)
    return killed


def _bind_to_app_lifetime(pid: int) -> None:
    """Put the agent in a kill-on-close job: it dies when this process dies.

    Best effort (Windows only) — failure just leaves the orphan cleanup above
    as the only defence.
    """
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        if _job_handle is None:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                kernel32.CloseHandle(job)
                return
            # Deliberately never closed: the handle closes when this process
            # exits, and that is what kills the agent.
            _job_handle = job
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not handle:
            return
        try:
            kernel32.AssignProcessToJobObject(_job_handle, handle)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        logger.debug("Could not bind tunnel agent %s to the app lifetime", pid, exc_info=True)


async def tunnel_loop() -> None:
    """Run one tunnel agent; return when it dies so the supervisor respawns it.

    Started only when ``MINIAPP_ENABLED`` is true (see main.py), so there is no
    disabled-and-spinning case to guard against here.
    """
    name, argv, extract = provider_spec()
    if state.public_url is None and _health.down_since is None:
        _health.down_since = datetime.now()
    if orphan_scan_due(_health):
        await kill_orphaned_agents(name, settings.miniapp_port)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **_NO_WINDOW,
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        logger.warning("%s could not be started (%s) — Mini App stays local", name, exc)
        if note_failure(_health, f"{name} could not be started: {exc}"):
            await record_app_event(
                "WARNING", "tunnel", f"{name} could not be started",
                {"argv": argv, "error": str(exc)},
            )
        await _check_down_alert(name)
        # A missing binary will not appear on its own; retry rarely.
        await asyncio.sleep(MISSING_BINARY_DELAY_SECONDS)
        return

    _bind_to_app_lifetime(proc.pid)
    logger.info("%s started for port %s (pid %s)", name, settings.miniapp_port, proc.pid)
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    published = False
    try:
        assert proc.stdout is not None
        while True:
            if published:
                raw = await proc.stdout.readline()
            else:
                # Cancelling readline on timeout keeps the buffered bytes, so a
                # half-read line is not lost.
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), URL_WAIT_SECONDS)
                except asyncio.TimeoutError:
                    await _check_down_alert(name)
                    continue
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
    if published:
        _health.down_since = datetime.now()  # it was up until this moment
    if note_failure(_health, failure_reason(list(tail), proc.returncode)) or published:
        await record_app_event(
            "WARNING", "tunnel", f"{name} exited",
            {"code": proc.returncode, "published": published, "tail": list(tail),
             "failures": _health.failures},
        )
    await _check_down_alert(name)
    await asyncio.sleep(retry_delay(_health.failures))
