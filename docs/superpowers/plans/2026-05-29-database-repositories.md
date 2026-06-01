# database.py → Domain Repositories Refactoring Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 2500-line `database.py` into focused domain modules under `db/`. `database.py` becomes a thin re-export facade so all existing imports continue to work.

**Architecture:** Create `db/` package. Move DB functions into domain files. Keep `database.py` as `from db.xxx import *` facade — zero import changes in `main.py` or routers. Run this plan **after** the main-py-routers plan is complete.

**Tech Stack:** Python package re-exports, existing `aiosqlite`, existing schema/migration logic.

> **WARNING:** Do one domain at a time. Test after every task. Circular import risk is low since `db/` modules only import each other's utility functions, not `database.py`.

---

## File Map

| New file | Functions moved from `database.py` |
|----------|-------------------------------------|
| `db/__init__.py` | Re-exports everything |
| `db/core.py` | `_connect`, `_columns`, `_now_iso`, `_env_int`, `_json_loads`, `_parse_mentions`, `_search_tokens`, `_fts_query`, `init_db`, `get_schema_version`, `get_db_stats`, `get_detailed_stats`, `cleanup_old_data` |
| `db/pings.py` | `save_ping`, `get_pings`, `get_pings_grouped`, `get_ping_by_id`, `get_ping_by_message_ref`, `mark_ping_read`, `mark_pings_read`, `toggle_favorite`, `update_ping_meta`, `update_ping_deadline`, `delete_ping`, `delete_ping_by_message_id`, `_sync_ping_indexes`, `_build_pings_filters`, `_add_where`, `rebuild_search_indexes` |
| `db/giveaways.py` | `upsert_giveaway_candidate`, `get_giveaway_candidate`, `get_giveaway_candidates`, `update_giveaway_candidate_status`, `seed_giveaway_candidates_from_pings`, `record_giveaway_action`, `get_giveaway_actions`, `get_giveaway_board`, `reconcile_giveaway_flags`, `reconcile_giveaway_outcomes`, `reconcile_win_flags`, `_candidate_from_row`, `_giveaway_board_row`, `_giveaway_workflow_stage`, `_giveaway_sort_rank`, `_giveaway_workflow_hint`, `_matches_strict_giveaway_rule` |
| `db/checkpoints.py` | `get_checkpoint`, `get_checkpoints`, `get_latest_checkpoints`, `save_checkpoint`, `save_checkpoints` |
| `db/channels.py` | `upsert_channel_profile`, `get_channel_profile`, `update_channel_deadlines`, `get_source_scores`, `get_source_score`, `recalculate_source_scores` |
| `db/market.py` | `save_market_snapshot`, `get_market_history` |
| `db/reminders.py` | `replace_ping_reminders`, `get_due_reminders`, `mark_reminder_sent`, `backfill_deadlines_from_text`, `_parse_iso_datetime` |
| `db/analytics.py` | `get_task_overview`, `get_debt_board`, `get_report_data`, `get_account_ping_stats` |
| `db/events.py` | `record_event`, `get_events`, `get_recent_problem_events`, `enqueue_outbox_event`, `get_outbox_after`, `get_outbox_stats`, `cleanup_outbox` |
| `db/scan.py` | `start_scan_run`, `update_scan_run`, `get_latest_scan_run`, `get_scan_runs`, `interrupt_stale_scan_runs`, `get_scan_run_health` |
| `db/settings_db.py` | `get_setting`, `set_setting` (+ `get_settings_history` if settings-history plan is done) |
| `db/backups.py` | `backup_db_if_present`, `create_db_backup`, `list_db_backups`, `_effective_backup_dir` |

---

### Task 1: Create `db/` package skeleton with `core.py`

**Files:**
- Create: `db/__init__.py`
- Create: `db/core.py`

- [ ] **Step 1: Create `db/__init__.py`** (empty for now)

```python
```

- [ ] **Step 2: Create `db/core.py`**

Copy from `database.py`:
- All imports at the top (json, os, re, shutil, sys, aiosqlite, Path, datetime, etc.)
- `BASE_DIR`, `SRC_DIR`, sys.path manipulation
- The `try/except` block that imports from `pulse_desk.*`
- `SCHEMA_VERSION`
- Helper functions: `_now_iso`, `_env_int`, `_connect`, `_columns`, `_parse_mentions`, `_json_loads`, `_search_tokens`, `_fts_query`
- `init_db` function (the full CREATE TABLE + migration block)
- `get_schema_version`, `get_db_stats`, `get_detailed_stats`, `cleanup_old_data`

Leave `database.py` unchanged for now.

- [ ] **Step 3: Verify `db/core.py` imports cleanly**

```bash
python -c "from db.core import init_db, _connect; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add db/
git commit -m "refactor: create db/ package skeleton with core.py"
```

---

