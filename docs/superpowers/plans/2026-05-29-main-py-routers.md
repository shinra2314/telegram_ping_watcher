# main.py → FastAPI Routers Refactoring Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 3000-line `main.py` into domain-specific router files with no behaviour changes. App must pass all existing tests and start correctly after refactoring.

**Architecture:** Create `src/pulse_desk/app_ctx.py` that holds `state`, `settings`, `logger`, and auth dependencies — the shared "context" that every router needs. Create `routers/` directory with one file per domain. `main.py` becomes a ~200-line orchestrator: imports, middleware, lifespan, router includes.

**Tech Stack:** FastAPI `APIRouter`, existing `AppState`, existing `get_settings()`.

> **WARNING:** This is a high-risk structural refactoring. Run tests after every task. Commit after every task. If anything breaks, revert the task and investigate.

---

## File Map

| New file | Moved from `main.py` |
|----------|----------------------|
| `src/pulse_desk/app_ctx.py` | `state`, `settings`, `logger`, all constants, `get_current_role`, `require_admin`, auth helpers |
| `routers/core.py` | `/`, `/favicon.ico`, `/api/health`, `/api/session`, `/api/status`, `/api/diagnostics`, `/api/share-guide`, `/api/setup-check` |
| `routers/auth.py` | `/api/auth/send-code`, `/api/auth/sign-in` |
| `routers/pings.py` | `/api/pings`, `/api/pings/*`, `/api/search/rebuild` |
| `routers/analytics.py` | `/api/analytics`, `/api/dashboard/summary`, `/api/analytics/detailed`, `/api/stats/detailed`, `/api/tasks`, `/api/debts`, `/api/report-html` |
| `routers/giveaways.py` | `/api/giveaways/*` |
| `routers/sources.py` | `/api/sources/*`, `/api/channels/*` |
| `routers/settings.py` | `/api/settings/*`, `/api/saved-filters` |
| `routers/market.py` | `/api/market`, `/api/market-history-full` |
| `routers/export.py` | `/api/export-csv`, `/api/export-json` |
| `routers/scan.py` | `/api/scan-history`, `/api/scan-history/cancel`, `/api/scan-status`, `/api/scan-runs` |
| `routers/accounts.py` | `/api/accounts`, `/api/accounts/*` |
| `routers/system.py` | `/api/backups/*`, `/api/events`, `/api/logs` |
| `routers/live.py` | `/api/live` (SSE stream) |

---

### Task 1: Create `src/pulse_desk/app_ctx.py`

This is the prerequisite for all other tasks. It must not import from `main.py`.

**Files:**
- Create: `src/pulse_desk/app_ctx.py`

- [ ] **Step 1: Create `src/pulse_desk/app_ctx.py`**

