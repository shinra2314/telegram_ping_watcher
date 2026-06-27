# Bot UI Redesign — Phase 6 (Remove Giveaway-Join) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Remove the giveaway auto-join capability across the app, leaving detection, the board, channel-cleanup, manual analyze, and `/skip` intact.

**Investigation results (what stays vs goes):**
- `giveaway_actions.py` mixes JOIN and DETECTION. **Keep** `analyze_and_store_giveaway`, `find_giveaway_action_client`, `load_giveaway_message` (used by manual analyze + channel cleanup). **Remove** `confirm_safe_giveaway_join`, `_find_safe_join_button`, `_button_is_safe_join`.
- The web UI has **no** confirm/join button (`app-main.js` calls only `/analyze`, `/skip`, `/refresh-deadline`, `/actions`). So removing `POST /api/giveaways/{id}/confirm` breaks nothing on the frontend. `/skip` is used by the web → **keep**.
- Join was reachable only via: the bot `gconfirm` button (push + legacy callback) and the `/confirm` endpoint. Both removed here.
- `DRY_RUN_GIVEAWAYS` is referenced only by the (removed) join confirm + settings plumbing/frontend → becomes dead, removed in Tasks 3–4.

**Tech Stack:** Python 3, FastAPI, Telethon, vanilla JS, `unittest`/pytest, `node --check`, ruff (CI gate — no unused imports allowed).

**Spec:** Section "Phase 6 — Remove giveaway-join". **Final phase of the bot UI redesign.**

---

## Task 1: Remove join from the bot (service.py + bot_notify.py)

**Files:** `src/pulse_desk/bot/service.py`, `src/pulse_desk/bot_notify.py`.

- [ ] **Step 1: bot_notify.py — drop the gconfirm/gskip button block.** Remove:
```python
        if ping_id and record.get("is_giveaway"):
            buttons.append([
                Button.inline("✅ Участвовать", data=f"gconfirm_{ping_id}"),
                Button.inline("⏭ Пропустить", data=f"gskip_{ping_id}"),
            ])
```

- [ ] **Step 2: service.py — drop the gconfirm/gskip callback branches.** Replace:
```python
            ping_id = _cb_id(data)
            if data.startswith(("fav_", "read_", "gconfirm_", "gskip_")) and ping_id is None:
                await event.answer("Некорректная команда", alert=True)
                return
            if data.startswith("fav_"):
                await toggle_favorite(ping_id)
                await event.answer("Избранное обновлено")
            elif data.startswith("read_"):
                await mark_ping_read_db(ping_id)
                await event.answer("Отмечено как прочитанное")
                await event.delete()
            elif data.startswith("gconfirm_"):
                try:
                    result = await confirm_safe_giveaway_join(ping_id, actor="telegram_bot")
                    await event.answer(result.get("message") or "Joined")
                    await finalize_buttons(event, "✅ Участвую")
                except HTTPException as exc:
                    await event.answer(str(exc.detail), alert=True)
            elif data.startswith("gskip_"):
                await update_giveaway_candidate_status(ping_id, "skipped")
                await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
                await record_giveaway_action(ping_id, "skip", "skipped", "telegram_bot")
                await event.answer("Skipped")
                await event.delete()
```
with:
```python
            ping_id = _cb_id(data)
            if data.startswith(("fav_", "read_")) and ping_id is None:
                await event.answer("Некорректная команда", alert=True)
                return
            if data.startswith("fav_"):
                await toggle_favorite(ping_id)
                await event.answer("Избранное обновлено")
            elif data.startswith("read_"):
                await mark_ping_read_db(ping_id)
                await event.answer("Отмечено как прочитанное")
                await event.delete()
            elif data.startswith(("gconfirm_", "gskip_")):
                await event.answer("Действие розыгрышей больше недоступно.", alert=True)
```

