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

**Run tests** (pytest is in the `dev` extra; `asyncio_mode = "auto"` is set in pyproject.toml):
```powershell
.\.venv\Scripts\python.exe -m pytest                        # all tests
.\.venv\Scripts\python.exe -m pytest tests\test_core.py     # single file
.\.venv\Scripts\python.exe -m pytest tests\test_core.py -k name_of_test

# unittest also works (tests are unittest-style)
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

**Syntax validation (no linter configured):**
```powershell
.\.venv\Scripts\python.exe -m py_compile main.py telegram_ping_watcher.py
.\.venv\Scripts\python.exe -c "import main"   # imports every module + registers all routers
node --check static/js/app-core.js   # requires Node.js; repeat per changed JS file
```

**Docker:**
```powershell
docker compose up --build
```

## Architecture

**Pulse Desk** is a Telegram channel monitoring app. It watches configured channels for mentions of tracked usernames, detects giveaway opportunities, tracks tasks/debts and deadlines, and shows everything in a web dashboard with live updates.

### Layer breakdown

```
main.py (~160 lines)
  Entry point only: builds the FastAPI app, registers routers, runs the
  startup/shutdown lifespan (DB init, settings load, bot + background jobs).
  No business logic and no endpoints live here anymore.

routers/                   — ALL HTTP endpoints. One module per area:
  access (scheduled access), analytics, auth, backups, boards, bot_access,
  export, giveaways, launcher, live (SSE), lookups, market, obsidian, pings,
  push, scan, settings, system.
  Registered in main.py via app.include_router(). They import singletons from
  src/pulse_desk/app_ctx.py — NEVER from main (avoids circular imports).
  New endpoint groups go here, not in main.py.

src/pulse_desk/
  app_ctx.py        — Shared context: settings/state/logger singletons,
                      derived constants, auth dependencies for routers
  config.py         — Pydantic BaseSettings loaded from .env
  runtime.py        — AppState dataclass (clients, bot, scan status, keywords,
                      tracked usernames — all mutable shared state lives here)
  common.py         — now_iso, record_app_event, flood_wait_seconds,
                      start_background_task / start_supervised wrappers
  watch_settings.py — Mutable watcher settings: keyword/tracking/notification/
                      digest settings + runtime tunables (SCAN_INTERVAL_SECONDS
                      etc.). Read tunables via the module: `ws.SCAN_HISTORY_LIMIT`
  telegram_accounts.py — Account lifecycle: start_client, reconnect/cooldown,
                      auth-session helpers, disconnect
  ping_pipeline.py  — process_ping_message: classify, score, deadlines,
                      persist, notify, web-push fanout
  scan_engine.py    — full_history_scan, scan_single_account, mention backfill
  giveaway_actions.py — Safe giveaway join: analysis, button detection, confirm
  bot_notify.py     — Outbound bot messages: admin notify + member broadcasts
  bot_service.py    — init_bot: inline menus, slash commands, access keys,
                      /access scheduled-access management
  bot/stickers.py   — Aperture sticker registry + best-effort sender (gated by
                      BOT_STICKERS_ENABLED). .webp set lives in
                      assets/bot/stickers/; fired on win/giveaway/check/scan/
                      ping/welcome. Never raises into a handler (photo fallback)
  access_control.py — Pure schedule resolution (Window/Decision, window_contains,
                      resolve_access, next_boundary). Zoneinfo/DST-aware, no I/O,
                      fully unit-tested. Source of truth for bot_role gating
  loops.py          — Background loops: market, reminders, digest, scores,
                      auto-scan, obsidian-sync, access-scheduler, startup maintenance
  obsidian_debts.py — Two-way sync of the Debts board with an Obsidian
                      `Долги.md` note: parse/normalise/reconcile (pure, unit
                      tested) + atomic write w/ dated backup. Note wins on
                      conflict; only the "done = claimed" bit is synced. Gated
                      by OBSIDIAN_SYNC_* env vars (see config.py)
  analytics.py      — build_analytics / channel_account_stats
  jobs.py           — Task supervision primitives (start_tracked/supervised_task)
  scan.py           — Scan limit normalisation + sweep-start helpers
  giveaways.py      — Giveaway detection and candidate scoring
  deadlines.py      — Natural-language date/time parsing for reminders
  dashboard.py      — Dashboard summary aggregation
  live.py / live_hub.py — SSE event publishing to connected clients
  push.py           — Web Push notifications (PWA)
  digest.py         — Periodic digest generation
  security.py       — HMAC constant-time token validation
  telegram_reconnect.py — Exponential backoff reconnect logic
  process_supervisor.py — Launcher: spawn/supervise EXTERNAL runtimes (Discord
                      bot) as child processes; stdlib-only (Popen + thread
                      reader + asyncio supervise loop), auto-restart w/ backoff.
                      Singleton via get_supervisor(). See docs/DASHBOARD.md
  service_registry.py — Loads managed-service manifest (config/services.json,
                      template config/services.example.json; git-ignored real file)