```python
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from .config import get_settings
from .logging_config import configure_logging
from .runtime import AppState
from .scan import normalize_recent_edit_scan_limit, normalize_scan_history_limit
from .security import mask_secret, token_matches
from .telegram_reconnect import reconnect_delay_seconds as _calc_reconnect_delay

settings = get_settings()
state = AppState()

LOG_FILE = settings.log_file
logger = configure_logging(LOG_FILE, settings.log_level, settings.telethon_log_level)

# Derived constants (mirrors the module-level block in old main.py)
ADMIN_TOKEN: str = settings.effective_admin_token
VIEWER_TOKEN: str = settings.viewer_token.strip()
WEB_AUTH_TOKEN: str = settings.web_auth_token.strip()
PUBLIC_SHARE_MODE: bool = settings.public_share_mode
ALLOW_QUERY_TOKEN: bool = settings.allow_query_token
AUTO_JOIN_GIVEAWAYS: bool = settings.auto_join_giveaways
DRY_RUN_GIVEAWAYS: bool = settings.dry_run_giveaways
GIVEAWAY_ACTION_ACCOUNT: str = settings.giveaway_action_account.strip().lstrip("@")
GIVEAWAY_REVIEW_MODE: str = settings.giveaway_review_mode.strip().lower() or "manual"
GIVEAWAY_ANALYZE_RECENT_MESSAGES: int = settings.giveaway_analyze_recent_messages
GIVEAWAY_INACTIVE_CHANNEL_DAYS: int = settings.giveaway_inactive_channel_days
GIVEAWAY_MIN_ACTION_DELAY_SECONDS: int = settings.giveaway_min_action_delay_seconds
SCAN_INTERVAL_SECONDS: int = settings.scan_interval_seconds
SCAN_ACCOUNT_CONCURRENCY: int = max(1, min(8, settings.scan_account_concurrency))
SCAN_HISTORY_LIMIT: int = normalize_scan_history_limit(settings.scan_history_limit)
EDIT_SCAN_RECENT_MESSAGES: int = normalize_recent_edit_scan_limit(settings.edit_scan_recent_messages)
STARTUP_SCAN_DELAY_SECONDS: int = max(0, min(300, settings.startup_scan_delay_seconds))
STARTUP_SCAN_WAIT_SECONDS: int = max(0, min(300, settings.startup_scan_wait_seconds))
TELEGRAM_CONNECT_TIMEOUT_SECONDS: int = max(5, min(120, settings.telegram_connect_timeout_seconds))
TELEGRAM_RETRY_DELAY_SECONDS: int = max(1, min(60, settings.telegram_retry_delay_seconds))
TELEGRAM_RECONNECT_BASE_SECONDS: int = max(5, min(600, settings.telegram_reconnect_base_seconds))
TELEGRAM_RECONNECT_MAX_SECONDS: int = max(TELEGRAM_RECONNECT_BASE_SECONDS, min(1800, settings.telegram_reconnect_max_seconds))
TELEGRAM_RECONNECT_JITTER_SECONDS: int = max(0, min(120, settings.telegram_reconnect_jitter_seconds))
MARKET_POLL_SECONDS: int = settings.market_poll_seconds
MARKET_ALERT_CHANGE_PCT: float = settings.market_alert_change_pct
MARKET_RETENTION_DAYS: int = settings.market_retention_days
PENDING_AUTH_TTL_SECONDS: int = settings.pending_auth_ttl_seconds
ADMIN_ID: Optional[int] = settings.admin_id


def get_current_role(
    request: Request,
    x_pulse_token: Optional[str] = Header(default=None),
) -> str:
    if not ADMIN_TOKEN and not VIEWER_TOKEN:
        return "admin"
    provided = x_pulse_token or request.cookies.get("pulse_token")
    if not provided and ALLOW_QUERY_TOKEN:
        provided = request.query_params.get("token")
        if provided:
            logger.warning("Access token was provided through URL query; prefer X-Pulse-Token header.")
    if token_matches(provided, ADMIN_TOKEN):
        return "admin"
    if token_matches(provided, WEB_AUTH_TOKEN):
        return "admin"
    if token_matches(provided, VIEWER_TOKEN):
        return "viewer"
    raise HTTPException(status_code=401, detail="Invalid or missing access token")


def require_admin(role: str = Depends(get_current_role)) -> str:
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin token required")
    return role


def require_viewer(role: str = Depends(get_current_role)) -> str:
    return role  # any authenticated role is fine


def app_base_url() -> str:
    host = settings.host
    port = settings.port
    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{shown_host}:{port}"
```

- [ ] **Step 2: Verify `app_ctx.py` imports cleanly**

```bash
python -c "from src.pulse_desk.app_ctx import state, settings, require_admin; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/pulse_desk/app_ctx.py
git commit -m "refactor: create app_ctx.py shared context module for router refactoring"
```

---

### Task 2: Update `main.py` to import from `app_ctx.py`

Before creating any routers, make `main.py` itself use `app_ctx.py` so the two stay in sync.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Replace module-level init block in `main.py`**

Delete lines 145–193 in `main.py` (the block from `settings = get_settings()` through `scan_cancel_event = state.scan_cancel_event`). Replace with:

```python
from pulse_desk.app_ctx import (
    ADMIN_ID,
    ADMIN_TOKEN,
    ALLOW_QUERY_TOKEN,
    AUTO_JOIN_GIVEAWAYS,
    DRY_RUN_GIVEAWAYS,
    EDIT_SCAN_RECENT_MESSAGES,
    GIVEAWAY_ACTION_ACCOUNT,
    GIVEAWAY_ANALYZE_RECENT_MESSAGES,
    GIVEAWAY_INACTIVE_CHANNEL_DAYS,
    GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
    GIVEAWAY_REVIEW_MODE,
    MARKET_ALERT_CHANGE_PCT,
    MARKET_POLL_SECONDS,
    MARKET_RETENTION_DAYS,
    PENDING_AUTH_TTL_SECONDS,
    PUBLIC_SHARE_MODE,
    SCAN_ACCOUNT_CONCURRENCY,
    SCAN_HISTORY_LIMIT,
    SCAN_INTERVAL_SECONDS,
    STARTUP_SCAN_DELAY_SECONDS,
    STARTUP_SCAN_WAIT_SECONDS,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_RECONNECT_BASE_SECONDS,
    TELEGRAM_RECONNECT_JITTER_SECONDS,
    TELEGRAM_RECONNECT_MAX_SECONDS,
    TELEGRAM_RETRY_DELAY_SECONDS,
    VIEWER_TOKEN,
    WEB_AUTH_TOKEN,
    app_base_url,
    get_current_role,
    logger,
    require_admin,
    require_viewer,
    settings,
    state,
)

BASE_DIR = settings.base_dir
STATIC_DIR = settings.static_dir
LOG_FILE = settings.log_file

clients = state.clients
connected_user_ids = state.connected_user_ids
pending_auths = state.pending_auths
accounts_state = state.accounts_state
scan_lock = state.scan_lock
scan_status = state.scan_status
scan_cancel_event = state.scan_cancel_event
```

