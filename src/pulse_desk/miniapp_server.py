"""A second ASGI app carrying only the Mini App.

The tunnel forwards a whole origin, so whatever runs on the published port is
on the internet. Keeping the Mini App on its own port means the dashboard, the
SSE stream and every token-authenticated ``/api/*`` route stay bound to
localhost where they were.

The server runs as an asyncio task inside the main process — same event loop,
same database connections, same ``state`` — so nothing has to be shared across
processes.
"""
from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .app_ctx import logger, settings


def build_miniapp() -> FastAPI:
    """The Mini App ASGI app: its router plus its own static mount.

    The docs endpoints are off: this app is publicly reachable, and an OpenAPI
    schema would advertise every route to anyone who finds the tunnel URL.
    """
    from routers import miniapp as miniapp_router

    app = FastAPI(
        title="Pulse Desk Mini App",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.mount(
        "/app-static",
        StaticFiles(directory=str(settings.static_dir / "app")),
        name="app-static",
    )
    app.include_router(miniapp_router.router)
    return app


async def serve_miniapp() -> None:
    """Run the Mini App server until the process shuts down."""
    config = uvicorn.Config(
        build_miniapp(),
        host="127.0.0.1",
        port=settings.miniapp_port,
        access_log=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    logger.info("Mini App server listening on 127.0.0.1:%s", settings.miniapp_port)
    try:
        await server.serve()
    except asyncio.CancelledError:
        server.should_exit = True
        raise
