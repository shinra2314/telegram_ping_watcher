# Aperture Mini App — Design

**Date:** 2026-09-07
**Branch:** `redesign/bot-aperture`

## Problem

The owner wants bot buttons that look like the reference screenshots: dark rounded
cards with custom line-art icons, a persistent bottom pill bar, gradients.

Telegram cannot do this in a bot keyboard. `KeyboardButtonCallback.text` (MTProto)
and `InlineKeyboardButton.text` (Bot API) are plain strings with no `entities`
field, so custom emoji, stickers, colours and shapes are all unreachable there.
This is already recorded in `CLAUDE.md` as a known limit and it still holds.

Both reference screenshots are a Telegram **Mini App** (WebApp): HTML + CSS
rendered inside Telegram. That is the only surface that gives custom icons and
custom button shapes.

## Decision

Hybrid. A Mini App becomes the rich surface; the existing inline keyboards stay
as the fallback for guests, notifications and quick actions. Nothing about the
current bot UI is removed.

First release covers **Home**, **Giveaways** and **Rates + Converter**. Recent
mentions, Summary, Analytics, Management and Keys stay in the inline keyboards.

## Architecture

```
Telegram client (phone)
  └─ KeyboardButtonWebView → https://<tunnel>/app
        ↓ initData (HMAC-SHA256 keyed by the bot token), sent on every request
  miniapp ASGI app  ← SEPARATE app on its own port (default 8010)
    ├─ routers/miniapp.py                  the page + /api/app/*
    ├─ src/pulse_desk/miniapp_auth.py      NEW, pure: parse + verify initData
    ├─ src/pulse_desk/bot_permissions.py   reused: per-key grants
    └─ database/*                          reused
  src/pulse_desk/tunnel.py   NEW: cloudflared quick-tunnel supervisor
  static/app/                NEW: the Mini App page (its own CSS/JS)
```

### Why a second ASGI app on its own port

A Cloudflare quick tunnel forwards a whole origin; it cannot be restricted to a
path prefix. Tunnelling `:8000` would publish the dashboard, every `/api/*`
route and the SSE stream to the internet. A second ASGI instance on `:8010`
mounts **only** the Mini App router, so the tunnel exposes nothing but
initData-authenticated endpoints. Cost is roughly 30 lines: a second
`uvicorn.Server` run as an asyncio task alongside the main one.

`main.py` keeps serving the dashboard on `:8000`, bound to `127.0.0.1`, untouched.

## Components

### 1. `src/pulse_desk/tunnel.py` — background job `tunnel`

Follows the shape of `process_supervisor.py`: `subprocess.Popen` + a thread
draining output + an asyncio supervise loop with capped backoff.

- Spawns `cloudflared tunnel --url http://127.0.0.1:<MINIAPP_PORT>`.
- Extracts `https://[a-z0-9-]+\.trycloudflare\.com` from the child's output.
- Publishes it to `state.public_url` and persists it under the `tunnel`
  settings key (so a restart has a last-known value while the new tunnel warms).
- The URL changes on every restart. That is harmless: WebApp buttons are built
  at send time from `state.public_url`, never cached in a callback payload.

Gated by `MINIAPP_ENABLED` (default `false`). If the flag is off, `cloudflared`
is missing, or the tunnel dies, `state.public_url` is `None`, WebApp buttons are
simply not rendered, and the bot behaves exactly as it does today. The tunnel
must never be able to break the bot.

### 2. `src/pulse_desk/miniapp_auth.py` — pure, unit-tested

Standard Telegram WebApp validation:

1. Parse `initData` as a query string; pull out `hash`.
2. Build the data-check string: remaining pairs as `k=v`, sorted by key, joined
   with a newline.
3. `secret = HMAC_SHA256(key=b"WebAppData", msg=bot_token)`.
4. Compare `HMAC_SHA256(secret, data_check_string)` against `hash` in constant
   time (`security.py` already has the primitive).
5. Reject when `auth_date` is older than 24 h.

Returns `tg_id`, `first_name`, `username`, or raises.

