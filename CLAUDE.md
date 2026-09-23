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

**Interpreter:** `.venv` is based on `.python\3.12.13\` (git-ignored), a copy of the
Python the venv was first built from. That original lives in the Codex app's
`~\.cache\codex-runtimes\codex-primary-runtime`, which Codex replaces on update (the
`.previous-*` folders next to it) — a minor-version bump there would have left the app
and both scheduled tasks unable to start. Do not point `.venv\pyvenv.cfg` back at it.
Rebuilding from scratch: any Python 3.12 → `python -m venv .venv` →
`pip install -r requirements.txt` (the pinned set is complete; CI installs only that).
Files written from a sandboxed agent shell outside the project folder (e.g. under
`%LOCALAPPDATA%`) may be invisible to the real app — keep runtime files in the project.

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

`tests/conftest.py` points `PULSE_DB_PATH`, `PULSE_LOG_DIR` and `database.BACKUP_DIR`
at a temp dir and blanks the Obsidian/salary paths before anything imports the config,
and refuses (and fails the run on) any connection to the live `pulse_desk.db`. Before it
existed, a test that forgot to patch `DB_PATH` wrote into the owner's database (fake
«Bot handler failed: nope» events) and competed with the running app for the write lock.
It is pytest-only: `unittest discover` does not load it, so prefer pytest.

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
token). **Do not reintroduce the web dashboard.**

uvicorn is still the process host and serves exactly one endpoint, `/api/health`:
`scripts/pulse_watchdog.ps1` and `scripts/start_dashboard.ps1` poll it, so replacing the
entry point would mean rewriting both Task Scheduler jobs for nothing.

The one web surface that exists is the **Telegram Mini App «🛰 Панель»** (returned
2026-09-12 at the owner's request; it was pulled on 2026-09-08 over a Telegram client
crash the owner later attributed to a Telegram bug). It is not a dashboard: it opens
from the bot's main menu, lives on its own port behind a tunnel, and carries only what
inline keyboards do badly — a live-typing converter, **logging into a Telegram account**
(a code typed into a Telegram chat is voided by Telegram; a page field is not), account
control, giveaways/wins, debts with multi-select and the salary book. The inline bot stays
complete on its own; the panel is on top, not instead.

Deadline tracking and redeemable-check (чеки) detection were removed in 2026-07 — the
owner works from the bot only, and both features cost a Telegram `GetFullChannel`
per giveaway plus a polling loop. Nothing parses or writes `deadline_*`/`is_check`
any more; the columns survive in the schema (no migration) but stay NULL/0.

Checks came back on 2026-09-23 as a different feature: **auto-claim** of xRocket /
CryptoBot / RedCube checks (`check_claims.py` + `check_claimer.py`, ⚙️ → 🧾 Чеки). It does not
touch `pings.is_check`; its journal is the `check_claims` table. Two rules are the
owner's and are not up for "optimisation": a check posted by one of our accounts (or
the owner) is **never** pressed, and a captcha is **never** solved by code — it is
relayed to the owner, whose tap the script presses in the wallet bot.

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

routers/                   — `health.py` (main app) and `miniapp/` (panel app only).
  A new bot screen belongs in `src/pulse_desk/bot/sections/`, never here.
  routers/miniapp/  — the panel's JSON, one module per area, mounted ONLY on the
                      separate ASGI app in `miniapp_server.py` (its port is the one
                      on the internet). `common.py`: every request verifies the
                      `X-Telegram-Init-Data` header (`miniapp_auth.verify`), then
                      role + grants come from `bot_membership` — the bot's own
                      rules. Owner areas depend on `admin_caller`; **every POST**
                      depends on `fresh_*`, which refuses initData older than
                      `FRESH_SECONDS` (1 h) so a leaked string cannot drive
                      accounts for its whole 24 h read life. Modules reuse the
                      bot sections' helpers instead of re-deriving them: home
                      (`collect_dashboard`, 15 s cache), giveaways
                      (`GiveawayFilter`, `visible_accounts`, `apply_ping_meta`),
                      market (`rate_table`: price of every code in USD, so the page
                      converts while typing; `/convert?q=` uses `parse_query`),
                      accounts (+ login, via `account_login`), debts
                      (`segment_rows`/`total_value`), salary (`sections/salary.visible`,
                      404 when not visible, like the bot's "Неизвестная команда").
                      Guest areas: feed (`sections/feed.fetch` — same FeedFilter,
                      same FTS path, same `mention_any` cut; the list needs
                      `recent`, a `q=` needs `search`), prefs (`allowed_pref_keys`
                      + `bot_prefs.apply_prefs_patch`; 404 for the owner, who has
                      no member row, and the only endpoint on `fresh_caller`
                      rather than `fresh_admin`), analytics
                      (`analytics.build_panel_report`, counted **only over the
                      key's accounts**; the bot's 🏠/📊/📈/🛰 now call the same
                      builder for a guest — see the rule below; `stats` gets the
                      summary and the day chart, `analytics` the rest). `home` adds `me` (role,
                      accounts, notify types, delay, schedule) and `mine` (their
                      accounts' wins, engagement, last wins) for a non-admin.
                      Added 21.09 (ТЗ «развитие панели», five stages): owner —
                      `triage` («Разбор»: debts board + need_action + account
                      problems, one card at a time), `system` (calls
                      `routers.health.health()` itself + `miniapp` journal),
                      `keys` (keys and members' access windows through the bot's
                      setters and `sections.members.add/remove_access_window`),
                      `settings` (fields from `settings_schema`), card actions and
                      `cleanup` in `giveaways`, feed meta/read/ignore in `feed`;
                      guest — `wins` (history, not the queue), `engagement` POST;
                      both — `pulse` (10 s per-person cache, "anything new?"),
                      `undo` (the bot's `bot/undo` snapshot: every status change
                      answers with a token). `common.py` also rate-limits per
                      `tg_id` (owner 120 GET / 60 POST a minute, guest 60 / 10)
                      and puts the caller on `request.state` for the journal:
                      `miniapp_server.journal` writes one `miniapp` app event per
                      successful POST — path + ids only, never the body.
                      `/api/app/feed` and `/giveaways` page by `after=<last id>`
                      (+ `loaded`), a keyset cursor (`get_pings(after=…)`), not by
                      refetching pages 1..N

static/app/                — the panel page. Vanilla JS, no build step, one global
  scope, load order in index.html: icons.js → app.js (App nav stack, api(),
  Sheet, Store = Telegram CloudStorage w/ localStorage fallback, delegated
  `data-act` routing to the current screen's `actions`/`inputs`/`changes`) →
  screens/*.js (App.register) → boot.js. CSP allows scripts from self and
  telegram.org only, so no inline scripts. A screen that updates in place
  (converter typing, debt selection, every switch on «Уведомления») must not
  re-render — that steals focus and refetches. A slider reads `input` for its
  label and `changes` for the save, so a drag is one request, not fifty. Animations move but never fade: a throttled WebView would
  hold a fading screen at opacity 0.
  Per-device state is keyed by Telegram user (`whoami()`): Telegram Desktop
  shares one WebView's localStorage across accounts, and a guest once landed
  on the owner's saved screen. `Saved` (filters, open stack — resumed within
  30 min, dropped when the key no longer opens it), `pd.retry.<id>` (an action
  a stale session refused, offered after reopening; only `RETRYABLE` paths —
  never login), `Offline` (last answer of the read-only screens, shown when
  the PC is off; no post texts, accounts, keys). `/app-sw.js` (static/app/sw.js)
  is a network-first shell cache so the page opens at all while the PC is off;
  it never touches /api. `Pulse` polls `/api/app/pulse` every 30 s while
  visible and only marks tabs/lists as new — lists are never repainted under
  the reader.
  Dev: `scripts/miniapp_dev_link.py` prints a localhost link with initData
  signed by the real bot token (no auth bypass exists in the server).

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
                      persist, notify. Watches **channels and groups**
                      (`WATCHED_CHAT_TYPES`; groups = supergroups and the
                      discussion group behind a channel, i.e. comments). In a
                      group `own_mention` also counts Telegram's
                      `message.mentioned` flag for the receiving account, but
                      **not when the message is a reply** (owner's order,
                      18.09): that flag is set both for a real mention and for
                      anyone answering the account, and the answers were the
                      bulk of the group noise. A reply that does name the
                      account (@name, mention entity, t.me link) still arrives
                      through `extract_mentions`. Messages sent by our own
                      accounts (`state.connected_user_ids`) are skipped there;
                      private chats stay out. **One win, one card, only while it
                      is news** (owner's order, 21.09): a post whose newest stamp
                      (posting or last edit) is > 24 h old is backlog — stored,
                      no card (`is_backlog`, `NOTIFY_MAX_AGE_SECONDS`; a sweep of
                      a newly joined channel carded a win from 24.02). A
                      discussion chat's copy of a channel post (sent by the
                      channel, `telegram_ping_watcher.channel_post_ref`) is
                      stored **as that post** (`record_as_channel_post`), so the
                      copy and the channel read share one row and one card in
                      either order; a win pasted/forwarded by a person into
                      another chat after the channel's is a dedupe copy and gets
                      no card (`copied_from_another_chat`; same chat + same text
                      is a new win). A tag bot's ad (≥3 hidden user
                      links on emoji, no tracked name written out —
                      `telegram_ping_watcher.is_mass_tag`) is stored without a
                      card or member copy, at the owner's request (16.09).
                      A chat on the ignore list (`ignored_chats.py`, settings key
                      `ignored_chats`, `state.ignored_chat_ids`) is skipped before
                      anything is read; 🔇 on a group card adds it, ⚙️ → 🔔 →
                      «🔇 Игнор-чаты» (`bot/sections/ignored.py`, `igc:*`) lists
                      and returns them
  ping_notify.py    — **a ping card is never lost.** Owner's cards are an outbox
                      on the `pings` row (schema 24): `notified_at` /
                      `win_notified_at`, NULL = owed. `notify_detected_ping`:
                      owner's card first → `before_broadcast` (giveaway analysis,
                      its score feeds the member filter) → one-time member
                      broadcast, which edits the owner's card (score line, 🙈,
                      moderation buttons). `notify-retry` delivers what is owed
                      (90 s grace, 24 h window; older ones are settled with one
                      summary). `_inflight` is claimed *before* the row is read,
                      so a live update and a sweep racing on one post send one
                      card. A pass that sends no card on purpose (backlog,
                      `/win`) settles the row itself
  scan_engine.py    — full_history_scan, scan_single_account, mention backfill.
                      A sweep spends no request on a channel whose newest
                      message id (free from the dialog list) is not past its
                      checkpoint; idle channels only get the recent-window
                      (edit) pass once an hour, active ones every sweep.
                      Groups are never swept (chatter); instead
                      `catch_up_group_mentions` reads the groups whose dialog
                      carries `unread_mentions_count` with
                      `InputMessagesFilterMyMentions` — the nightly gap for
                      comments costs one request per group that has one, and is
                      skipped while (unread, newest id) is unchanged.
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
  public_preview.py — The other path for such a card: its public t.me page.
                      `recover_public_text` caches per post (re-read on a new
                      edit date or after 30 min, failures 5 min) — every sweep on
                      every account used to refetch and re-log the same post
                      several times a minute
  check_claims.py   — Wallet-bot checks (pure): a check is `t.me/<bot>?start=<code>`
                      (URL button, hidden link or text) for `xrocket` / `send` /
                      `CryptoBot` / `redcubebetbot` (casino, inline @redcube) **and** a post that reads like one («чек» in the
                      text or a «Получить/Receive» button) — referral links in
                      chatter do not count; CryptoBot codes only `CQ…` (invoices are
                      `IV…`), xRocket anything but `inv…` (its invoices; a personal
                      check it calls «перевод»), RedCube `C` + 11 (`U<id>` is a
                      profile link). Amounts with a ticker or in `$`. Amount, addressee
                      («для @X»), a password written in the post, the own-sender
                      rule, reply classification (claimed / gone / not_for_you /
                      own / premium / captcha / password / subscribe / unknown —
                      order matters: «уже активирован» is `gone` before
                      «активирован» can read as a win), config (`check_claim` key:
                      `mode` claim|watch|off, `disabled` sessions; absent = claim, all)
  check_claimer.py  — Presses checks (I/O). `on_message` runs in **every account's**
                      live handlers *before* `remember_message` (that dedupe lets one
                      account act for all; here each account presses for itself) and
                      before the ping pipeline, so a claim waits on nothing.
                      **No pressing old or dead checks:** a general check only
                      ≤ 5 min after posting (later arrivals are the catch-up or
                      xRocket editing an old post's counter — every edit is a
                      fresh update); a personal check for one of ours ≤ 24 h (the
                      group unread-mention catch-up hands its messages over,
                      `live=False`). A post the bot itself marked as used up
                      («Чек активирован», «10/10», a relabelled button —
                      `post_is_dead`, read only in text sent via a bot) is never
                      pressed; a `gone` / `invoice` answer marks the code dead for
                      every account (`state.dead_check_codes`). `load()` restores
                      the last 24 h of attempts from the journal at startup, so the
                      daily restart does not press anything a second time. **One
                      account per check** (owner's order, 23.09 — four 5 USDT checks
                      in @ludka2k33 were pressed by all eight accounts, 32 presses,
                      32 «уже активирован»): a general check by the first of ours to
                      see it (`_taker`; one switched off in 🧾 Чеки hands it to the
                      next online account that is on), a personal one only by the
                      addressee (matched by live `username`), whichever account
                      received it. The dedupe is per code (`claim|<bot>|<code>` in
                      `state.check_seen`), so the others' copies of the update press
                      nothing. **Speed:** the press (`_press_start`) goes out
                      before any lock, lookup or SQLite — the wallet bot's peer is
                      cached per account by `warm_up`; only reading the answer and
                      follow-up steps queue per (account, bot). Answers to presses
                      that overlapped are attributed by order (`_replies_for`: the
                      k-th of a burst of our «/start» gets the k-th answer). The
                      journal's `press_ms` (schema 26) is update-received → startBot
                      answered; the log line also gives how old the post was.
                      **Own checks are never pressed:** sender is one of our accounts
                      or `ADMIN_ID`; `message.out` (posted as a channel / anonymous
                      admin — remembered for the others); sender is a chat one of our
                      accounts administers (`note_dialog` harvests it from the sweep's
                      dialog list, persisted in `check_claim_admin_chats`); or a code
                      the wallet bot showed one of our accounts in its own chat
                      before any chat carried it. Pressing = `messages.startBot`
                      (the «Получить» button), then the bot's chat is **polled** for
                      the answer (Conversation API races the dispatcher). One
                      conversation per (account, bot) at a time (`_lock_for`): four
                      personal checks in five seconds interleaved replies otherwise.
                      `subscribe` → joins ≤ 3 linked channels and presses the bot's
                      «проверить»; `password` → types the one written in the post, or
                      waits 10 min for the check author's next post in that chat
                      («пароль: X», «🔑 X» or a bare token — `follow_up_password`,
                      `state.check_awaiting_password`) and types it in;
                      `captcha` / `unknown` / password nobody wrote → **relay card**
                      to the owner: the bot's text + picture, its callback buttons
                      mirrored as `ck:b:<token>:<r>:<c>`, «✍️ Ответить» (typed text
                      sent from the account), «🔁 Повторить». Relays
                      live 30 min (`bot-janitor`). **Money never moves out:** a reply
                      that reads as an invoice («Счёт на…», «оплатите») ends the
                      attempt as `invoice`; a button labelled like a payment,
                      transfer, withdrawal, top-up or bet (`money_out`) is never
                      pressed and never mirrored onto a relay card, and
                      `relay_press` refuses such an index even from a forged
                      callback. A post with a wallet-bot button that does not read
                      as a check is logged once with its label and text (a new link
                      format would otherwise go silent)
  miniapp_auth.py   — Telegram Mini App initData validation (pure): drop `hash`,
                      join the rest as sorted `k=v` lines, HMAC-SHA256 under
                      HMAC(b"WebAppData", bot_token), reject anything older than
                      `max_age` (24 h). No session — verified on every request
  miniapp_server.py — The panel's own ASGI app + uvicorn task on MINIAPP_PORT,
                      bound to 127.0.0.1, docs/OpenAPI off. Adds `no-store` to
                      `/api/*` and a CSP to the page
  tunnel.py         — Runs the tunnel agent as a child process and owns
                      `state.public_url` (`tunnel` job); the «🛰 Панель» button
                      (`keyboards.webapp_row`) exists only while it is set.
                      Providers are argv builders + pure URL extractors:
                      `tailscale` (default, `tailscale funnel <port>`, stable
                      ts.net host), `ngrok` (quarantined by Defender here),
                      `cloudflared` (quick tunnel; api.trycloudflare.com blocked here).
                      **Orphans:** Windows does not kill children with their
                      parent, and a leftover `tailscale funnel 8010` kept the :443
                      listener ("listener already exists for port 443", panel
                      hidden). The agent is now assigned to a kill-on-close job
                      object (`_bind_to_app_lifetime`), `kill_orphaned_agents`
                      stops an our-port agent whose parent is gone (scanned on a
                      fresh start or when the port is busy), and
                      `restart_app.ps1` uses `taskkill /T`.
                      **NoState is not a warm-up.** Tailscale here is not in
                      Unattended Mode, so tailscaled runs the tailnet only
                      while a client of the user holds a connection —
                      normally the tray app `tailscale-ipn.exe`. Without one
                      it sits in NoState forever and `tailscale funnel` exits
                      on it before its own connection can count (23.09:
                      tailscaled crashed 11:02, came back with no tray app,
                      panel dark until opened by hand). On NoState the job
                      starts the tray app (`_wake_tailscale_gui`, ≤ 1 per
                      5 min, not when it already runs) **through
                      explorer.exe**, so it belongs to the shell and survives
                      `taskkill /T`, then retries in 10 s — up in ~25 s.
                      `Stopped` (a manual Disconnect) is left alone.
                      **Outages:** retries back off (10 → 120 s) and a
                      repeated cause is logged once
                      (`TunnelHealth`). Down ≥ 15 min → one owner message, and
                      one more when the panel is back — the watchdog does not
                      cover this job (no heartbeat)
  account_login.py  — Login to a Telegram account from the panel: `request_code`
                      → `submit_code` → `submit_password` / `cancel`, returning
                      `LoginResult`. Pending logins hold a connected client in
                      `state.pending_auths` until `PENDING_AUTH_TTL_SECONDS`
                      (swept on each new request); a code request per phone is
                      limited to one per 60 s. On success the temp client is
                      closed, the session joins `state.session_names` and
                      `start_client` launches it. Phone, code and password never
                      reach the log or `app_events` — only the session name
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
                      scan, system, broadcast, roulette, checks, legacy). Each exposes
                      `register(router)` plus its own renderers, so the slash
                      command and the button can never render different screens.
                      `legacy.py` answers the underscore callbacks still sitting
                      on old messages in the chat (`fav_`, `read_`, `gconfirm_`).
                      The feed's 🔎 is gated on the **`search`** grant
                      (`feed.can_search`, `keyboards.feed_keyboard(can_search=)`):
                      `/search` always needed it, the in-feed button did not, so
                      one key had two different answers to the same question
  bot/pending.py    — free-text capture (Telethon has no ConversationHandler):
                      one armed entry per sender in `state.bot_pending_inputs`,
                      300 s TTL, swept on every new prompt. A section declares
                      its input kind *with its consumer* (`register_prompt`), so
                      adding "type a value here" is a local change; `admin=True`
                      is enforced in `consume`. **Clean chat:** the entry keeps
                      the ids of the «✍️» prompt and the screen it was armed
                      from; after the consumer answers, prompt + typed answer +
                      old screen are deleted (`keep_screen=True` for kinds armed
                      from notification cards: ping note/tag, roulette time).
                      A consumer that cannot accept the text **raises
                      `InputRejected(message)`** instead of answering — the
                      prompt stays armed, up to `MAX_ATTEMPTS` (3). «✖️ Отмена»
                      (`st_x`) deletes the prompt and keeps the screen
  bot/undo.py       — «↩️ Отменить» for 30 s after a status change (debts
                      claim/scam/mass claim, giveaway status, feed status):
                      `snapshot` before, `remember(sender, rows, label, back)`,
                      `undo_row(token)` prepended to the redrawn screen; `ud:<token>`
                      (sections/undo.py) restores and re-dispatches `back`.
                      In-memory (`state.bot_undo`), one per sender
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
                      An unfiltered page 1 still encodes as plain `menu_giveaways`.
                      Owner's «✖ Убрать из очереди…» (`gw:x:<state>`) is the same
                      feed in removal mode: a row tap (`gw:rm:<id>:<state>`) sets
                      `closed`/`closed` and redraws the page with «↩️ Отменить».
                      A mode, not a ✖ per row: Telegram splits a row evenly and
                      the chat name would be cut in half. The panel has ✕ per
                      row instead; its «Вернуть» posts the old `status`+`action`
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
  jobs.py           — Task supervision primitives (start_tracked/supervised_task),
                      `EXPECTED_JOBS`, `feature_job_polls` (which feature-gated
                      jobs this install runs → `watchdog.default_thresholds`)
  autoclean.py      — Auto-delete of minor notifications (pure): owner choice in
                      the `autoclean` key (⚙️ → 🔔 «🧹 Удалять мелкие»), member's
                      in `notification_prefs.autoclean_hours`; kinds mention /
                      market / system only, wins and giveaways never; ≤47 h
                      (Telegram's 48 h delete window). Rows in
                      `bot_ephemeral_messages`, deleted by `bot-janitor`
  account_health.py — Account problems (pure): unauthorized / banned /
                      session_ip_conflict / error, stuck reconnecting >30 min,
                      online but no update 12 h (`last_update_at` set by the
                      account's update handlers); `spam_verdict` for @SpamBot
  prize_value.py    — Prize amount from post text via the converter vocabulary
                      («5 USDT», «4$», «100 грн», «1💵»), priced from the newest
                      market snapshot; `value_by_account` → «💵 Незабрано» on
                      the debts board and `db:v` per account
  manual_win.py     — `/win <link> [@account]`: reads the post with any account
                      that can, or records from the link alone; saved through
                      the normal helpers as a claim_prize debt
  latency.py        — Post → detection and win edit → win flag delays (pure);
                      📈 Аналитика «Задержка» tab (`an:lat`). Needs
                      `pings.edited_at` / `win_detected_at` (schema 23)
  dedupe.py         — Copies of one winners post (same normalised text, a
                      shared tracked account, 72 h) glued to a primary (channel
                      > group > private, then earliest) via `pings.duplicate_of`;
                      the debts board lists primaries, `apply_ping_meta`
                      propagates statuses to copies. Linked on save
                      (`ping_pipeline.link_win_copies`), backfilled at startup
  report.py         — Weekly / monthly report (pure data + text; card in
                      `render/screens.build_report_card`); `rp:*` (🖼 Отчёт in
                      the management grid), `/report [месяц]`, sent by
                      `weekly-report` on Monday / the 1st
  vacation.py       — Vacation mode (pure routing): owner muted for minor kinds,
                      optional delegate member raised to `admin` for the period
                      and copied on owner notifications; `vc:*` (🏖 Отпуск),
                      ended by `bot-janitor` when the date passes
  housekeeping.py   — Pure maintenance rules, unit tested: `vacuum_due`
                      (freelist share / stored interval), backup rotation
                      (`backups_to_delete`, `backup_needed`), `FILE_RULES`,
                      `disk_low`, `gap_worth_reporting` (downtime report)
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
                      Columns of «Выплаты по месяцам» are positional: D крипта,
                      E к выплате, F скины, G к выплате, **H йобо, I к выплате**,
                      then **J расходы, K удержано**, L итого, M дата выплаты,
                      N статус. The йобо pair was added 18.09 by
                      `scripts/add_salary_yobo_column.ps1` (the type existed in
                      «Настройки» and in the journal, but no sheet summed it, so
                      0.55 $ of owner shares reached nobody).
                      **Expenses are deducted before the split** (19.09,
                      `scripts/add_salary_expenses_column.ps1`): J is the owner's
                      manual entry, `K = J × доля`, and «Итого» is `E+G+I−K`,
                      i.e. `(выигрыш − расходы) × доля` arranged so the per-kind
                      columns keep their meaning and the deduction stays a visible
                      line. Running the accounts costs money and the book had
                      nowhere to put it, so the only way to carry a cost was to
                      quietly shade someone's percentage — a rate that does not
                      mean what it says is a rate nobody can check. «Сводка» got
                      the same pair, and its `B17` had to lose `K11`: `C19`
                      compares `B17` against the monthly total and would otherwise
                      declare the book broken.
                      The parser does **not** hardcode where «Итого» sits —
                      `month_columns` reads the J5 header, because the owner
                      re-saves the book in Excel and between that save and the
                      next restart a hardcoded layout would read «Итого» as
                      expenses and show everyone zeros. An empty expense cell is
                      0, so every past month stays figure-for-figure the same;
                      `uncounted_total` adds `withheld` back (it is deducted on
                      purpose, not lost), and `collect_issues` reports a month
                      whose expenses ate the whole share instead of clamping it.
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
  _core.py    — _connect(), shared helpers, SCHEMA_VERSION (current: 26 —
                check_claims.press_ms; 25 — check_claims, the check auto-claim journal; 24 —
                pings.notified_at / win_notified_at, the owner-card outbox;
                23 — bot_ephemeral_messages, bot_access_keys.max_uses,
                pings.edited_at / win_detected_at / duplicate_of)
  ping_notifications.py — owed-card queries for ping_notify (mark, list, expire, count)
  schema.py   — init_db + migrations   backups.py  — file backups
  pings.py    — ping CRUD/filters/FTS  checkpoints.py — scan checkpoints
  giveaways.py — candidates/actions/reconcile   boards.py — giveaway/debt boards
  channels.py — channel profiles, source scores
  market.py   — market snapshots       scan_runs.py — scan-run bookkeeping
  events.py   — app event log          settings_kv.py — key-value settings
  outbox.py   — SSE outbox             push.py — push subscriptions
  bot_access.py — bot keys/members     stats.py / maintenance.py — stats, cleanup
  access_windows.py — scheduled-access windows (access_schedule) + audit
  ephemeral.py — auto-clean deletions queue   dedupe.py — duplicate_of links
  check_claims.py — one row per (session, bot, code) check attempt; written
                after the press, never read before it (a busy SQLite must not
                slow a claim); own checks once per code with an empty session;
                trimmed at 90 days by `cleanup_unbounded_tables`
  bot_access.py also: max_uses (one-time invites, `/invite`, 🎟 in the key panel;
  a person already in is not counted against the limit)
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
                             add_salary_yobo_column.ps1 (gives «йобо» its own
                             pair of columns — H «йобо за месяц» and I «к
                             выплате» — on «Выплаты по месяцам» and «Сводка»,
                             and re-points D at Крипта alone. Drives Excel over
                             COM rather than editing the XML: Excel is what
                             shifts every formula, width and merged cell that
                             pointed past the insert, and it recalculates and
                             saves, so the cached values the bot reads are fresh.
                             Refuses while that book is open, makes a dated
                             backup, closes without saving if anything throws.
                             It replaced fix_salary_yobo_formulas.py, which
                             folded йобо into the Крипта column — running that
                             one now would count йобо twice),
                             add_salary_expenses_column.ps1 (same COM approach:
                             inserts J «Расходы» (manual, carries the «Дата
                             выплаты» fill so the book's «жёлтое = вписать
                             руками» convention still reads) and K «Удержано»
                             before «Итого» on both money sheets, re-points
                             «Итого» at `E+G+I−K` and «Сводка» `B17` at
                             `E11+G11+I11−K11`. Refuses unless J5 still says
                             «Итого» — an insert into an unexpected layout would
                             land two columns inside someone's data)
```

### Background jobs (always running)

| Job | Purpose | Key env var |
|-----|---------|-------------|
| `auto-scan` | Sweeps channels for new messages every `SCAN_INTERVAL_SECONDS` **measured from the start of the cycle** (the sleep is the remainder of the interval, so sweep time no longer stacks on top of it; minimum 30 s gap). Scans only — retention moved to `maintenance`. After the first sweep that follows a long downtime (see `maintenance`) it sends the owner a «⏳ Пропуск наверстан» card: how long nothing was watching and what the sweep found | `SCAN_INTERVAL_SECONDS` (default 900 s) |
| `maintenance` | Stamps `last_alive_at` every 5 min (the next start compares against it to tell a restart from the nightly shutdown — `loops.detect_downtime`), and once an hour (first pass 10 min after start) runs `run_maintenance_once`: age cleanup (`PINGS_RETENTION_DAYS`; wins, giveaways and favourites are never aged out), unbounded-table trim (`scan_runs`/`settings_history`/`access_audit`/`giveaway_actions`/`scan_checkpoints`; `settings_history` capped **per key** as well as by age; `scan_checkpoints` drops channels unseen for 90 days **and** rows of sessions that no longer exist or usernames no longer tracked), the size cap (evicts oldest non-favorite/non-win pings to `pulse_desk_archive.db`), archive-record pruning, daily pruning of broadcast/pending-send/outbox bookkeeping (was startup-only), **VACUUM when ≥20 % and ≥16 MB of the file is free pages** (`housekeeping.vacuum_due`; the old in-memory "every 168 h since start" never fired on a PC that reboots nightly and left 114 MB of free pages in a 136 MB file), a daily backup + rotation, the `housekeeping.FILE_RULES` file prune (render cache, stale login QR files) and a once-per-episode low-disk alert. State in the `maintenance` settings key (`audit=False`), last result on `state.maintenance_stats` (→ `/api/health`, 🩺 Диагностика, 💾 Бэкапы, where «🧹 Уборка сейчас» runs a pass) | `DB_MAX_SIZE_MB`, `DB_ARCHIVE_ENABLED`, `ARCHIVE_RETENTION_DAYS`, `SCAN_RUNS_RETENTION`, `AUDIT_RETENTION_DAYS`, `VACUUM_INTERVAL_HOURS` (fallback), `DISK_FREE_ALERT_MB`, `BACKUP_*` |
| `daily-digest` | Ticks every 30 s and sends once per slot (`digest.digest_due`; handled slot date in the `digest_state` key, `audit=False`), so a 10:00 slot the PC was off for still goes out when it boots — within `DIGEST_CATCHUP_HOURS` (4 h). Sends the daily digest to admin + opted-in bot members at a configurable time (settings key `digest`, default 10:00) as a two-image album: a ping treemap (24 h, wins/giveaways/mentions per chat, today-vs-yesterday counter) and a crypto heatmap (all 8 tracked coins, area by market cap, day delta from our own `market_history` snapshots, leader/laggard, UAH line). The caption carries only the win links; if Pillow can't render, the old text digest goes out instead | — |
| `roulette-reminder` | Daily nudge to spin the yobo-bot roulette from every account. The alarm time *is* the previous day's last-click time, reported back by the owner (button, `/roulette 21:47`, or a bare `21:47` in the bot chat). Ticks every 30 s instead of sleeping to the target, so a time reported mid-day applies at once and a slot missed while the PC was off still fires once on the next tick | settings key `roulette` |
| `bot-janitor` | Every 60 s: deletes unanswered «✍️» prompts (5 min TTL), closes abandoned Mini App logins (`account_login.sweep_expired` — each held a connected Telethon client), forgets stale feed queries / debt marks / undo snapshots and check relay cards (30 min), deletes due auto-clean notifications (`bot_ephemeral_messages`) and ends an expired vacation | — |
| `account-health` | Every 5 min (first after 3 min): `account_health.account_problem` per account, pages the owner once per problem kind and once on recovery (reported kinds persisted in `account_alerts`, so the morning restart does not re-page); hourly `get_me` probe per online account catches revoked/banned sessions | — |
| `weekly-report` | Ticks every 60 s; Monday 10:15 sends the previous ISO week, the 1st sends the previous month (catch-up 72 h counted from the period's own slot, so a PC off for the whole Monday / 1st still gets it; sent periods in `report_state`) | — |
| `bot-connection` | Reconnects the bot client after Telethon gives up, so incoming updates never stall (started only when the bot is configured) | — |
| `bot-start-retry` | One-off, only when the first `init_bot` failed before the client came up (network not ready at boot — 15.08 the bot was dead 09:25→12:46). `bot_connection.keep_starting_bot` retries with backoff, then tells the owner how long it was down and how many cards are being re-sent | — |
| `notify-retry` | Every 30 s: delivers owner ping cards still owed (`ping_notify.retry_owed_notifications`) with a «⏳ С опозданием» footer; stops at the first failed send; cards owed > 24 h are settled with one summary message. Count in `/api/health` (`owed_ping_cards`) and 🩺 Диагностика | — |
| `pending-sends` | Drains `bot_pending_sends`: delivers member copies whose per-key `delay_minutes` elapsed (20 s poll, drops rows older than 24 h). One batch is `loops.drain_pending_sends`, split out so the failure handling is testable. A non-delivery is classified, not counted blindly: `BotOffline` spends no attempt and stops the batch (a minute of downtime used to burn all three attempts and drop every due copy), a FloodWait idles the loop instead of sleeping inside the batch, and `RecipientUnreachable` cancels the row at once | — |
| `source-scores` | Recalculates channel reliability scores | — |
| `access-scheduler` | Warms the scheduled-access cache and notifies members when their access window opens/closes (not the source of truth — `bot_role` recomputes on demand) | — |
| `obsidian-sync` | Reconciles the Debts board with the Obsidian `Долги.md` note (note wins; syncs the claimed/done bit, appends newly detected wins) | `OBSIDIAN_DEBTS_PATH`, `OBSIDIAN_SYNC_ENABLED`, `OBSIDIAN_SYNC_WRITE`, `OBSIDIAN_SYNC_POLL_SECONDS` |
| `salary-sync` | Rereads the salary workbook when its mtime/size change (Excel rewrites the file whole on every save, so that pair is enough) and refreshes `state.salary_book`; the parse runs in `asyncio.to_thread`. Newly ticked payout dates are announced to the account's key holders — the set of already-paid `месяц\|аккаунт` pairs lives in the `salary_sync` settings key (`audit=False`), so a restart between two saves neither loses the notice nor repeats it. Started only when `SALARY_XLSX_PATH` is set, and left out of the watchdog for the same reason as the other feature-gated loops | `SALARY_XLSX_PATH`, `SALARY_SYNC_POLL_SECONDS` |
| `market-monitor` | Fetches crypto prices (+ fiat cross-rates for the bot converter), alerts on volatility | `MARKET_POLL_SECONDS` |
| `bot-service` | Telegram bot for notifications + inline menus | `TELEGRAM_BOT_TOKEN` (optional) |
| `miniapp-server` | Serves the panel on `MINIAPP_PORT`, alone on that port (started only when enabled; not in the watchdog, like the other feature-gated jobs) | `MINIAPP_ENABLED`, `MINIAPP_PORT` |
| `tunnel` | Keeps the tunnel agent up and `state.public_url` current, so the «🛰 Панель» button has an HTTPS origin | `MINIAPP_ENABLED`, `TUNNEL_PROVIDER`, `TAILSCALE_BIN`, `NGROK_DOMAIN`, `NGROK_BIN`, `CLOUDFLARED_BIN` |

### Data flow for a "ping"

0. Live handlers first hand the message to `check_claimer.on_message` (per account, no
   await) — a wallet-bot check is pressed before anything below runs
1. Live `NewMessage`/`MessageEdited` handlers, the `auto-scan` sweep (channels), the group
   unread-mention catch-up and the global-search pass all call `process_ping_message`
2. `telegram_ping_watcher.py` parses messages, matches tracked usernames + win/giveaway keywords
   (in groups also `message.mentioned` for the receiving account, replies excluded)
3. Matches written to `pings` (card owed); checkpoints updated so next scan is incremental
4. `ping_notify` sends the owner's card (silent when muted, never dropped), then the member
   broadcast; `notify-retry` delivers any card still owed

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
- **Startup reconciles must be idempotent.** `startup_maintenance` runs on every
  start (the PC reboots daily): wins first (`reconcile_win_flags`), then
  giveaways, then outcomes, then dedupe. Each writes and counts only rows that
  actually change — a second start must log nothing. Rules that bit: a channel
  win always keeps `is_giveaway=1` (clearing it wiped `giveaway_status`, even
  «claimed», and the next sweep undid it); owner decisions (claimed/scam/missed)
  are never overwritten; wins not judged by text — the global-search placeholder
  (`TEXTLESS_CARD_PREFIX`) and `/win` rows (`MANUAL_WIN_MARK`) — are never un-won.
  The scanner must agree with them or every sweep undoes a start: `save_ping` keeps a
  win's `is_win` and its higher `priority_score` sticky, and `ping_pipeline.apply_priority`
  applies the same 90 floor to a result post as `outcome_target` (it scored 85, so 17
  rows flipped 85↔90 between every sweep and every start).
  `tests/test_core.py::test_startup_reconciles_are_idempotent` pins it.
- **A mention always reaches the owner (owner's order, 2026-09-16).** Nothing may
  drop the owner's ping card: quiet hours, the notification switch, filters and
  rules, vacation mode and the repeat throttle only make it **silent**
  (`bot_notify.owner_card_silent`); they still decide the *member* broadcast.
  The only mentions stored without a card are backlog (post and edit > 24 h
  old), tag-bot ads and a copy of a win already stored in another chat — see
  `ping_pipeline`. A
  card that could not be sent stays owed (`pings.notified_at IS NULL`) and
  `notify-retry` sends it. Any new code path that stores a ping without meaning
  to announce it must settle it (`mark_ping_notified`), or a card goes out 90 s
  later. Account usernames change (MCshinra = session `w3v8f0rm`,
  Megatronus_praim = `Swight0`, ktkerf427 = `Timofey02513`): match an account by
  its live `get_me().username`, never by the session file name.
- **Nothing periodic may be timed from process start.** The PC is switched off
  every night, so "every N hours since startup" never fires. Store the last-run
  time in a settings key (`audit=False`) or trigger on the measured condition —
  see `maintenance` (`last_vacuum_at`, freelist share) and `digest_state`.
- **Backups are snapshots, not file copies.** `database/backups.py` uses
  `sqlite3.Connection.backup` (reads through the WAL, safe under writes) and
  zips the result to `backups/pulse_desk_YYYYmmdd_HHMMSS.db.zip`. `shutil.copy2`
  of the main file missed everything still in `-wal`, and after a kill
  mid-checkpoint was not even consistent. Rotation (`housekeeping.backups_to_delete`):
  3 newest + one per day for 7 days + one per week for 4 weeks, then the oldest
  go until `BACKUP_MAX_TOTAL_MB` fits; the startup backup is skipped when the
  newest copy is younger than `BACKUP_MIN_INTERVAL_HOURS`. Old plain `.db`
  copies are listed and rotated the same way.
- **Anything blocking goes through `asyncio.to_thread`.** The bot's Telethon
  handlers and uvicorn share one event loop (`init_bot` runs inside the FastAPI
  lifespan), so a synchronous read or a Pillow render freezes the API *and* every
  bot button. Current users: the digest card render, the `/logs` tail
  (`common.tail_lines`, which seeks from the end instead of reading the file),
  backups (snapshot + zip) and the file prune in `maintenance`.
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
- **A guest is counted over their own key, never over the base (owner's order,
  2026-09-18).** A key holder pressed «📊 Сводка» and got the owner's 🛰 Пульт:
  790 mentions, 588 important, the history-scan progress, 4154 channels. The
  grant (`stats`) was right; the numbers behind it were everyone's. Every
  read-only screen now branches on `role`: the owner keeps `collect_dashboard` /
  `build_analytics` / `build_detailed_analytics`, a guest gets
  `analytics.build_panel_report(perms["accounts"], visible_accounts(perms))` —
  the very report the Mini App already served — rendered by `member_home_card`,
  `member_summary_card`, `member_analytics_card` and `analytics.member_status`.
  An empty `accounts` whitelist still means "all accounts", exactly as in the
  panel. Owner infrastructure (scan progress, channel counts, uptime, DB size,
  job list, account problems, the «Качество» tab that needs the global report)
  is not scoped down for a guest — it is absent.
  `tests/test_bot_member_scope.py` pins it: the guest path must never await the
  global builders.
- **Owner-only sections gate on `role == "admin"`, never on a grant code.** An
  empty `permissions` column means "grant everything" for legacy keys, so a new
  feature code would open the admin section for every key issued before it
  existed. Grant codes are for guest-visible sections only.
- **The panel's port is on the internet.** Nothing but `routers/miniapp` may be
  mounted on `miniapp_server`; every endpoint depends on `current_caller` (or a
  stricter dependency), and every state-changing one on `fresh_admin` /
  `fresh_caller`. `tests/test_miniapp_api.py` pins the gate (forged, stale,
  guest). Owner areas use `admin_caller`, never a grant code (see below).
- **The Mini App was removed on 2026-09-08 and returned on 2026-09-12.** The
  removal was over a Telegram client crash; the owner later put it down to a
  Telegram bug, not the panel. If a crash comes back, the fastest isolation is
  `MINIAPP_ENABLED=false` (button and tunnel both disappear, the bot is unchanged).
- **`cryptg` must stay installed.** Without it Telethon decrypts every MTProto packet with `pyaes`, in pure Python, on the event loop thread. With 8 accounts plus the bot that took ~50 % of the loop and starved everything else: bot callbacks expired (`QueryIdInvalidError`), awaited SQLite calls blew their 5 s busy timeout (`database is locked` storms), and the HTTP server went unreachable long enough for the watchdog to restart the app. If the bot ever "hangs" again, check `import cryptg` first.
- **Profiling the running app:** `py-spy record --pid <pid of :8000 listener> --duration 120 --format speedscope --threads`. `database/_core.py` also logs a stack for any DB connection held ≥ `PULSE_DB_TRACE_SECONDS` (default 2 s).
- **No frontend toolchain.** Node, npm and `node --check` are not part of this
  project; the panel's JS is checked by opening it (the dev link above) in a browser
  and reading the console.
