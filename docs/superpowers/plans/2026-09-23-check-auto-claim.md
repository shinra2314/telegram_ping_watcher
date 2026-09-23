# Check auto-claim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every account presses xRocket / CryptoBot checks it sees, never the ones our own accounts posted, and hands the owner only what needs a human (captcha).

**Architecture:** pure rules in `check_claims.py`, I/O in `check_claimer.py` hooked into each account's live handlers before the shared dedupe, journal table `check_claims` (schema 25), owner screen `bot/sections/checks.py`. Spec: `docs/superpowers/specs/2026-09-23-check-auto-claim-design.md`.

**Tech Stack:** Python 3.12, Telethon (`messages.startBot`, `channels.joinChannel`, `messages.importChatInvite`, `Message.click`), aiosqlite, pytest (`asyncio_mode = auto`).

Execution: inline in the session that wrote it (subagents were not requested).

---

## File map

| File | Responsibility |
|---|---|
| Create `src/pulse_desk/check_claims.py` | pure: `find_check`, `message_links`, `parse_amount`, `addressee`, `post_password`, `own_sender`, `classify_reply`, `join_targets`, `recheck_button`, `normalize_config`, `is_fresh`, amount totals |
| Create `src/pulse_desk/check_claimer.py` | `on_message`, `claim`, reply polling, subscribe/password steps, relay (`relay_press`, `relay_text`, `relay_retry`, `relay_next`), `warm_up`, `note_dialog`, `persist_admin_chats`, `load`, `save`, `sweep_relays` |
| Create `database/check_claims.py` | `record_check_claim`, `update_check_claim`, `get_recent_check_claims`, `get_check_claim_stats` |
| Create `src/pulse_desk/bot/sections/checks.py` | `ck`, `ck:m:*`, `ck:a:*`, `ck:b|t|r|n:<token>`; prompt `ck_reply` |
| Modify `database/_core.py`, `database/schema.py`, `database/__init__.py`, `database/maintenance.py` | schema 25, table, exports, 90-day trim |
| Modify `src/pulse_desk/runtime.py` | state fields |
| Modify `src/pulse_desk/telegram_accounts.py` | call `on_message` in both live handlers; warm-up after connect |
| Modify `src/pulse_desk/scan_engine.py` | `note_dialog` per dialog, persist after listing |
| Modify `main.py`, `src/pulse_desk/loops.py` | load config at startup; janitor sweeps relays |
| Modify `src/pulse_desk/bot/sections/__init__.py`, `bot/sections/settings.py` | register section; «🧾 Чеки» in ⚙️ |
| Tests `tests/test_check_claims.py`, `tests/test_check_claimer.py`, `tests/test_bot_routes.py` | rules, claim path with fakes, routes |
| Docs `CLAUDE.md` | module + section + table + the reversal of «чеки removed» |

## Tasks

- [ ] **1. Rules (TDD).** Tests first in `tests/test_check_claims.py`: URL button «Получить 0.1 USDT» → `t.me/xrocket?start=mc_x` found with amount `0.1 USDT`, addressee from «для @MCshinra»; hidden text link to `t.me/send?start=CQabc` found; `IVabc` and `inv_abc` ignored; referral link in chatter (no check word, no claim label) ignored; `own_sender` for out / own user / own admin chat / stranger; `classify_reply` RU+EN table incl. «уже активирован» → gone (not claimed); `post_password`; `join_targets` skips bots, dedupes, caps at 3; `normalize_config` defaults to claim. Run `pytest tests/test_check_claims.py` → fail, implement, → pass.
- [ ] **2. Journal.** Table + `SCHEMA_VERSION = 25`; DB functions; maintenance trim. Test upsert keeps one row per (session, bot, code) and stats sum claimed amounts per currency.
- [ ] **3. Claimer (TDD with fakes).** Fake client records `StartBotRequest`, serves scripted bot replies. Tests: eligible → one startBot per account; personal → only the addressee's client, even when another account received it; own sender / out / admin chat → no startBot, code remembered; watch → none; repeated/edited message → one; stale → none; reply «Вы получили» → journal `claimed` + owner notified; subscribe reply → join + recheck click → claimed; captcha → relay registered, `relay_press` clicks mirrored (row, col).
- [ ] **4. Wiring.** State fields, live handlers, warm-up, dialog harvest, startup load, janitor.
- [ ] **5. Section.** Screen, toggles, relay buttons, prompt; ⚙️ button; `tests/test_bot_routes.py` admin routes `ck`, `ck:m:watch`, `ck:a:0`, `ck:b:ab12cd34:0:0`, `ck:t:ab12cd34`, `ck:r:ab12cd34`, `ck:n:ab12cd34`.
- [ ] **6. Verify + ship.** `python -m pytest` all green, `python -c "import main"`, commit (only this feature's files/hunks — the tree holds someone else's WIP in `database/__init__.py` and others), CLAUDE.md, restart via `restart_app.ps1`, check `logs/app.log` for the accounts' warm-up and no tracebacks.
