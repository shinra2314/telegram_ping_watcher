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
.\.venv\Scripts\python.exe -c "import main"   # imports every module
```

**Docker:**
```powershell
docker compose up --build
```

## Architecture

**Pulse Desk** is a Telegram channel monitoring app. It watches configured channels for
mentions of tracked usernames, detects giveaway opportunities, tracks debts, and shows
everything **in its Telegram bot**.

The web dashboard was removed on 2026-09-12 — every screen it had now lives in the bot
(feed with filters and search, ping cards with statuses/notes/tags, debts + the Obsidian
note, the giveaway board with analyse/leave, a drawn dashboard and market heatmap, all
settings, keys, people, accounts, services, backups, diagnostics). Gone with it:
`static/`, every router but `routers/health.py`, the SSE hub and outbox fan-out
(`live.py`, `live_hub.py`), web push (`push.py`, VAPID), `api_models.py` and the access
tokens (`ADMIN_TOKEN`, `VIEWER_TOKEN`, `WEB_AUTH_TOKEN`, `PUBLIC_SHARE_MODE`,
`ALLOW_QUERY_TOKEN`). The `push_subscriptions` and `outbox` tables stay in the schema,
unused, by the same convention that keeps `deadline_*`. Two things did not move and were
dropped on purpose: the printable HTML report (its job is done by CSV/JSON export and the
digest cards) and the "Друзьям" share guide (a friend now gets a bot access key, not a
token). **Do not reintroduce an HTTP UI.**

uvicorn is still the process host and serves exactly one endpoint, `/api/health`:
`scripts/pulse_watchdog.ps1` and `scripts/start_dashboard.ps1` poll it, so replacing the
entry point would mean rewriting both Task Scheduler jobs for nothing.

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
main.py (~134 lines)
  Entry point only: the startup/shutdown lifespan (DB init, settings load, bot +
  background jobs) and a FastAPI app that serves `/api/health`. No business logic.

routers/                   — what is left of HTTP: `health.py` and nothing else.
  A new screen belongs in `src/pulse_desk/bot/sections/`, never here.

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
                      degraded past 10 min offline). It is **supervised**, and
                      calls `on_alive` each pass — the loop never returns, so
                      that callback is the only thing that can tell the watchdog
                      the supervisor itself is alive. It was fire-and-forget and
                      absent from both monitoring lists, so if it died the bot
                      went deaf while `/api/health` still said ok
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
  bot_service.py    — back-compat shim re-exporting `bot/service.py:init_bot`
  bot/service.py    — init_bot only: start the client, build the callback router,
                      register sections, own the slash commands, the free-text
                      catch-all and the `/access` text CLI. It used to be 2822
                      lines with every screen as a closure inside one function
                      and a ~600-line if-chain for callbacks; a section could not
                      be tested, reused or moved. Screens now live in
                      `bot/sections/*`
  bot/router.py     — `CallbackRouter`: exact match → `:`-family → `_`-family,
                      with the `feature=`/`admin=` gate declared at registration
                      so the denial wording cannot drift between sections. A
                      button nothing claims still gets the "кнопка устарела"
                      answer, never a silent spinner. `Click` carries the press
                      (data, segments, role, grants)
  bot/sections/     — one module per area (feed, giveaways, home, analytics,
                      market, converter, salary, keys, members, settings, prefs,
                      scan, system, broadcast, roulette, legacy). Each exposes
                      `register(router)` plus its own renderers, so the slash
                      command and the button can never render different screens.
                      `legacy.py` answers the underscore callbacks still sitting
                      on old messages in the chat (`fav_`, `read_`, `gconfirm_`)
  bot/pending.py    — free-text capture (Telethon has no ConversationHandler):
                      one armed entry per sender in `state.bot_pending_inputs`,
                      300 s TTL, swept on every new prompt. A section declares
                      its input kind *with its consumer* (`register_prompt`), so
                      adding "type a value here" is a local change; `admin=True`
                      is enforced in `consume`
  bot/persist.py    — every settings write: save → apply to the live
                      `watch_settings` → audit → publish. A value saved but not
                      applied leaves the running scanner on the old rules
  bot/reply.py      — `safe_edit` / `respond_rich` / `tell` + the Telethon error
                      shims. Split out so sections can answer without importing
                      service.py back
  bot/media.py      — `show_screen`: Telegram cannot edit a text message into a
                      media one (or back), so a screen that changes kind is
                      deleted and re-sent; same kind is a normal edit. Also the
                      card cache — a Pillow render costs hundreds of ms and the
                      callback query expires in ~15 s, so the same screen is
                      drawn once (`render_cached`, always in `asyncio.to_thread`)
  bot/render/canvas.py — the Aperture drawing vocabulary (grid canvas, header,
                      stat row, treemap tile, fonts, save, prune), lifted out of
                      `digest_cards` so every rendered screen looks identical.
                      Pillow is imported inside the functions: without it the
                      module still imports and the caller falls back to text
  bot/settings_schema.py — editable settings as data (key, label, kind, bounds,
                      unit, presets). Menus, validation and error wording are
                      generated from it, so a new tunable is one table row
                      instead of a hand-written branch that drifts
  bot/views.py      — `FeedFilter` carries the whole mentions-feed state in one
                      callback (`mon:f:<type>:<status>:<fav>:<sort>:<order>:<q>:<page>`,
                      19 B): type, status, favourites, sort and direction. The
                      search *text* does not fit 64 B, so the button carries only
                      a flag and the string lives in `state.bot_feed_queries`
                      (1 h TTL) — after a restart the feed honestly shows no
                      search instead of someone else's. `GiveawayFilter` carries
                      the giveaways-feed state in one callback
                      (`gw:f:<sort>:<wins>:<account>:<page>`, ≤64 B):
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
  health_report.py  — system status / diagnostics / setup checks as data. These
                      lived inside routers/system.py, so they existed only while
                      the web did, and the bot counted its own slightly different
                      numbers. Now both surfaces read the same facts
  dashboard.py      — `collect_dashboard` (the five queries + the pure assembly)
                      and `build_dashboard_summary`. The bot's 🛰 Пульт and the
                      web's /api/dashboard/summary call the same collector; the
                      router only adds its 5 s cache on top
  bot/render/screens.py — the drawn screens: the dashboard card (counters, daily
                      sparkline, scan progress, the "what to deal with" list) and
                      the market heatmap. The heatmap is the digest's own card —
                      one implementation, because two copies had already drifted
                      (one read `snapshots[0]` as "a day ago" when the query
                      returns newest first, and printed every rise as a fall)
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
  salary.py         — The salary workbook (`SALARY_XLSX_PATH`), read-only and
                      dependency-free: a `.xlsx` is a zip of XML, so `zipfile` +
                      `ElementTree` parse it and openpyxl stays out of the
                      requirements. Reads the *cached* `<v>` of every formula —
                      Excel stores its last computed value, so the bot never
                      evaluates a formula. Sheets are found **by name**
                      (`sheet_targets` walks workbook.xml → rels), never by
                      `sheetN.xml`, because that numbering is creation order.
                      Dates are 1900-system serials (`serial_to_date`). Everything
                      is pure except `read_book`; the parsed `SalaryBook` snapshot
                      lives on `state.salary_book`.
                      Who sees what is decided by the **key label**, not by a
                      grant code: a label matching an account on the «Настройки»
                      sheet (case- and space-insensitive) opens the section for
                      that one account, and a key without a matching label has no
                      button, no `/salary`, no callback — an unknown grant code
                      would have meant "allow" for every legacy key instead.
                      `collect_issues` warns the owner about money the workbook
                      itself drops (a row with no account, an unknown reward type,
                      a type the monthly formulas forgot to sum)
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
                      so unreachable from a router. Kept as a module so the
                      rules live in one place instead of a closure.
                      Its collaborators (member lookup, schedule decision) are
                      arguments, so the decision logic unit-tests with no DB
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
  _core.py    — _connect(), shared helpers, SCHEMA_VERSION (current: 22)
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
                             the full set), set_bot_profile.py (upload avatar),
                             fix_salary_yobo_formulas.py (adds the missing
                             «йобо» term to the salary workbook's monthly
                             formulas — the reward type existed in Настройки but
                             no sheet summed it, so such wins reached nobody's
                             salary; makes a dated backup, needs the book closed,
                             `--dry-run` reports what it would change)
```

### Background jobs (always running)

| Job | Purpose | Key env var |
|-----|---------|-------------|
| `auto-scan` | Sweeps channels for new messages every `SCAN_INTERVAL_SECONDS` **measured from the start of the cycle** (the sleep is the remainder of the interval, so sweep time no longer stacks on top of it; minimum 30 s gap); also runs retention each sweep: age cleanup (`PINGS_RETENTION_DAYS`; wins, giveaways and favourites are never aged out), unbounded-table trim (`scan_runs`/`settings_history`/`access_audit`/`giveaway_actions`/`scan_checkpoints`; `settings_history` is capped **per key** as well as by age, and `scan_checkpoints` drops channels unseen for 90 days), a size cap that evicts oldest non-favorite/non-win pings (archived to `pulse_desk_archive.db` first) + VACUUM, and archive-record pruning (the archive *file* is kept, but its rows older than `ARCHIVE_RETENTION_DAYS` are aged out) | `SCAN_INTERVAL_SECONDS` (default 900 s), `DB_MAX_SIZE_MB`, `DB_ARCHIVE_ENABLED`, `ARCHIVE_RETENTION_DAYS`, `SCAN_RUNS_RETENTION`, `AUDIT_RETENTION_DAYS` |
| `daily-digest` | Sends the daily digest to admin + opted-in bot members at a configurable time (settings key `digest`, default 10:00) as a two-image album: a ping treemap (24 h, wins/giveaways/mentions per chat, today-vs-yesterday counter) and a crypto heatmap (all 8 tracked coins, area by market cap, day delta from our own `market_history` snapshots, leader/laggard, UAH line). The caption carries only the win links; if Pillow can't render, the old text digest goes out instead | — |
| `roulette-reminder` | Daily nudge to spin the yobo-bot roulette from every account. The alarm time *is* the previous day's last-click time, reported back by the owner (button, `/roulette 21:47`, or a bare `21:47` in the bot chat). Ticks every 30 s instead of sleeping to the target, so a time reported mid-day applies at once and a slot missed while the PC was off still fires once on the next tick | settings key `roulette` |
| `bot-connection` | Reconnects the bot client after Telethon gives up, so incoming updates never stall (started only when the bot is configured) | — |
| `pending-sends` | Drains `bot_pending_sends`: delivers member copies whose per-key `delay_minutes` elapsed (20 s poll, drops rows older than 24 h). One batch is `loops.drain_pending_sends`, split out so the failure handling is testable. A non-delivery is classified, not counted blindly: `BotOffline` spends no attempt and stops the batch (a minute of downtime used to burn all three attempts and drop every due copy), a FloodWait idles the loop instead of sleeping inside the batch, and `RecipientUnreachable` cancels the row at once | — |
| `source-scores` | Recalculates channel reliability scores | — |
| `access-scheduler` | Warms the scheduled-access cache and notifies members when their access window opens/closes (not the source of truth — `bot_role` recomputes on demand) | — |
| `obsidian-sync` | Reconciles the Debts board with the Obsidian `Долги.md` note (note wins; syncs the claimed/done bit, appends newly detected wins) | `OBSIDIAN_DEBTS_PATH`, `OBSIDIAN_SYNC_ENABLED`, `OBSIDIAN_SYNC_WRITE`, `OBSIDIAN_SYNC_POLL_SECONDS` |
| `salary-sync` | Rereads the salary workbook when its mtime/size change (Excel rewrites the file whole on every save, so that pair is enough) and refreshes `state.salary_book`; the parse runs in `asyncio.to_thread`. Newly ticked payout dates are announced to the account's key holders — the set of already-paid `месяц\|аккаунт` pairs lives in the `salary_sync` settings key (`audit=False`), so a restart between two saves neither loses the notice nor repeats it. Started only when `SALARY_XLSX_PATH` is set, and left out of the watchdog for the same reason as the other feature-gated loops | `SALARY_XLSX_PATH`, `SALARY_SYNC_POLL_SECONDS` |
| `market-monitor` | Fetches crypto prices (+ fiat cross-rates for the bot converter), alerts on volatility | `MARKET_POLL_SECONDS` |
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
TELEGRAM_BOT_TOKEN=...     # the bot is the whole UI now
ADMIN_ID=...               # your Telegram id — the owner
```

Guests get an access key from the bot (`/newkey`), not a token.

Session discovery: if `TELEGRAM_SESSIONS` is empty, all `*.session` files in `./sessions` are used automatically.

`src/pulse_desk/config.py` is the authoritative list of every supported env var with defaults.

## Key conventions

- **All DB access is async** via `aiosqlite`; never use synchronous sqlite3 in new code.
- **Everything imports singletons from `app_ctx`, never from `main`** — `src/pulse_desk/app_ctx.py` holds the settings/state/logger singletons, so modules avoid circular imports with `main.py`. It no longer holds auth dependencies: with the web gone there is nothing to authenticate.
- **Schema changes** require bumping `SCHEMA_VERSION` in `database/_core.py` and adding a migration branch in `database/schema.py` (`init_db`).
- **`set_setting` audits every change into `settings_history`.** Pass
  `audit=False` for keys that carry pure runtime bookkeeping rewritten on a
  timer (a last-run timestamp, a file hash). `obsidian_sync` is written every
  30 s and its audit trail had reached 141 666 rows / **111 MB — 82 % of the
  whole database**, while still sitting inside the 90-day age cap. An age cap
  bounds nothing when a key is rewritten on a timer, so `cleanup_unbounded_tables`
  also caps history **per key** (`SETTINGS_HISTORY_PER_KEY`).
- **Anything blocking goes through `asyncio.to_thread`.** The bot's Telethon
  handlers and uvicorn share one event loop (`init_bot` runs inside the FastAPI
  lifespan), so a synchronous read or a Pillow render freezes the API *and* every
  bot button. Current users: the digest card render and the `/logs` tail
  (`common.tail_lines`, which seeks from the end instead of reading the file).
- **Bot handler errors are visible.** `safe()` wraps every handler: it times it
  (`SLOW_HANDLER_SECONDS`, the early warning for callback-query expiry), counts
  calls/errors/slow runs onto `state`, records an `ERROR` app event, and reports
  through `_tell()` — which falls back to `respond` when the query is already
  answered, because Telethon answers it on the first `edit`/`respond` and a later
  `answer(alert=True)` is then a silent no-op.
- **Session files are secrets** — treat `.session` files like passwords; they are excluded from git via `.gitignore`.
- **The salary workbook is read-only for the app.** It is the owner's accounting
  file, edited in Excel by a human; Pulse Desk only ever opens it for reading, and
  a payout is "taken" when the owner types a date into the «Дата выплаты» column —
  there is no button in the bot that writes back. Anything that needs to change the
  book is a one-off script under `scripts/`, run by hand with the book closed.
- **Bot keyboards cannot carry custom emoji or custom shapes, ever.** Button text
  is a plain string with no `entities` in both MTProto and the Bot API, so the
  Aperture pack renders in card *text* only. Do not try to solve this in
  `keyboards.py` again.
- **A new bot screen is a section, not a branch.** Add a module under
  `src/pulse_desk/bot/sections/`, list it in that package's `SECTIONS`, and
  declare its callbacks in `register(router)` with the gate as `feature=` or
  `admin=`. `tests/test_bot_routes.py` asserts every advertised button resolves
  to a section with the gate it was meant to have — a screen nobody registered
  is otherwise indistinguishable from a stale button.
- **Editing a ping goes through `ping_actions.apply_ping_meta`.** It validates the
  status against `statuses.py`, writes, publishes the live event and mirrors a
  "claimed" into the Obsidian note. When that logic lived in `routers/pings.py`,
  the same action taken from the bot silently skipped the note.
- **Owner-only sections gate on `role == "admin"`, never on a grant code.** An
  empty `permissions` column means "grant everything" for legacy keys, so a new
  feature code would open the admin section for every key issued before it
  existed. Grant codes are for guest-visible sections only.
- **The Telegram Mini App and its tunnel were removed (2026-09-08)** — the
  WebApp panel crashed the owner's Telegram client. Gone: `routers/miniapp.py`,
  `miniapp_server.py`, `miniapp_auth.py`, `tunnel.py`, `static/app/`,
  `webapp_row`, `state.public_url` and the `MINIAPP_*` / `TUNNEL_*` /
  `TAILSCALE_*` / `NGROK_*` / `CLOUDFLARED_*` settings. The bot is inline
  keyboards only. Do not reintroduce a WebApp button.
- **`cryptg` must stay installed.** Without it Telethon decrypts every MTProto packet with `pyaes`, in pure Python, on the event loop thread. With 8 accounts plus the bot that took ~50 % of the loop and starved everything else: bot callbacks expired (`QueryIdInvalidError`), awaited SQLite calls blew their 5 s busy timeout (`database is locked` storms), and the HTTP server went unreachable long enough for the watchdog to restart the app. If the bot ever "hangs" again, check `import cryptg` first.
- **Profiling the running app:** `py-spy record --pid <pid of :8000 listener> --duration 120 --format speedscope --threads`. `database/_core.py` also logs a stack for any DB connection held ≥ `PULSE_DB_TRACE_SECONDS` (default 2 s).
- **There is no frontend.** Node, npm and `node --check` are no longer part of this
  project's toolchain; the only UI is Telegram inline keyboards.
