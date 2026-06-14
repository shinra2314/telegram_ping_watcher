"""Loads the declarative list of external services Pulse Desk should supervise.

The manifest is plain JSON (no new dependency) so it is easy to hand-edit and
diff. Resolution order:

1. ``$PULSE_SERVICES_FILE`` if set,
2. ``<base>/config/services.json`` (machine-specific, git-ignored),
3. ``<base>/config/services.example.json`` (committed template).

Paths inside ``cwd`` and ``cmd`` support ``${VAR}`` / ``$VAR`` and ``~``
expansion so the committed example stays machine-agnostic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .app_ctx import logger, settings
from .process_supervisor import ManagedService


def _config_dir() -> Path:
    return Path(settings.base_dir) / "config"


def _manifest_path() -> Path | None:
    override = os.getenv("PULSE_SERVICES_FILE", "").strip()
    if override:
        p = Path(override)
        return p if p.exists() else None
    cfg = _config_dir()
    for candidate in (cfg / "services.json", cfg / "services.example.json"):
        if candidate.exists():
            return candidate
    return None


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def _coerce_service(raw: dict[str, Any]) -> ManagedService | None:
    name = str(raw.get("name") or "").strip()
    cmd = raw.get("cmd")
    if not name or not isinstance(cmd, list) or not cmd:
        logger.warning("Skipping invalid service entry in manifest: %r", raw)
        return None
    health = raw.get("health") or {}
    cwd = raw.get("cwd")
    return ManagedService(
        name=name,
        label=str(raw.get("label") or name),
        cmd=[_expand(str(part)) for part in cmd],
        cwd=_expand(str(cwd)) if cwd else None,
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        health_host=(str(health.get("host")) if health.get("host") else None),
        health_port=(int(health["port"]) if health.get("port") else None),
        panel_url=(str(raw["panel_url"]) if raw.get("panel_url") else None),
        autostart=bool(raw.get("autostart", False)),
        autorestart=bool(raw.get("autorestart", True)),
        backoff_base=float(raw.get("backoff_base", 3.0)),
        backoff_max=float(raw.get("backoff_max", 120.0)),
        max_restarts=int(raw.get("max_restarts", 8)),
    )


def load_services() -> list[ManagedService]:
    path = _manifest_path()
    if path is None:
        logger.info("No service manifest found (config/services.json); launcher has no managed services.")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.error("Could not parse service manifest %s", path, exc_info=True)
        return []
    entries = data.get("services") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        logger.error("Service manifest %s must contain a 'services' list", path)
        return []
    services: list[ManagedService] = []
    seen: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        svc = _coerce_service(raw)
        if svc is None or svc.name in seen:
            continue
        seen.add(svc.name)
        services.append(svc)
    logger.info("Loaded %d managed service(s) from %s", len(services), path.name)
    return services