- [ ] **Step 3: service.py — remove the now-dead `finalize_buttons` closure** (lines ~232–251):
```python
        async def finalize_buttons(event, done_label: str) -> None:
            """Replace the message keyboard with kept URL buttons + a done marker."""
            try:
                msg = await event.get_message()
                kept = [Button.url(b.text, b.url) for row in (msg.buttons or []) for b in row if b.url]
                rows: list[list[Button]] = []
                if kept:
                    rows.append(kept)
                rows.append([Button.inline(done_label, b"noop")])
                await safe_edit(event, buttons=rows)
            except Exception:
                logger.debug("Could not finalize buttons", exc_info=True)
```
Delete the whole closure (it was only used by `gconfirm`).

- [ ] **Step 4: service.py — remove now-unused imports** (ruff F401 would fail CI):
  - Top: delete `from fastapi import HTTPException` (line 10) and `from ..giveaway_actions import confirm_safe_giveaway_join` (line 37).
  - In the `from database import (...)` block: delete `record_giveaway_action,`, `update_giveaway_candidate_status,`, `update_ping_meta,` (all three were only used by gskip).

- [ ] **Step 5: Verify** `import main`, `py_compile`, `ruff check src/pulse_desk/bot/`, `pytest`.

Run:
```
.\.venv\Scripts\python.exe -c "import main"
.\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py src/pulse_desk/bot_notify.py
.\.venv\Scripts\python.exe -m ruff check src/pulse_desk/bot/service.py src/pulse_desk/bot_notify.py
.\.venv\Scripts\python.exe -m pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/pulse_desk/bot/service.py src/pulse_desk/bot_notify.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase6.md
git commit -m "feat(bot): remove giveaway-join buttons from the bot

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Remove join from the backend (giveaway_actions.py + routers)

**Files:** `src/pulse_desk/giveaway_actions.py`, `routers/giveaways.py`.

- [ ] **Step 1: giveaway_actions.py — delete the three join functions:** `_button_is_safe_join`, `_find_safe_join_button`, and `confirm_safe_giveaway_join`. Keep `find_giveaway_action_client`, `analyze_and_store_giveaway`, `load_giveaway_message`.

- [ ] **Step 2: giveaway_actions.py — drop now-unused imports.** After removing the join funcs, check and remove any of these that are no longer referenced: `datetime` (used only by confirm), `HTTPException` (still used by `load_giveaway_message` → KEEP), `FloodWaitError` (used only by confirm → remove), `types` (used only by the button helpers → remove), `TelegramClient` (still used in signatures → keep). Verify with `ruff check`.

- [ ] **Step 3: routers/giveaways.py — remove the confirm endpoint + import.** Delete:
```python
@router.post("/api/giveaways/{ping_id}/confirm", dependencies=[Depends(require_admin)])
async def confirm_giveaway_api(ping_id: int):
    return await confirm_safe_giveaway_join(ping_id, actor="admin")
```
and remove `confirm_safe_giveaway_join,` from the `from pulse_desk.giveaway_actions import (...)` block.

- [ ] **Step 4: Verify** `import main`, `ruff check src/pulse_desk/giveaway_actions.py routers/giveaways.py`, `pytest`.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/giveaway_actions.py routers/giveaways.py
git commit -m "feat(giveaways): remove safe-join action + /confirm endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Remove the dead `DRY_RUN_GIVEAWAYS` backend plumbing

**Files:** `config.py`, `app_ctx.py`, `watch_settings.py`, `api_models.py`, `routers/system.py`, `routers/settings.py`, `dashboard.py`.

- [ ] **Step 1:** `src/pulse_desk/config.py` — delete the field:
```python
    dry_run_giveaways: bool = Field(default=True, alias="DRY_RUN_GIVEAWAYS")
