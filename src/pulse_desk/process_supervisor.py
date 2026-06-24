"""Launcher / process supervisor for external sibling services.

Pulse Desk acts as the host shell: this module spawns and supervises *other*
runtimes (the Discord bot, any future helper) as child processes, so a single
auto-started Pulse Desk process brings the whole stack up.

Design goals (see docs/DASHBOARD.md):
- **Stdlib only.** Uses ``subprocess.Popen`` + a daemon reader thread + an
  asyncio supervise loop. Deliberately avoids ``asyncio.create_subprocess_exec``
  so it works regardless of the event-loop policy uvicorn picks on Windows
  (Proactor vs Selector). ``psutil`` is used *only if installed* for CPU/RAM.
- **Auto-restart with capped exponential backoff**, mirroring the in-process
  job supervisor in ``jobs.py`` so the behaviour is familiar.
- **Never block the event loop.** Spawning and termination are cheap; output is
  drained on a background thread into a bounded deque.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import asyncio

from .app_ctx import logger

try:  # optional: richer metrics when available, degrade gracefully otherwise
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is an optional extra
    psutil = None  # type: ignore

LOG_TAIL_LIMIT = 400


@dataclass
class ManagedService:
    """Declarative description of one external service to supervise."""

    name: str
    label: str
    cmd: list[str]
    cwd: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)
    health_host: Optional[str] = None
    health_port: Optional[int] = None
    panel_url: Optional[str] = None
    autostart: bool = False
    autorestart: bool = True
    backoff_base: float = 3.0
    backoff_max: float = 120.0
    max_restarts: int = 8

    def has_health_probe(self) -> bool:
        return bool(self.health_host and self.health_port)


@dataclass
class _Runtime:
    proc: Optional[subprocess.Popen] = None
    status: str = "stopped"  # stopped | starting | running | crashed | failed | stopping
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    restarts: int = 0
    last_error: Optional[str] = None
    manual_stop: bool = True
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_TAIL_LIMIT))
    reader: Optional[threading.Thread] = None
    supervisor_task: Optional[asyncio.Task] = None


class ServiceSupervisor:
    """Owns and supervises a registry of :class:`ManagedService` children."""

    def __init__(self) -> None:
        self.services: dict[str, ManagedService] = {}
        self.runtime: dict[str, _Runtime] = {}
        self.shutting_down = False

    # -- registry ---------------------------------------------------------
    def register(self, svc: ManagedService) -> None:
        self.services[svc.name] = svc
        self.runtime.setdefault(svc.name, _Runtime())

    def known(self, name: str) -> bool:
        return name in self.services

    # -- spawning ---------------------------------------------------------
    def _spawn(self, svc: ManagedService, rt: _Runtime) -> None:
        env = os.environ.copy()
        env.update({k: str(v) for k, v in (svc.env or {}).items()})
        creationflags = 0
        if sys.platform == "win32":
            # Don't pop a console window for GUI-less child processes.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            svc.cmd,
            cwd=svc.cwd or None,
            env=env,
            stdin=subprocess.DEVNULL,  # never inherit parent stdin: under pythonw
            # (no console) the parent's std handles are invalid and inheriting
            # them makes Popen fail / can abort the host process.
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        rt.proc = proc
        rt.last_error = None
        rt.log.append(f"[{_now_hms()}] >>> started pid={proc.pid}: {' '.join(svc.cmd)}")
        reader = threading.Thread(
            target=self._drain_output, args=(svc.name, proc), name=f"svc-reader:{svc.name}", daemon=True
        )
        reader.start()
        rt.reader = reader

    def _drain_output(self, name: str, proc: subprocess.Popen) -> None:
        rt = self.runtime[name]
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                rt.log.append(line.rstrip("\n"))
        except Exception:  # pragma: no cover - reader is best-effort
            pass

    # -- lifecycle --------------------------------------------------------
    async def start(self, name: str) -> dict[str, Any]:
        svc = self._require(name)
        rt = self.runtime[name]
        if rt.supervisor_task and not rt.supervisor_task.done():
            return self.status(name)
        rt.manual_stop = False
        rt.status = "starting"
        rt.supervisor_task = asyncio.create_task(self._supervise(name), name=f"svc-supervise:{name}")
        await self._log_event("INFO", f"Service {svc.label} start requested")
        # Give the spawn a brief moment so the first status reflects reality.
        await asyncio.sleep(0.2)
        return self.status(name)

    async def stop(self, name: str, *, timeout: float = 10.0) -> dict[str, Any]:
        svc = self._require(name)
        rt = self.runtime[name]
        rt.manual_stop = True
        rt.status = "stopping"
        if rt.supervisor_task and not rt.supervisor_task.done():
            rt.supervisor_task.cancel()
        await self._terminate(rt, timeout=timeout)
        rt.status = "stopped"
        rt.stopped_at = datetime.now()
        await self._log_event("INFO", f"Service {svc.label} stopped")
        return self.status(name)

    async def restart(self, name: str) -> dict[str, Any]:
        await self.stop(name)
        return await self.start(name)

    async def _terminate(self, rt: _Runtime, *, timeout: float) -> None:
        proc = rt.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        # Wait off the event loop so we don't block other requests.
        deadline = time.monotonic() + timeout
        while proc.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        rt.log.append(f"[{_now_hms()}] <<< terminated pid={proc.pid}")

    async def _supervise(self, name: str) -> None:
        svc = self._require(name)
        rt = self.runtime[name]
        attempt = 0
        try:
            while not self.shutting_down and not rt.manual_stop:
                if rt.proc is None or rt.proc.poll() is not None:
                    try:
                        self._spawn(svc, rt)
                        rt.started_at = datetime.now()
                        rt.status = "running"
                    except Exception as exc:  # spawn failed (bad path, missing node, ...)
                        attempt += 1
                        rt.last_error = f"{type(exc).__name__}: {exc}"
                        rt.status = "crashed"
                        logger.error("Service %s failed to spawn (attempt %d)", name, attempt, exc_info=exc)
                        await self._log_event("ERROR", f"Service {svc.label} spawn failed: {rt.last_error}")
                        if not svc.autorestart or attempt > svc.max_restarts:
                            rt.status = "failed"
                            return
                        await asyncio.sleep(_backoff(svc, attempt))
                        continue
                # Watch the running process.
                while not self.shutting_down and not rt.manual_stop:
                    if rt.proc.poll() is not None:
                        break
                    await asyncio.sleep(1.0)
                if rt.manual_stop or self.shutting_down:
                    return
                code = rt.proc.poll()
                rt.restarts += 1
                attempt += 1
                rt.last_error = f"exited with code {code}"
                rt.status = "crashed"
                rt.log.append(f"[{_now_hms()}] !!! exited code={code}")
                logger.warning("Service %s exited (code=%s, attempt %d)", name, code, attempt)
                await self._log_event("WARNING", f"Service {svc.label} exited (code={code})")
                if not svc.autorestart or attempt > svc.max_restarts:
                    rt.status = "failed"
                    return
                await asyncio.sleep(_backoff(svc, attempt))
        except asyncio.CancelledError:
            raise

    # -- introspection ----------------------------------------------------
    async def health(self, name: str) -> Optional[bool]:
        svc = self._require(name)
        if not svc.has_health_probe():
            return None
        return await _tcp_probe(svc.health_host, int(svc.health_port))

    def status(self, name: str) -> dict[str, Any]:
        svc = self._require(name)
        rt = self.runtime[name]
        alive = bool(rt.proc and rt.proc.poll() is None)
        uptime = int((datetime.now() - rt.started_at).total_seconds()) if (alive and rt.started_at) else 0
        info: dict[str, Any] = {
            "name": svc.name,
            "label": svc.label,
            "status": "running" if alive else rt.status,
            "running": alive,
            "pid": rt.proc.pid if alive and rt.proc else None,
            "uptime_seconds": uptime,
            "restarts": rt.restarts,
            "last_error": rt.last_error,
            "autostart": svc.autostart,
            "autorestart": svc.autorestart,
            "panel_url": svc.panel_url,
            "has_health_probe": svc.has_health_probe(),
            "cmd": " ".join(svc.cmd),
            "cwd": svc.cwd,
        }
        if psutil and alive and rt.proc:
            info["metrics"] = _proc_metrics(rt.proc.pid)
        return info

    def status_all(self) -> list[dict[str, Any]]:
        return [self.status(name) for name in self.services]

    def logs(self, name: str, limit: int = 200) -> list[str]:
        rt = self.runtime[self._require(name).name]
        limit = max(1, min(limit, LOG_TAIL_LIMIT))
        return list(rt.log)[-limit:]

    # -- bulk ops ---------------------------------------------------------
    async def autostart_enabled(self) -> None:
        for name, svc in self.services.items():
            if svc.autostart:
                logger.info("Autostarting service %s", name)
                await self.start(name)

    async def start_all(self) -> list[dict[str, Any]]:
        for name in self.services:
            await self.start(name)
        return self.status_all()

    async def stop_all(self) -> list[dict[str, Any]]:
        for name in self.services:
            await self.stop(name)
        return self.status_all()

    async def shutdown(self) -> None:
        self.shutting_down = True
        for name in list(self.services):
            rt = self.runtime[name]
            if rt.supervisor_task and not rt.supervisor_task.done():
                rt.supervisor_task.cancel()
            await self._terminate(rt, timeout=8.0)

    # -- helpers ----------------------------------------------------------
    def _require(self, name: str) -> ManagedService:
        svc = self.services.get(name)
        if svc is None:
            raise KeyError(name)
        return svc

    async def _log_event(self, level: str, message: str) -> None:
        # Lazy import to avoid an import cycle (common -> jobs -> app_ctx).
        try:
            from .common import record_app_event

            await record_app_event(level, "launcher", message)
        except Exception:
            logger.debug("Could not record launcher event", exc_info=True)


def _backoff(svc: ManagedService, attempt: int) -> float:
    return min(svc.backoff_max, svc.backoff_base * (2 ** max(0, attempt - 1)))


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _proc_metrics(pid: int) -> dict[str, Any]:  # pragma: no cover - requires psutil
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            return {
                "cpu_percent": p.cpu_percent(interval=None),
                "rss_mb": round(p.memory_info().rss / 1024 / 1024, 1),
                "threads": p.num_threads(),
            }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_supervisor: Optional[ServiceSupervisor] = None


def get_supervisor() -> ServiceSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = ServiceSupervisor()
    return _supervisor
