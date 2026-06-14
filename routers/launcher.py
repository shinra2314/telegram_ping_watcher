"""Launcher endpoints: control + monitor external services (the Discord bot etc.).

Pulse Desk is the host shell; these endpoints drive the process supervisor that
spawns sibling runtimes. All routes are admin-only — starting/stopping local
processes is a privileged action.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse_desk.app_ctx import require_admin
from pulse_desk.process_supervisor import get_supervisor

router = APIRouter(prefix="/api/services", tags=["launcher"], dependencies=[Depends(require_admin)])


@router.get("")
async def list_services():
    sup = get_supervisor()
    services = sup.status_all()
    # Probe health for services that expose a port (cheap, parallel-friendly).
    for svc in services:
        if svc.get("has_health_probe"):
            svc["healthy"] = await sup.health(svc["name"])
    running = sum(1 for s in services if s["running"])
    return {"services": services, "total": len(services), "running": running}


def _require(name: str):
    sup = get_supervisor()
    if not sup.known(name):
        raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
    return sup


@router.post("/start-all")
async def start_all():
    return {"services": await get_supervisor().start_all()}


@router.post("/stop-all")
async def stop_all():
    return {"services": await get_supervisor().stop_all()}


@router.post("/{name}/start")
async def start_service(name: str):
    return await _require(name).start(name)


@router.post("/{name}/stop")
async def stop_service(name: str):
    return await _require(name).stop(name)


@router.post("/{name}/restart")
async def restart_service(name: str):
    return await _require(name).restart(name)


@router.get("/{name}/health")
async def service_health(name: str):
    sup = _require(name)
    return {"name": name, "healthy": await sup.health(name)}


@router.get("/{name}/logs")
async def service_logs(name: str, limit: int = Query(200, ge=1, le=400)):
    sup = _require(name)
    return {"name": name, "logs": sup.logs(name, limit=limit)}
