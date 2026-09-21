"""A second ASGI app carrying only the Mini App («🛰 Панель»).

The tunnel forwards a whole origin, so whatever runs on the published port is
on the internet. Keeping the panel on its own port means ``/api/health`` on the
main app stays bound to localhost where the watchdog reads it.

The server runs as an asyncio task inside the main process — same event loop,
same database connections, same ``state`` — so nothing is shared across
processes.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .app_ctx import logger, settings
from .common import record_app_event

APP_DIR = Path(__file__).resolve().parents[2] / "static" / "app"

# The page loads Telegram's WebApp script and nothing else from outside. Inline
# scripts are not used, so none are allowed — an injected <script> goes nowhere.
CSP = (
    "default-src 'self'; "
    "script-src 'self' https://telegram.org; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors https://web.telegram.org https://*.telegram.org 'self'; "
    "base-uri 'none'; form-action 'none'"
)


async def journal(request: Request) -> None:
    """One ``miniapp`` event per action that went through: who, what, on what.

    The body is never read here — it can carry a phone number, a login code or
    a 2FA password. What a handler wants on record beyond the path it puts in
    ``request.state.audit`` (``routers.miniapp.common.audit``).
    """
    caller = getattr(request.state, "caller", None)
    context = {
        "path": request.url.path,
        "tg_id": getattr(caller, "tg_id", None),
        "role": getattr(caller, "role", None),
        **(getattr(request.state, "audit", None) or {}),
    }
    await record_app_event("INFO", "miniapp", "Panel action", context)


def build_miniapp() -> FastAPI:
    """The Mini App ASGI app: its router, its static mount and security headers.

    The docs endpoints are off: this app is publicly reachable, and an OpenAPI
    schema would advertise every route to anyone who finds the tunnel URL.
    """
    from routers import miniapp as miniapp_router

    app = FastAPI(title="Pulse Desk Mini App", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.method == "POST" and request.url.path.startswith("/api/") and response.status_code < 400:
            await journal(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            # Account lists and login answers must never sit in a proxy or disk cache.
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = CSP
            # A redeploy must reach phones at once; the files are small.
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/app-static", StaticFiles(directory=str(APP_DIR)), name="app-static")

    @app.get("/")
    @app.get("/app")
    async def app_page() -> FileResponse:
        """The shell. Unauthenticated on purpose: the HTML holds no data."""
        return FileResponse(APP_DIR / "index.html")

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
