# Bot Redesign — Visual + UX Overhaul (Aperture)

**Date:** 2026-06-30
**Scope:** Telegram bot only (`src/pulse_desk/bot/*`, `bot_notify.py`, asset
generators). No web, no scan engine, no business-logic changes.
**Goal:** Improve the bot's look and feel — on-brand stickers at key moments,
header images on key screens, richer cards — without breaking the existing,
unit-tested navigation or any monitoring behaviour.

## Design decisions (approved 2026-06-30)

- **Nav/image model: Option A.** Home (`/start`, `/menu`) is a photo card
  (banner + caption). Section navigation stays fast text edits (callback
  `safe_edit`). Events fire on-brand **stickers** as standalone messages.
  Rejected Option B (every screen a media message, caption-edit nav) because the
  1024-char caption cap breaks logs/long lists and the user declined it.
- **Stickers: custom, Aperture style.** Generated locally with Pillow, reusing
  the existing `generate_bot_assets.py` primitives. Not a public Telegram pack.
- **Win sticker → admin only** (not broadcast to viewer members) to avoid
  spamming friends. Same gating as existing notifications (global enable +
  quiet hours).
- **Functionality: visual + UX only.** No new business features, no `.tgs`
  animated stickers, no media-nav of every screen.

## Current state (what exists)

- `src/pulse_desk/bot/` package: `service.py` (handlers, 1636 lines),
  `views.py` (DIV, fmt_dt, menus, help), `chrome.py` (header/kv/dot/empty),
  `cards.py` (pure card renderers), `keyboards.py` (inline keyboards).
- `bot_notify.py`: outbound notifications. `notification_image_path()` already
  picks a header PNG per ping type (`notify_win/giveaway/mention`).
- `assets/bot/`: `avatar.png`, `description.png`, `welcome.png`,
  `notify_{mention,giveaway,win}.png`. Built by `scripts/generate_bot_assets.py`
  (Aperture: graphite body, oscilloscope grid, focus reticle, scan line).
- Unit tests: `test_bot_cards`, `test_bot_chrome`, `test_bot_keyboards`,
  `test_bot_views`, `test_bot_prefs`, `test_bot_member_block`. The pure visual
  layer is tested and MUST stay green.

### Latent bug to fix in this pass
`bot_notify.notification_image_path()` returns `notify_check.png` for checks,
but `generate_bot_assets.py` never generates it — checks get no header image
today. The new banner generation fixes this.

## Components

### 1. Sticker assets — `scripts/generate_bot_stickers.py` (new)
- Imports the Aperture primitives from `generate_bot_assets.py` (reticle,
  scan_line, frame_brackets, add_grid, tracked_text, palette). No duplication.
- Output: `assets/bot/stickers/*.webp`, 512×512, **transparent** background
  (sticker convention) — a per-event glyph + a small reticle/lock motif in the
  event's signal colour, plus a mono unit-code label.
- Set (11): `win` (amber), `giveaway` (citron), `check` (citron), `scan`
  (citron sweep), `scan_done` (citron ✓lock), `access_on` (citron), `access_off`
  (red), `welcome` (citron), `empty` (dim), `error` (amber/red), `pong`
  (citron node).
- Saved as `.webp` with alpha (`Image.save(..., "WEBP", lossless=True)`).
- Importable without running (CI does `import`/`py_compile`); actual render is
  manual on Windows (needs `C:\Windows\Fonts`), like the existing generator.

### 2. Header banners — extend `scripts/generate_bot_assets.py`
- Add `_notify("notify_check.png", CITRON, "ЧЕК", "PD·01 // SIGNAL LOCK · CHECK")`
  (fixes the latent bug) and `notify_deadline.png` (amber/cyan, "ДЕДЛАЙН").
- Keep existing six outputs unchanged.

### 3. Sticker delivery — `src/pulse_desk/bot/stickers.py` (new)
- `STICKERS: dict[str, str]` name → absolute path under `assets/bot/stickers/`.
- `sticker_path(name) -> Optional[str]`: path if the file exists, else None.
- `async def send_sticker(client, peer, name) -> bool`: send the `.webp` via
  Telethon `send_file(..., attributes=[DocumentAttributeSticker(alt, InputStickerSetEmpty())], force_document=False)`
  so it renders as a sticker; on any error fall back to a plain photo send; on
  total failure log + return False (never raises into a handler).
- A module-level `STICKERS_ENABLED` flag, read from config at init.
- Pure path/registry logic (`sticker_path`, name validation) is unit-testable
  without Telethon.

### 4. Event hooks (additive — no existing branch removed)
- **`ping_pipeline` / `bot_notify.send_bot_notification`**: when `record.is_win`,
  send `win` sticker to the **admin** before the card. When `is_giveaway` AND
  `priority_label in {critical, high}`, send `giveaway` sticker to admin.
  Gated by the same `settings.enabled` + `is_quiet_time` checks already in
  `send_bot_notification`, plus `STICKERS_ENABLED`.
