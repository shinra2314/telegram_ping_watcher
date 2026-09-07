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

**Pulse Desk** is a Telegram channel monitoring app. It watches configured channels for mentions of tracked usernames, detects giveaway opportunities, tracks tasks/debts, and shows everything in a web dashboard with live updates.

Deadline tracking and redeemable-check (чеки) detection were removed in 2026-07 — the
owner works from the bot only, and both features cost a Telegram `GetFullChannel`
per giveaway plus a polling loop. Nothing parses or writes `deadline_*`/`is_check`
any more; the columns survive in the schema (no migration) but stay NULL/0.

Giveaway auto-join was removed the same way (2026-07): no `AUTO_JOIN_GIVEAWAYS`
setting, no `auto_joined` writes, no badge/filter in the UI. The `auto_joined`
column stays in the schema, always 0. Joining a giveaway is a manual act.

The detailed analytics report lives **in the bot only** (📈 Аналитика section,
`/analytics`, callbacks `an:<tab>`) — the web app has no analytics tab, and
`/api/analytics/detailed` + `/api/stats/detailed` are gone. `/api/analytics`
survives because the dashboard tiles read it.

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
  ping_pipeline.py  — process_ping_message: classify, score,
                      persist, notify, web-push fanout
  scan_engine.py    — full_history_scan, scan_single_account, mention backfill.
                      A sweep spends no request on a channel whose newest
                      message id (free from the dialog list) is not past its
                      checkpoint; idle channels only get the recent-window
                      (edit) pass once an hour, active ones every sweep.
                      Ends each account with the global-search pass below
  global_search.py  — Mentions reading messages cannot see. A mini-app result
                      card (@Random winner table) arrives as
                      `messageMediaUnsupported`: empty text, no entities, so
                      the mention parser has nothing to match. Telegram's
                      *global* search (`messages.searchGlobal`, i.e.
                      `iter_messages(None, search=...)`) does index it —
                      per-peer `messages.search` does not, which is why this
                      pass is global. One query per tracked username per
                      account per sweep (`GLOBAL_SEARCH_LIMIT`, 0 disables).
                      A hit with readable text goes through the normal parser
                      (search is prefix-matching, the parser is stricter); a
                      textless hit is stored on the search's word alone, with
                      a placeholder body, and its win flag comes from
                      intersecting the hit with a second round of global
                      searches over `win_keywords`
  giveaway_actions.py — Safe giveaway join: analysis, button detection, confirm
  bot_notify.py     — Outbound bot messages: admin notify + member broadcasts
  bot_connection.py — Keeps the bot client connected (`bot-connection` job).
                      Telethon stops auto-reconnecting after
                      `connection_retries` failures and leaves the client dead;
                      outgoing sends recover lazily (`ensure_bot_connected`) but
                      incoming updates do not, so a network flap used to freeze
                      every command/button until the next notification happened
                      to reconnect — the "all bot messages are delayed on random
                      days" symptom. The supervisor mirrors
                      `telegram_accounts.monitor_client_disconnect` for the bot:
                      awaits `client.disconnected` (bounded poll, so a wedged
                      client is still caught), reconnects with capped backoff,
                      and records the outage in `state.bot_offline_since` →
                      `/api/health` (`bot_connected`, `bot_offline_seconds`;
                      degraded past 10 min offline)
  bot_permissions.py — Per-key grants (pure, unit tested): which bot sections a
                      guest may open, which notification types reach them, an
                      optional whitelist of tracked accounts, and `delay_minutes`
                      — how long their copies are held back (owner is never
                      delayed). Empty `permissions` column = full viewer access,
                      no delay (legacy keys). Edited from the bot's key panel
                      (`key:*` callbacks in bot/service.py). That panel also owns
                      the key's whole life after creation: label (`key:name`),
                      viewer/premium (`key:role`), expiry (`key:e`, presets +
                      typed days; stored in `expires_at`), holders (`key:m`),
                      link (`key:link`), revoke/restore (`key:rm` / `key:on`) and
                      a confirmed delete (`key:del` → `key:delgo`). The keys list
                      shows revoked keys too — the panel is where they come back
  bot_service.py    — init_bot: inline menus, slash commands, access keys,
                      /access scheduled-access management
  bot/views.py      — `GiveawayFilter` carries the whole giveaways-feed state in
                      one callback (`gw:f:<sort>:<wins>:<account>:<page>`, ≤64 B):
                      sort by detection or by the message's own date, wins-only,
                      and one tracked account addressed **by index** into the
                      list `giveaway_accounts(perms)` rebuilds identically on
                      render and on click. The account picker (`gw:a:…`, backed
                      by `database.giveaway_account_counts`) shows open wins and
                      giveaways per account; `gw:open:<id>:<state>` carries the
                      list state so ⬅️ from a card lands back on the same filter.
                      An unfiltered page 1 still encodes as plain `menu_giveaways`
  bot/stickers.py   — Aperture sticker registry + best-effort sender (gated by
                      BOT_STICKERS_ENABLED). .webp set lives in
                      assets/bot/stickers/. NOT auto-fired anywhere (sticker
                      spam removed 2026-07) — module kept for manual/opt-in use.
                      Never raises into a handler (photo fallback)
  bot/emoji.py      — Custom-emoji rendering: resolve a @Stickers pack
                      (BOT_CUSTOM_EMOJI_SET) to {emoji: document_id}, inject
                      MessageEntityCustomEmoji into card text (UTF-16 offsets).
                      No pack → no-op, plain Markdown. Premium-only render;
                      non-Premium see the same standard emoji as fallback.
                      `with_vs16_variants` registers every emoticon both with
                      and without U+FE0F — Telegram may hand back a pack key as
                      bare `⚠` while the cards print `⚠️`, and matching only the
                      bare codepoint emits an entity one UTF-16 unit short.
                      Glyph set: assets/bot/emoji/ (85× 100px webp), covering
                      every emoji the bot prints **in message text**. Inline
                      keyboard labels cannot carry entities, so keyboards.py
                      always renders stock Telegram emoji — that is a Telegram
                      limit, not a gap in the pack
  access_control.py — Pure schedule resolution (Window/Decision, window_contains,
                      resolve_access, next_boundary). Zoneinfo/DST-aware, no I/O,
                      fully unit-tested. Source of truth for bot_role gating
  loops.py          — Background loops: market, digest, scores,
                      auto-scan, obsidian-sync, access-scheduler, startup maintenance
  obsidian_debts.py — Two-way sync of the Debts board with an Obsidian
                      `Долги.md` note: parse/normalise/reconcile (pure, unit
                      tested) + atomic write w/ dated backup. Note wins on
                      conflict; only the "done = claimed" bit is synced. Gated
                      by OBSIDIAN_SYNC_* env vars (see config.py)
  analytics.py      — build_analytics (dashboard tiles + bot home/summary) /
                      build_detailed_analytics (bot 📈 Аналитика) / channel_account_stats
  jobs.py           — Task supervision primitives (start_tracked/supervised_task)
  scan.py           — Scan limit normalisation + sweep-start helpers + sweep
                      pacing (`channel_has_new_messages`, `edit_sweep_due`,
                      `next_scan_delay` — pure, unit tested)
  giveaways.py      — Giveaway detection and candidate scoring
  dashboard.py      — Dashboard summary aggregation
  live.py / live_hub.py — SSE event publishing to connected clients
  push.py           — Web Push notifications (PWA)
  digest.py         — Digest text (pure): ping roundup + day-over-day
                      counters + crypto block. Day deltas come from our own
                      `market_history` snapshots (CoinGecko's `*_24h_change`
                      is only the fallback when history is too short); the
                      data is collected by `loops.collect_digest_market`.
                      `format_digest` is now the fallback form — the digest
                      normally goes out as two images with the short
                      `format_digest_caption` (win links only) attached
  digest_cards.py   — The digest as two Aperture treemap cards (Pillow):
                      card 1 groups the day's pings into wins / giveaways /
                      mentions with one tile per chat sized by count, card 2
                      is a market heatmap — tile area from `usd_market_cap`
                      (square-rooted, floored at max/15, or a static weight
                      table for snapshots predating the cap field), colour
                      from the day move. `squarify` (the treemap layout) and
                      `group_pings` are pure and unit tested; the drawing is
                      not. Type is Bahnschrift (variable DIN — condensed, set
                      by axes) for names/tickers and Consolas for every
                      figure: tabular digits are what make a column of
                      readouts line up like an instrument panel. Both degrade
                      through a candidate list (Franklin Gothic → DejaVu →
                      Pillow's default). `build_digest_cards` never raises — no Pillow, no
                      fonts or an unwritable dir simply falls the digest back
                      to `format_digest`. Cards land in `data/digest/`
                      (git-ignored) and are pruned after 3 days, because a
                      member's delayed copy still reads the file hours later
  roulette.py       — Daily yobo-roulette reminder (pure): config normalisation,
                      due/next-fire arithmetic, and resolving a reported `HH:MM`
                      to a real moment (a time still ahead of now means yesterday).
                      All state is one `roulette` settings key; `last_cycle` — the
                      date of the last closed day, set both when the nudge goes out
                      and when the owner checks in — is what keeps the loop
                      idempotent and lets a slot missed overnight fire once on the
                      next tick (unlike the digest, which sleeps through a missed
                      slot). Bot surface: `/roulette [HH:MM]`, `rl:*` callbacks, the
                      🎰 button on the management grid; owner-only, no guest grant
  converter.py      — Bot currency/crypto converter (pure): free-text query
                      parsing (`100 usd в грн`, `1 btc uah`, RU stems and
                      symbols), cross rates and card rendering. Reads only the
                      newest `market_history` snapshot, so a conversion costs
                      no network call. Fiat rates live on that snapshot under
                      `_fiat` (units per USD), written by `loops.fetch_fiat_rates`
                      from a second small CoinGecko request over two bridge
                      coins — quoting all eight coins in a dozen currencies
                      would bloat every stored row. A pre-`_fiat` snapshot still
                      converts USD/UAH from its own coin quotes (no migration).
                      Bot surface: `/convert`, `cv:*` callbacks, 💱 button on
                      the Курсы view; gated by the existing `market` grant
  bot_membership.py — Telegram user -> (role, grants). Lifted out of
                      bot/service.py, where it was a closure inside init_bot and
                      so unreachable from a router, so the Mini App resolves
                      access through the same rules instead of a second copy.
                      Its collaborators (member lookup, schedule decision) are
                      arguments, so the decision logic unit-tests with no DB
  miniapp_auth.py   — Telegram Mini App initData validation (pure): drop `hash`,
                      join the rest as sorted `k=v` lines, HMAC-SHA256 under
                      HMAC(b"WebAppData", bot_token), reject anything older than
                      24 h. Verified on every request — there is no session
  miniapp_server.py — The Mini App's own ASGI app + uvicorn task, on
                      MINIAPP_PORT. Separate from the dashboard because a quick
                      tunnel forwards a whole origin: whatever shares that port
                      is on the internet. OpenAPI/docs are off there
  tunnel.py         — Runs a tunnel agent as a child process and owns
                      `state.public_url` (`tunnel` job). Provider is
                      `TUNNEL_PROVIDER`: `ngrok` (default — a free account
                      carries one reserved domain, so `NGROK_DOMAIN` gives a
                      *stable* address that survives restarts and can be pinned
                      in BotFather) or `cloudflared` (quick tunnel, no account,
                      new hostname per start). Each provider is an argv builder
                      plus a pure line-extractor, both unit tested; the
                      supervisor around them is shared. A missing binary or a
                      dead tunnel just empties `public_url`, the buttons vanish
                      and the bot falls back to its inline keyboards.
                      NOTE: on this machine (verified 2026-09-07) only
                      `api.trycloudflare.com:443` is blocked — `api.cloudflare.com`,
                      `region1/2.v2.argotunnel.com:7844`, the ngrok agent host
                      and the Tailscale control plane all connect. So quick
                      tunnels cannot work here, but ngrok and a *named*
                      Cloudflare tunnel can, with no VPN. When an agent dies
                      without printing an address its last output lines go into
                      app.log, because that is what the cause looks like
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
  _core.py    — _connect(), shared helpers, SCHEMA_VERSION (current: 21)
  schema.py   — init_db + migrations   backups.py  — file backups
  pings.py    — ping CRUD/filters/FTS  checkpoints.py — scan checkpoints
  giveaways.py — candidates/actions/reconcile   boards.py — giveaway/debt boards
  channels.py — channel profiles, source scores
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
  static/app/ is the Telegram Mini App — its own shell, CSS, inline-SVG icon set
  and script, served by miniapp_server.py on a different port. It shares nothing
  with the dashboard's app-*.js global scope and has no service worker.
sessions/                  — Telethon .session credential files (never commit these)
scripts/                   — One-off tools: generate_bot_assets.py (bot branding
                             PNGs, needs Pillow), generate_bot_emoji.py (the 85
                             Aperture custom-emoji tiles, 100px webp; glyphs are
                             Pillow geometry — no emoji font — so `--sheet`
                             renders a review contact sheet to
                             assets/bot/emoji-preview.png), generate_bot_stickers.py
                             (same silhouettes at 512px inside the reticle;
                             imports the painters from generate_bot_emoji, needs
                             Consolas only for the unit code),
                             upload_emoji_pack.py (drives @Stickers from a
                             Premium session — stop the app first, ~10 min for
                             the full set), set_bot_profile.py (upload avatar)
```

### Background jobs (always running)

| Job | Purpose | Key env var |
|-----|---------|-------------|
| `auto-scan` | Sweeps channels for new messages every `SCAN_INTERVAL_SECONDS` **measured from the start of the cycle** (the sleep is the remainder of the interval, so sweep time no longer stacks on top of it; minimum 30 s gap); also runs retention each sweep: age cleanup (`PINGS_RETENTION_DAYS`; wins, giveaways and favourites are never aged out), unbounded-table trim (`scan_runs`/`settings_history`/`access_audit`/`giveaway_actions`), a size cap that evicts oldest non-favorite/non-win pings (archived to `pulse_desk_archive.db` first) + VACUUM, and archive-record pruning (the archive *file* is kept, but its rows older than `ARCHIVE_RETENTION_DAYS` are aged out) | `SCAN_INTERVAL_SECONDS` (default 900 s), `DB_MAX_SIZE_MB`, `DB_ARCHIVE_ENABLED`, `ARCHIVE_RETENTION_DAYS`, `SCAN_RUNS_RETENTION`, `AUDIT_RETENTION_DAYS` |
| `daily-digest` | Sends the daily digest to admin + opted-in bot members at a configurable time (settings key `digest`, default 10:00) as a two-image album: a ping treemap (24 h, wins/giveaways/mentions per chat, today-vs-yesterday counter) and a crypto heatmap (all 8 tracked coins, area by market cap, day delta from our own `market_history` snapshots, leader/laggard, UAH line). The caption carries only the win links; if Pillow can't render, the old text digest goes out instead | — |
| `roulette-reminder` | Daily nudge to spin the yobo-bot roulette from every account. The alarm time *is* the previous day's last-click time, reported back by the owner (button, `/roulette 21:47`, or a bare `21:47` in the bot chat). Ticks every 30 s instead of sleeping to the target, so a time reported mid-day applies at once and a slot missed while the PC was off still fires once on the next tick | settings key `roulette` |
| `bot-connection` | Reconnects the bot client after Telethon gives up, so incoming updates never stall (started only when the bot is configured) | — |
| `pending-sends` | Drains `bot_pending_sends`: delivers member copies whose per-key `delay_minutes` elapsed (20 s poll, drops rows older than 24 h) | — |
| `source-scores` | Recalculates channel reliability scores | — |
| `access-scheduler` | Warms the scheduled-access cache and notifies members when their access window opens/closes (not the source of truth — `bot_role` recomputes on demand) | — |
| `obsidian-sync` | Reconciles the Debts board with the Obsidian `Долги.md` note (note wins; syncs the claimed/done bit, appends newly detected wins) | `OBSIDIAN_DEBTS_PATH`, `OBSIDIAN_SYNC_ENABLED`, `OBSIDIAN_SYNC_WRITE`, `OBSIDIAN_SYNC_POLL_SECONDS` |
| `market-monitor` | Fetches crypto prices (+ fiat cross-rates for the bot converter), alerts on volatility | `MARKET_POLL_SECONDS` |
| `bot-service` | Telegram bot for notifications + inline menus | `TELEGRAM_BOT_TOKEN` (optional) |
| `miniapp-server` | Serves the Telegram Mini App on `MINIAPP_PORT`, alone on that port (started only when enabled) | `MINIAPP_ENABLED`, `MINIAPP_PORT` |
| `tunnel` | Keeps the tunnel agent up and `state.public_url` current, so WebApp buttons have an HTTPS origin | `MINIAPP_ENABLED`, `TUNNEL_PROVIDER`, `NGROK_DOMAIN`, `NGROK_BIN`, `CLOUDFLARED_BIN` |

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
- **Bot keyboards cannot carry custom emoji or custom shapes, ever.** Button text
  is a plain string with no `entities` in both MTProto and the Bot API, so the
  Aperture pack renders in card *text* only. Custom icons and custom button
  shapes live in the Mini App (`static/app/`), which is HTML. Do not try to
  solve this in `keyboards.py` again.
- **`cryptg` must stay installed.** Without it Telethon decrypts every MTProto packet with `pyaes`, in pure Python, on the event loop thread. With 8 accounts plus the bot that took ~50 % of the loop and starved everything else: bot callbacks expired (`QueryIdInvalidError`), awaited SQLite calls blew their 5 s busy timeout (`database is locked` storms), and the HTTP server went unreachable long enough for the watchdog to restart the app. If the bot ever "hangs" again, check `import cryptg` first.
- **Profiling the running app:** `py-spy record --pid <pid of :8000 listener> --duration 120 --format speedscope --threads`. `database/_core.py` also logs a stack for any DB connection held ≥ `PULSE_DB_TRACE_SECONDS` (default 2 s).
- The frontend is plain classic scripts in `/static` sharing one global scope — no npm, no bundler, no TypeScript. The `app-*.js` load order in index.html matters.