Role resolution then reuses the existing `bot_role` logic and the grants in
`bot_permissions.py`. A guest sees exactly the sections their access key grants,
and `delay_minutes` is honoured the same way it is in the bot.

No server-side session: `initData` travels in an `X-Telegram-Init-Data` header
on every request and is verified each time. HMAC is cheap, and statelessness
removes a whole class of expiry bugs.

### 3. `routers/miniapp.py` — page + JSON

| Endpoint | Purpose | Grant |
|---|---|---|
| `GET /app` | the Mini App shell | — (auth happens in the JSON calls) |
| `GET /api/app/home` | counters for the home cards | — |
| `GET /api/app/giveaways?sort&wins&account&page` | feed | `giveaways` |
| `GET /api/app/giveaways/{id}` | one card | `giveaways` |
| `GET /api/app/market` | rates | `market` |
| `GET /api/app/convert?amount&src&dst` | conversion | `market` |

`convert` calls the pure functions in `converter.py`, which read the newest
`market_history` snapshot — no network call on the request path.

### 4. `static/app/` — the UI

Vanilla JS, no build step, matching the rest of `static/`.

Design language taken from the reference screenshots; palette stays **Aperture**
(citron on slate), because that is this bot's brand — the reference red belongs
to a different product.

| Role | Colour |
|---|---|
| ground | `#0F1113` |
| card | `#1E2226` (`SLATE`) |
| keyline | `rgba(168,216,31,.28)` (`KEYLINE`) |
| accent | `#CDFF4A` (`CITRON`) |
| text | `#E7ECEA` |

- **Home** — a card grid: one wide card, then pairs. Each card is a line-art
  icon plus a label, with a top light gradient, an inner keyline, `scale(.98)`
  on press and `WebApp.HapticFeedback` on tap.
- **Bottom bar** — three pills (Giveaways / Rates / Menu), fixed, the active
  one lighter. This is the geometry of the second screenshot.
- **Icons** — inline SVG, stroke-only, 1.6px, `currentColor`, in a new
  `static/app/icons.js` exporting a `{name: path}` map. Silhouettes are redrawn
  from the same shapes as `scripts/generate_bot_emoji.py` so the Mini App and
  the card emoji read as one set. The existing `.webp` tiles are not reused:
  raster, wrong colour, not themeable.
- Telegram `themeParams` is read for the viewport chrome, but the page stays
  dark-first so the Aperture identity survives a light-theme client.

Screens: Home, Giveaways (feed + card, reusing `GiveawayFilter` semantics),
Rates + Converter.

### 5. Bot wiring

`keyboards.py` gains `webapp_row(label, path)`, which returns an empty list when
`state.public_url` is empty. It is inserted as the first row of
`main_menu_buttons` ("Панель") and into the giveaway card ("Открыть в панели").
Every other keyboard is left alone.

Telethon 1.43.2 lists `KeyboardButtonWebView` among its inline button types
(`telethon/tl/custom/button.py:69`), so the raw type goes straight into
`buttons=` with no wrapper.

## Error handling

- No `cloudflared` binary, tunnel process dead, or `MINIAPP_ENABLED=false` →
  no `public_url` → no WebApp buttons. The bot is unchanged.
- Invalid or expired `initData` → `401`, which the page renders as
  "открой панель заново из бота".
- A guest hitting an endpoint their key does not grant → `403`, and the section
  is not rendered in the UI in the first place.
- `converter.py` with no usable snapshot → the existing "нет данных" path.

## Testing

- `tests/test_miniapp_auth.py` — valid initData, tampered hash, expired
  `auth_date`, missing `hash`, unexpected extra fields.
- `tests/test_tunnel.py` — URL extraction from real `cloudflared` output lines,
  state transitions, missing binary.
- `node --check` on every new JS file.
- Existing suite must stay green: `.\.venv\Scripts\python.exe -m pytest -q`.

## Out of scope

Recent mentions, Summary, Analytics, Management and Keys screens in the Mini
App; a named tunnel with a custom domain; a BotFather menu button; any change to
scanning, notifications or the digest.
