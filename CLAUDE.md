# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the app:**
```powershell
# Windows (auto-setup venv, install deps, start)
.\run_local.ps1
.\run_local.ps1 -SkipInstall   # skip pip install

# With uv (faster, cross-platform)
uv sync
python main.py

# Manual
.\.venv\Scripts\python.exe main.py
```

**Install dev dependencies:**
```bash
uv sync --extra dev
```

**Run tests:**
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m unittest tests.test_core      # single file
```

**Install pytest (for API tests):**
```bash
pip install pytest==8.3.5 pytest-asyncio==0.24.0
```

**Syntax validation (no linter configured):**
```powershell
.\.venv\Scripts\python.exe -m py_compile main.py database.py telegram_ping_watcher.py src\pulse_desk\config.py
node --check static/app.js   # requires Node.js
```

**Docker:**
```powershell
docker compose up --build
```

## Architecture

**Pulse Desk** is a Telegram channel monitoring app. It watches configured channels for mentions of tracked usernames, detects giveaway opportunities, tracks tasks/debts and deadlines, and shows everything in a web dashboard with live updates.

### Layer breakdown

```
main.py (3000+ lines)
  FastAPI app — ~70 HTTP endpoints + WebSocket /api/live
  Role-based auth: ADMIN_TOKEN (full control) vs VIEWER_TOKEN (read-only)
  Startup lifespan hook initialises DB and launches background jobs

src/pulse_desk/
  config.py         — Pydantic BaseSettings loaded from .env
  runtime.py        — AppState singleton (in-memory job state, caches)
  jobs.py           — Background job management (scan, reminders, market-monitor, bot-service)
  scan.py           — Channel sweep logic using Telethon; writes pings/checkpoints to DB
  giveaways.py      — Giveaway detection and candidate scoring
  deadlines.py      — Natural-language date/time parsing for reminders
  dashboard.py      — Dashboard summary aggregation
  security.py       — HMAC constant-time token validation
  telegram_reconnect.py — Exponential backoff reconnect logic

database.py (2500+ lines)
  All SQLite access via aiosqlite. Schema version tracked (current: 8).
  Auto-migrates on startup. WAL mode + FK enabled + 5 s busy timeout.
  Main tables: pings, giveaway_candidates, tasks, debts, market_snapshots,
               channel_profiles, scan_runs, checkpoints, settings, reminders

telegram_ping_watcher.py   — Telethon client helpers and message parsing utilities
auth_accounts.py           — Console tool for Telegram account authentication

static/                    — Vanilla JS frontend (no build step). PWA with service worker.
sessions/                  — Telethon .session credential files (never commit these)
```

### Background jobs (always running)

| Job | Purpose | Key env var |
|-----|---------|-------------|
| `auto-scan` | Sweeps channels for new messages | `SCAN_INTERVAL_SECONDS` (default 900 s) |
| `reminders` | Fires deadline reminders | — |
| `source-scores` | Recalculates channel reliability scores | — |
| `market-monitor` | Fetches crypto prices, alerts on volatility | `MARKET_POLL_SECONDS` |
| `bot-service` | Telegram bot for admin notifications | optional |

### Data flow for a "ping"

1. `auto-scan` job calls `scan.py` → reads messages via Telethon
2. `telegram_ping_watcher.py` parses messages, matches tracked usernames + win/giveaway keywords
3. Matches written to `pings` table; checkpoints updated so next scan is incremental
4. WebSocket `/api/live` broadcasts events to connected clients in real time

## Configuration

Copy `.env.example` → `.env`. Required vars:

```env
TELEGRAM_API_ID=...        # from my.telegram.org
TELEGRAM_API_HASH=...
ADMIN_TOKEN=...            # must be ≥16 chars, not the placeholder
VIEWER_TOKEN=...
```

Session discovery: if `TELEGRAM_SESSIONS` is empty, all `*.session` files in `./sessions` are used automatically.

`src/pulse_desk/config.py` is the authoritative list of every supported env var with defaults.

## Key conventions

- **All DB access is async** via `aiosqlite`; never use synchronous sqlite3 in new code.
- **Schema changes** require bumping `SCHEMA_VERSION` in `database.py` and adding a migration branch in `run_migrations()`.
- **Giveaway actions default to dry-run** (`DRY_RUN_GIVEAWAYS=true`). Any code that joins or submits must check this flag.
- **Session files are secrets** — treat `.session` files like passwords; they are excluded from git via `.gitignore`.
- The frontend is plain ES modules in `/static` — no npm, no bundler, no TypeScript.