Also remove the duplicate `settings = get_settings()`, `logger = configure_logging(...)` lines since they're now in `app_ctx.py`.

- [ ] **Step 2: Run all tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 3: Start app and check health**

```bash
python main.py &
sleep 3
curl http://127.0.0.1:8000/api/health
kill %1
```

Expected: `{"status": "ok", ...}` or similar JSON

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "refactor: main.py imports constants from app_ctx.py (no behaviour change)"
```

---

### Task 3: Create `routers/` package and extract one router (pings)

Do one router first to validate the pattern before doing all 13.

**Files:**
- Create: `routers/__init__.py`
- Create: `routers/pings.py`
- Modify: `main.py`

- [ ] **Step 1: Create `routers/__init__.py`**

```python
# empty
```

- [ ] **Step 2: Extract pings routes into `routers/pings.py`**

Move all ping-related route handlers from `main.py` into `routers/pings.py`:

```python
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import (
    get_ping_by_id,
    get_pings,
    get_pings_grouped,
    mark_ping_read as mark_ping_read_db,
    mark_pings_read as mark_pings_read_db,
    rebuild_search_indexes,
    toggle_favorite,
    update_ping_deadline,
    update_ping_meta,
)
from pulse_desk.api_models import PingMetaRequest
from pulse_desk.app_ctx import require_admin, require_viewer, state
from pulse_desk.deadlines import iso_or_none, parse_claim_deadline, parse_deadline, parse_participation_deadline

router = APIRouter()

# Paste the exact handler functions from main.py here, replacing @app.get with @router.get
# Example:
# @router.get("/api/pings")
# async def list_pings(...):
#     ...
```

- [ ] **Step 3: Register router in `main.py`**

In `main.py`, after `app = FastAPI(...)`:

```python
from routers import pings as pings_router
app.include_router(pings_router.router)
```

Remove the original ping route handlers from `main.py` (they are now in `routers/pings.py`).

- [ ] **Step 4: Run tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 5: Verify app starts**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add routers/ main.py
git commit -m "refactor: extract pings routes into routers/pings.py"
```

---

### Task 4–14: Extract remaining routers

Repeat Task 3's pattern for each domain. One commit per router. Order matters — extract simpler ones first:

- [ ] **Task 4:** `routers/core.py` — routes: `/`, `/favicon.ico`, `/api/health`, `/api/session`, `/api/status`, `/api/diagnostics`, `/api/share-guide`, `/api/setup-check`
- [ ] **Task 5:** `routers/market.py` — routes: `/api/market`, `/api/market-history-full`
- [ ] **Task 6:** `routers/export.py` — routes: `/api/export-csv`, `/api/export-json`
- [ ] **Task 7:** `routers/system.py` — routes: `/api/backups`, `/api/backups/create`, `/api/backups/{name}/download`, `/api/events`, `/api/logs`
- [ ] **Task 8:** `routers/analytics.py` — routes: `/api/analytics`, `/api/dashboard/summary`, `/api/analytics/detailed`, `/api/stats/detailed`, `/api/tasks`, `/api/debts`, `/api/report-html`
- [ ] **Task 9:** `routers/scan.py` — routes: `/api/scan-history`, `/api/scan-history/cancel`, `/api/scan-status`, `/api/scan-runs`
- [ ] **Task 10:** `routers/accounts.py` — routes: `/api/accounts`, `/api/accounts/*`
- [ ] **Task 11:** `routers/settings.py` — routes: `/api/settings/*`, `/api/saved-filters`
- [ ] **Task 12:** `routers/sources.py` — routes: `/api/sources/*`, `/api/channels/*`
- [ ] **Task 13:** `routers/giveaways.py` — routes: `/api/giveaways/*`
- [ ] **Task 14:** `routers/auth.py` — routes: `/api/auth/send-code`, `/api/auth/sign-in`
- [ ] **Task 15:** `routers/live.py` — route: `/api/live` (SSE)

For each task:
1. Create the router file with `router = APIRouter()`
2. Move exact handler code from `main.py`
3. Update imports (use `from pulse_desk.app_ctx import ...`, `from database import ...`)
4. Add `app.include_router(xxx.router)` in `main.py`
5. Delete moved handlers from `main.py`
6. Run `python -m unittest tests.test_core -v` — all pass
7. Run `python -c "import main; print('OK')"`
8. Commit: `git commit -m "refactor: extract <domain> routes into routers/<domain>.py"`

---

### Task 16: Final cleanup

- [ ] **Step 1: Verify `main.py` line count is below 300**

```bash
wc -l main.py
```

Expected: < 300 lines (just imports, constants, lifespan, app setup, helper functions used in lifespan)

- [ ] **Step 2: Run all tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: complete main.py router extraction — main.py is now thin orchestrator"
```