database/                  — SQLite layer (aiosqlite), split per area.
  __init__.py re-exports the full public API, so `import database` /
  `from database import save_ping` keep working. DB_PATH stays a mutable
  attribute on the package (tests monkeypatch it); submodules resolve it
  through _core.db_path().
  _core.py    — _connect(), shared helpers, SCHEMA_VERSION (current: 16)
  schema.py   — init_db + migrations   backups.py  — file backups
  pings.py    — ping CRUD/filters/FTS  checkpoints.py — scan checkpoints
  giveaways.py — candidates/actions/reconcile   boards.py — giveaway/debt boards
  reminders.py — deadline reminders    channels.py — profiles, source scores
  market.py   — market snapshots       scan_runs.py — scan-run bookkeeping
  events.py   — app event log          settings_kv.py — key-value settings
  outbox.py   — SSE outbox             push.py — push subscriptions
  bot_access.py — bot keys/members     stats.py / maintenance.py — stats, cleanup
  access_windows.py — scheduled-access windows (access_schedule) + audit
  WAL mode + FK enabled + 5 s busy timeout everywhere.

telegram_ping_watcher.py   — Telethon client helpers and message parsing utilities
auth_accounts.py           — Console tool for Telegram account authentication

static/                    — Vanilla JS frontend (no build step). PWA with service worker.
  index.html loads classic scripts in a fixed order; the former app.js is split
  into static/js/app-{core,pings,dashboard,settings,main}.js which share one
  global scope — keep the load order from index.html when adding files, and
  bump CACHE_NAME in static/sw.js when shell assets change.
sessions/                  — Telethon .session credential files (never commit these)
scripts/                   — One-off tools: generate_bot_assets.py (bot branding
                             PNGs, needs Pillow), generate_bot_stickers.py
                             (Aperture .webp sticker set, needs Pillow + Windows
                             colour-emoji font), set_bot_profile.py (upload avatar)
```

### Background jobs (always running)

| Job | Purpose | Key env var |
|-----|---------|-------------|
| `auto-scan` | Sweeps channels for new messages; also runs retention each sweep: age cleanup, unbounded-table trim (`scan_runs`/`settings_history`/`access_audit`/`giveaway_actions`), a size cap that evicts oldest non-favorite/non-win pings (archived to `pulse_desk_archive.db` first) + VACUUM, and archive-record pruning (the archive *file* is kept, but its rows older than `ARCHIVE_RETENTION_DAYS` are aged out) | `SCAN_INTERVAL_SECONDS` (default 900 s), `DB_MAX_SIZE_MB`, `DB_ARCHIVE_ENABLED`, `ARCHIVE_RETENTION_DAYS`, `SCAN_RUNS_RETENTION`, `AUDIT_RETENTION_DAYS` |
| `reminders` | Fires deadline reminders (admin + opted-in bot members) | — |
| `daily-digest` | Sends daily ping digest to admin + opted-in bot members at a configurable time (settings key `digest`, default 09:00) | — |
| `source-scores` | Recalculates channel reliability scores | — |
| `access-scheduler` | Warms the scheduled-access cache and notifies members when their access window opens/closes (not the source of truth — `bot_role` recomputes on demand) | — |
| `obsidian-sync` | Reconciles the Debts board with the Obsidian `Долги.md` note (note wins; syncs the claimed/done bit, appends newly detected wins) | `OBSIDIAN_DEBTS_PATH`, `OBSIDIAN_SYNC_ENABLED`, `OBSIDIAN_SYNC_WRITE`, `OBSIDIAN_SYNC_POLL_SECONDS` |
| `market-monitor` | Fetches crypto prices, alerts on volatility | `MARKET_POLL_SECONDS` |
| `bot-service` | Telegram bot for notifications + inline menus | `TELEGRAM_BOT_TOKEN` (optional) |

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
- **Routers import from `app_ctx`, never from `main`** — `src/pulse_desk/app_ctx.py` holds the settings/state/logger singletons and auth dependencies precisely so router modules avoid circular imports with `main.py`.
- **Schema changes** require bumping `SCHEMA_VERSION` in `database/_core.py` and adding a migration branch in `database/schema.py` (`init_db`).
- **Session files are secrets** — treat `.session` files like passwords; they are excluded from git via `.gitignore`.
- The frontend is plain classic scripts in `/static` sharing one global scope — no npm, no bundler, no TypeScript. The `app-*.js` load order in index.html matters.
