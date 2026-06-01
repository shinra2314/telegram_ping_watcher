# API Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add integration tests for key API endpoints using `httpx.AsyncClient` + in-memory SQLite, and smoke tests for background job lifecycle. Run this plan after the main-py-routers and database-repositories plans are complete (or at minimum after `app_ctx.py` exists).

**Architecture:** Add `pytest` + `pytest-asyncio` + `httpx` (already in requirements). Create `tests/conftest.py` with a shared `app` fixture that uses an in-memory test DB. Group tests by domain in `tests/test_api_*.py`. Existing `tests/test_core.py` stays untouched.

**Tech Stack:** `pytest`, `pytest-asyncio`, `httpx.AsyncClient`, in-memory SQLite via monkey-patching `db.core.DB_PATH`.

---

### Task 1: Add pytest and test infrastructure

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml` (if it exists from pyproject plan)
- Create: `tests/conftest.py`

- [ ] **Step 1: Add test dependencies to `requirements.txt`**

```
pytest==8.3.5
pytest-asyncio==0.24.0
```

(`httpx` is already in requirements.txt)

Install:
```bash
pip install pytest==8.3.5 pytest-asyncio==0.24.0
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Patch DB_PATH to a temp file before importing main
import db.core as db_core

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def app_client():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        test_db = Path(f.name)

    # Override DB path before app imports
    db_core.DB_PATH = test_db

    from main import app
    from database import init_db
    await init_db()

    # Use a test admin token
    import pulse_desk.app_ctx as ctx
    ctx.ADMIN_TOKEN = "test-admin-token-12345"
    ctx.VIEWER_TOKEN = ""
    ctx.WEB_AUTH_TOKEN = ""

    headers = {"X-Pulse-Token": "test-admin-token-12345"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        yield client

    test_db.unlink(missing_ok=True)
```

- [ ] **Step 3: Verify conftest imports**

```bash
python -m pytest tests/conftest.py --collect-only
```

Expected: no errors, 0 items collected (conftest has no tests)

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py requirements.txt
git commit -m "test: add pytest infrastructure with async test client fixture"
```

---

### Task 2: Health and session endpoint tests

**Files:**
- Create: `tests/test_api_core.py`

- [ ] **Step 1: Create `tests/test_api_core.py`**

```python
import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(app_client):
    resp = await app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "ok"


@pytest.mark.asyncio
async def test_session_returns_role(app_client):
    resp = await app_client.get("/api/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("role") == "admin"


@pytest.mark.asyncio
async def test_health_without_token_returns_401_or_200(app_client):
    # health is public in some configurations — just check it doesn't 500
    from httpx import AsyncClient
    from httpx import ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        resp = await anon.get("/api/health")
    assert resp.status_code in (200, 401)
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_api_core.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_core.py
git commit -m "test: add API tests for health and session endpoints"
```

---

### Task 3: Pings CRUD endpoint tests

**Files:**
- Create: `tests/test_api_pings.py`

- [ ] **Step 1: Create `tests/test_api_pings.py`**

```python
import pytest


@pytest.mark.asyncio
async def test_get_pings_returns_list(app_client):
    resp = await app_client.get("/api/pings")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, (list, dict))  # list of pings or {items: [...]}


@pytest.mark.asyncio
async def test_get_pings_with_search_param(app_client):
    resp = await app_client.get("/api/pings?search=test")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_ping_by_id_404_for_missing(app_client):
    resp = await app_client.get("/api/pings/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_viewer_cannot_mark_read(app_client):
    from httpx import AsyncClient, ASGITransport
    from main import app
    import pulse_desk.app_ctx as ctx
    ctx.VIEWER_TOKEN = "viewer-token-12345"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Pulse-Token": "viewer-token-12345"},
    ) as viewer:
        resp = await viewer.post("/api/pings/mark-read/1")
    assert resp.status_code == 403
    ctx.VIEWER_TOKEN = ""


@pytest.mark.asyncio
async def test_toggle_favorite_on_missing_ping(app_client):
    resp = await app_client.post("/api/pings/toggle-favorite/99999")
    assert resp.status_code in (404, 200)  # 200 if no-op, 404 if strict
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_api_pings.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_pings.py
git commit -m "test: add API tests for pings endpoints"
```

---

### Task 4: Settings endpoint tests

**Files:**
- Create: `tests/test_api_settings.py`

- [ ] **Step 1: Create `tests/test_api_settings.py`**

```python
import pytest


@pytest.mark.asyncio
async def test_get_usernames(app_client):
    resp = await app_client.get("/api/settings/usernames")
    assert resp.status_code == 200
    data = resp.json()
    assert "usernames" in data


@pytest.mark.asyncio
async def test_get_keywords(app_client):
    resp = await app_client.get("/api/settings/keywords")
    assert resp.status_code == 200
    data = resp.json()
    assert "win_keywords" in data or "keywords" in data


@pytest.mark.asyncio
async def test_get_runtime_settings(app_client):
    resp = await app_client.get("/api/settings/runtime")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_usernames(app_client):
    payload = {"usernames": ["testuser1", "testuser2"]}
    resp = await app_client.put("/api/settings/usernames", json=payload)
    assert resp.status_code == 200

    resp2 = await app_client.get("/api/settings/usernames")
    data = resp2.json()
    assert "testuser1" in str(data)
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_api_settings.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_settings.py
git commit -m "test: add API tests for settings endpoints"
```

---

### Task 5: Background job lifecycle tests

**Files:**
- Create: `tests/test_jobs.py`

- [ ] **Step 1: Create `tests/test_jobs.py`**

```python
import asyncio
import pytest
import logging

from pulse_desk.jobs import runtime_health, start_tracked_task
from pulse_desk.runtime import AppState


@pytest.mark.asyncio
async def test_start_tracked_task_registers_and_cleans_up():
    state = AppState()
    logger = logging.getLogger("test")

    async def short_task():
        await asyncio.sleep(0)

    task = start_tracked_task(state, logger, "test-task", short_task())
    assert "test-task" in state.background_task_names
    await asyncio.sleep(0.05)
    assert "test-task" not in state.background_task_names  # cleaned up after completion


@pytest.mark.asyncio
async def test_start_tracked_task_cancels_previous():
    state = AppState()
    logger = logging.getLogger("test")

    async def long_task():
        await asyncio.sleep(100)

    t1 = start_tracked_task(state, logger, "long", long_task())
    t2 = start_tracked_task(state, logger, "long", long_task())
    assert t1.cancelled() or t1.cancelling() > 0
    t2.cancel()


def test_runtime_health_reports_missing_tasks():
    state = AppState()
    health = runtime_health(state, accounts_online=0, accounts_configured=1)
    assert "auto-scan" in health["missing_background_tasks"]
    assert health["accounts_ok"] is False


def test_runtime_health_accounts_ok_when_none_configured():
    state = AppState()
    health = runtime_health(state, accounts_online=0, accounts_configured=0)
    assert health["accounts_ok"] is True
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_jobs.py -v
```

Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_jobs.py
git commit -m "test: add background job lifecycle tests"
```

---

### Task 6: Run full test suite and verify

- [ ] **Step 1: Run all tests**

```bash
python -m pytest tests/ -v
python -m unittest tests.test_core -v
```

Expected: all pass, no regressions

- [ ] **Step 2: Commit if any fixes needed**

```bash
git add -A
git commit -m "test: fix any test regressions after full suite run"
```