- **`check` notifications** (`send_check_notification`): lead with `check`
  sticker to the configured target (admin-equiv), same gating.
- **`/scan` command + `menu_scan` callback**: reply with `scan` sticker.
- **`grant_access`**: lead the welcome message with the `welcome` sticker.
- **`/ping`**: reply with `pong` sticker.
- **`/giveaways` command when empty**: lead with `empty` sticker (command path
  sends a fresh message; button-nav empty stays text — can't add media on edit).
- **`safe()` handler wrapper error path**: optionally send `error` sticker on
  hard failures (low priority; keep the existing text alert as primary).

### 5. Home hero card
- `/start` and `/menu` send the home **banner image** + upgraded caption
  (caption stays < 1024). Today `/start` already sends `welcome.png`; `/menu`
  becomes consistent with it.
- `home_card()` (in `cards.py`) gains:
  - accounts signal bar via new `chrome.bar(online, total)` → `▰▰▰▱▱ 3/5`
  - 🔥 marker when `urgent > 0`
  - unchanged fields otherwise; output still a single short block.
- Admin home keyboard gains a `🔄 Скан` quick button (new callback reuses the
  existing `menu_scan` branch — no new logic).

### 6. Card / chrome polish (text layer, under tests)
- `chrome.bar(value, total, width=5) -> str`: unicode progress bar `▰`/`▱`,
  clamps, handles total=0.
- `chrome.chip(label) -> str`: a uniform tag pill (e.g. `「label」` or
  `· label ·`) used for status tags so cards read consistently.
- `cards.ping_card` / `cards.giveaway_card`: status tags rendered via `chip`;
  giveaway deadline shows a countdown bar when a deadline exists.
- Update `test_bot_chrome` (new `bar`/`chip`) and `test_bot_cards` (new tag
  formatting) so the suite stays green. Tests are the contract — adjust expected
  strings deliberately, don't loosen assertions.

### 7. Config / docs
- `config.py`: add `bot_stickers_enabled: bool = True` (env
  `BOT_STICKERS_ENABLED`).
- `.env.example`: document it.
- `CLAUDE.md`: note `assets/bot/stickers/`, the new generator script, and the
  `bot/stickers.py` module in the architecture map; mention the env var.
- Regenerate all assets locally and commit the new PNG/WebP files.

## Data flow (sticker on win)

1. Scan → `process_ping_message` classifies a win.
2. `send_bot_notification(record, ping_id)` runs its existing gating.
3. If enabled + not quiet + `STICKERS_ENABLED` + `record.is_win`:
   `send_sticker(bot_client, ADMIN_ID, "win")` (best-effort), then the existing
   header-image card send proceeds unchanged.
4. Viewer-member broadcast path is untouched (no sticker) → friends not spammed.

## Error handling

- `send_sticker` never raises into a handler: sticker fail → photo fallback →
  log + return False. A missing `.webp` resolves to `sticker_path()==None` and
  the call is skipped.
- All sticker sends are fire-and-forget around the existing flow; a sticker
  failure never blocks the card/notification that follows.

## Testing

- New `tests/test_bot_stickers.py`: registry resolves known names; unknown name
  → None; `sticker_path` returns None when the file is absent;
  `STICKERS_ENABLED=False` short-circuits (no send attempted) — using a fake
  client/monkeypatch, no real Telethon.
- Extend `test_bot_chrome` (`bar`, `chip`) and `test_bot_cards` (tags/countdown).
- Generators validated by `py_compile` + `import` in CI (no font render in CI).
- Manual: run both generator scripts on Windows, eyeball the assets, run the
  bot, observe stickers on win/scan/ping/access and the home banner.

## Out of scope

- Animated `.tgs` stickers.
- Media-backed navigation for every screen (Option B).
- Any new monitoring/business feature.
- Web dashboard changes.

## File touch list

- New: `scripts/generate_bot_stickers.py`, `src/pulse_desk/bot/stickers.py`,
  `tests/test_bot_stickers.py`, `assets/bot/stickers/*.webp`,
  `assets/bot/notify_check.png`, `assets/bot/notify_deadline.png`.
- Edit: `scripts/generate_bot_assets.py`, `src/pulse_desk/bot/chrome.py`,
  `src/pulse_desk/bot/cards.py`, `src/pulse_desk/bot/views.py` (home keyboard),
  `src/pulse_desk/bot/service.py` (event hooks), `src/pulse_desk/bot_notify.py`
  (sticker hooks), `src/pulse_desk/config.py`, `.env.example`, `CLAUDE.md`,
  `tests/test_bot_chrome.py`, `tests/test_bot_cards.py`.
