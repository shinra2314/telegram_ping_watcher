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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .app_ctx import logger, settings
from .common import record_app_event
from .resilience import ThrottledErrors

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


BUSY = "База занята — повторите через пару секунд"
INTERNAL = "Внутренняя ошибка — она записана в журнал"
# One traceback a minute per path: a broken endpoint polled by Pulse every
# 30 s must not fill the log.
_errors = ThrottledErrors(logger)


async def error_response(request: Request, exc: Exception) -> JSONResponse:
    """What an endpoint that raised answers: JSON the page can show, never a bare 500.

    A busy database is not a bug — «database is locked» gets 503 and
    ``Retry-After``, and the page retries a read once. Anything else is logged
    (throttled) with an ERROR app event, so it shows up in 🩺 Диагностика.
    """
    from database import is_locked_error

    path = request.url.path
    if is_locked_error(exc):
        return JSONResponse({"detail": BUSY}, status_code=503, headers={"Retry-After": "2"})
    if _errors.report(f"miniapp:{path}", f"Mini App {request.method} {path} failed: {exc}", exc):
        await record_app_event("ERROR", "miniapp", "Panel request failed",
                               {"path": path, "error": f"{type(exc).__name__}: {exc}"[:300]})
    return JSONResponse({"detail": INTERNAL}, status_code=500)


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
        try:
            response = await call_next(request)
        except Exception as exc:
            # Caught here, not by an exception handler: Starlette re-raises from
            # its outermost layer, and these headers would be skipped.
            response = await error_response(request, exc)
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

    @app.get("/app-sw.js")
    async def service_worker() -> FileResponse:
        """At the root so its scope covers /app. It caches the shell only,
        never /api — see static/app/sw.js."""
        return FileResponse(APP_DIR / "sw.js", media_type="application/javascript")

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
    except SystemExit as exc:
        # uvicorn calls sys.exit(1) when the port is taken. SystemExit is not an
        # Exception: it went past the supervisor and out of the event loop — the
        # whole app, bot and accounts included, died over the panel's port.
        raise RuntimeError(f"Mini App server could not start on :{settings.miniapp_port} "
                           f"(uvicorn exit {exc.code})") from None