```

- [ ] **Step 2:** `src/pulse_desk/app_ctx.py` — delete `DRY_RUN_GIVEAWAYS = settings.dry_run_giveaways` (line 44).

- [ ] **Step 3:** `src/pulse_desk/watch_settings.py` — delete the four references: the module global `DRY_RUN_GIVEAWAYS = app_ctx.DRY_RUN_GIVEAWAYS` (line 34); the `"dry_run_giveaways": DRY_RUN_GIVEAWAYS,` dict entry (line 270); the `"dry_run_giveaways": _as_bool(...)` parse entry (line 296); the name from the `global DRY_RUN_GIVEAWAYS, GIVEAWAY_ACTION_ACCOUNT, GIVEAWAY_REVIEW_MODE` statement (line 312) and the `DRY_RUN_GIVEAWAYS = cleaned["dry_run_giveaways"]` assignment (line 327). Keep `GIVEAWAY_ACTION_ACCOUNT`/`GIVEAWAY_REVIEW_MODE`.

- [ ] **Step 4:** `src/pulse_desk/api_models.py` — delete the `dry_run_giveaways: bool = True` field (line 74).

- [ ] **Step 5:** `routers/system.py` — delete the `"dry_run_giveaways": ws.DRY_RUN_GIVEAWAYS,` line (159) and the `"dry_run": ws.DRY_RUN_GIVEAWAYS,` diagnostics line (225).

- [ ] **Step 6:** `routers/settings.py` — delete the `"dry_run_giveaways": ws.DRY_RUN_GIVEAWAYS,` line (32).

- [ ] **Step 7:** `src/pulse_desk/dashboard.py` — remove the safety indicator that reads `dry_run_giveaways` (lines ~146–148). Read the surrounding block first; drop the whole indicator dict that depends on the removed key (do not leave a dangling reference).

- [ ] **Step 8: Verify** `import main`, `ruff check`, `pytest` (expect `tests/test_core.py` failures on the settings dict — fixed in Task 4).

- [ ] **Step 9: Commit** (after Task 4 if tests must stay green; otherwise commit backend + tests together — see Task 4).

---

## Task 4: Remove `DRY_RUN_GIVEAWAYS` from frontend, tests, docs

**Files:** `static/index.html`, `static/js/app-settings.js`, `static/js/diagnostics.js`, `tests/test_core.py`, `.env.example`, `CLAUDE.md`.

- [ ] **Step 1:** `static/index.html` — delete the dry-run toggle row (line 553):
```html
              <label class="toggle-row settings-toggle"><input type="checkbox" id="runtime-dry-run"> <span>Dry-run без клика в Telegram</span></label>
```

- [ ] **Step 2:** `static/js/app-settings.js` — delete the read (line 103) `$("runtime-dry-run").checked = values.dry_run_giveaways !== false;` and the write (line 118) `dry_run_giveaways: $("runtime-dry-run").checked,`.

- [ ] **Step 3:** `static/js/diagnostics.js` — remove the `dry run: ...` display line (85).

- [ ] **Step 4:** `tests/test_core.py` — remove the two `"dry_run_giveaways": True,` entries (lines 80, 125) from the expected settings dicts.

- [ ] **Step 5:** `.env.example` — delete `DRY_RUN_GIVEAWAYS=true` (line 38). `CLAUDE.md` — delete the dry-run bullet (line 186).

- [ ] **Step 6: Verify** all gates:
```
.\.venv\Scripts\python.exe -c "import main"
.\.venv\Scripts\python.exe -m ruff check src/ routers/
.\.venv\Scripts\python.exe -m pytest -q
node --check static/js/app-settings.js
node --check static/js/diagnostics.js
```

- [ ] **Step 7:** Bump `CACHE_NAME` in `static/sw.js` (shell assets changed — index.html/app-settings.js).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore: drop dead DRY_RUN_GIVEAWAYS flag (config/web/tests/docs)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 6 Self-Review

- [ ] `grep -rn "confirm_safe_giveaway_join\|gconfirm\|gskip\|DRY_RUN_GIVEAWAYS\|dry_run_giveaways" src/ routers/ static/ tests/` returns nothing (docs/plans may still mention it).
- [ ] Detection intact: `analyze_and_store_giveaway`, `get_giveaway_board`, candidates table untouched; `/api/giveaways/{id}/analyze`, `/skip`, `/refresh-deadline`, cleanup endpoints still present.
- [ ] `import main` + `ruff check src/ routers/` + full `pytest` green; `node --check` on changed JS green.