### Task 2: Move pings functions to `db/pings.py`

**Files:**
- Create: `db/pings.py`

- [ ] **Step 1: Create `db/pings.py`**

```python
from __future__ import annotations

import json
from typing import Any, Optional

import aiosqlite

from .core import _connect, _json_loads, _now_iso, _parse_mentions, _fts_query, _search_tokens
```

Then copy all ping-related functions from `database.py` (see file map above). Internal helpers like `_sync_ping_indexes`, `_build_pings_filters`, `_add_where` should be in this file since they are only used by ping functions.

- [ ] **Step 2: Verify**

```bash
python -c "from db.pings import save_ping, get_pings, toggle_favorite; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Update `db/__init__.py`**

```python
from db.core import init_db, get_schema_version, get_db_stats, get_detailed_stats, cleanup_old_data, backup_db_if_present
from db.pings import (
    save_ping, get_pings, get_pings_grouped, get_ping_by_id, get_ping_by_message_ref,
    mark_ping_read, mark_pings_read, toggle_favorite, update_ping_meta, update_ping_deadline,
    delete_ping, delete_ping_by_message_id, rebuild_search_indexes,
)
```

- [ ] **Step 4: Commit**

```bash
git add db/pings.py db/__init__.py
git commit -m "refactor: move pings DB functions to db/pings.py"
```

---

### Task 3–11: Move remaining domain functions

Repeat the pattern for each remaining domain file. One commit per domain:

- [ ] **Task 3:** Create `db/giveaways.py` — move all giveaway functions (see file map). Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _json_loads, _now_iso`
  - Commit: `"refactor: move giveaway DB functions to db/giveaways.py"`

- [ ] **Task 4:** Create `db/checkpoints.py` — move checkpoint functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _now_iso`
  - Commit: `"refactor: move checkpoint DB functions to db/checkpoints.py"`

- [ ] **Task 5:** Create `db/channels.py` — move channel/source functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _json_loads, _now_iso`
  - Commit: `"refactor: move channel/source DB functions to db/channels.py"`

- [ ] **Task 6:** Create `db/market.py` — move market functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _json_loads, _now_iso`
  - Commit: `"refactor: move market DB functions to db/market.py"`

- [ ] **Task 7:** Create `db/reminders.py` — move reminder/deadline functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _now_iso`
  - Commit: `"refactor: move reminder DB functions to db/reminders.py"`

- [ ] **Task 8:** Create `db/analytics.py` — move analytics/report functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _json_loads, _now_iso`
  - Commit: `"refactor: move analytics DB functions to db/analytics.py"`

- [ ] **Task 9:** Create `db/events.py` — move event log and outbox functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _json_loads, _now_iso`
  - Commit: `"refactor: move event/outbox DB functions to db/events.py"`

- [ ] **Task 10:** Create `db/scan.py` — move scan run functions. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _now_iso`
  - Commit: `"refactor: move scan run DB functions to db/scan.py"`

- [ ] **Task 11:** Create `db/settings_db.py` — move `get_setting`, `set_setting`. Update `db/__init__.py`.
  - Imports needed: `from .core import _connect, _now_iso`
  - Note: name is `settings_db.py` (not `settings.py`) to avoid shadowing Python's `settings` module name
  - Commit: `"refactor: move settings DB functions to db/settings_db.py"`

- [ ] **Task 12:** Create `db/backups.py` — move backup functions. Update `db/__init__.py`.
  - Imports needed: `from .core import DB_PATH, BACKUP_DIR, _env_int`
  - Move `DB_PATH`, `BACKUP_DIR` constants to `db/core.py` if not already there
  - Commit: `"refactor: move backup DB functions to db/backups.py"`

---

### Task 13: Convert `database.py` to a facade

**Files:**
- Modify: `database.py`

- [ ] **Step 1: Replace `database.py` content with re-exports**

```python
"""
Backward-compatible facade — re-exports all public symbols from the db/ package.
Import from this module as before; nothing in main.py or routers needs to change.
"""
from db import *  # noqa: F401, F403
from db.core import (
    DB_PATH, SCHEMA_VERSION, BACKUP_DIR,
    _connect, _columns, _now_iso,  # keep private helpers accessible for tests
)
```

- [ ] **Step 2: Run all tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 3: Verify app starts**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add database.py db/
git commit -m "refactor: database.py is now a thin facade over db/ package"
```

---

### Task 14: Verify and clean up

- [ ] **Step 1: Check `database.py` line count**

```bash
wc -l database.py
```

Expected: < 20 lines

- [ ] **Step 2: Check total db/ line counts**

```bash
wc -l db/*.py
```

No single file should exceed 400 lines.

- [ ] **Step 3: Run tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 4: Final commit**

```bash
git commit -m "refactor: database.py split complete — all functions in db/ domain modules"
```
