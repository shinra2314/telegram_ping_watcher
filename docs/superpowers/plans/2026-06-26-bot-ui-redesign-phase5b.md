# Bot UI Redesign — Phase 5b (Access Presets) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.

**Goal:** Extend the member access card with button presets: close 2h, close until 08:00, undo, history — reusing the exact logic already in `/access`.

**Architecture:** `member_access_keyboard` grows three preset buttons + undo + history; the `acc:` dispatch branch handles `close2h`/`morning`/`undo`/`log` alongside `close`/`open`, calling the same DB helpers the `/access` slash uses. History renders inline (plain text + DIV), back to the access card.

**Spec:** Section 4. **This is Phase 5b of 6.** `work`/`cron` windows stay CLI.

---

## Task 1: Extend `member_access_keyboard`

**Files:** Modify `keyboards.py`; Test `tests/test_bot_keyboards.py`.

- [ ] **Step 1: Update the test** `test_member_access_close_open` to also assert the presets:

```python
    def test_member_access_close_open(self):
        datas = [b.data for row in member_access_keyboard(7) for b in row]
        for cb in (b"acc:close:7", b"acc:close2h:7", b"acc:open:7",
                   b"acc:morning:7", b"acc:undo:7", b"acc:log:7",
                   b"mem:open:7", b"mem:access:7"):
            self.assertIn(cb, datas)
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 3: Replace `member_access_keyboard` in `keyboards.py`:**

```python
def member_access_keyboard(tg: int) -> list[list[Button]]:
    return [
        [Button.inline("🔴 Закрыть", f"acc:close:{tg}".encode()),
         Button.inline("🔴 На 2ч", f"acc:close2h:{tg}".encode()),
         Button.inline("🟢 Открыть", f"acc:open:{tg}".encode())],
        [Button.inline("🔴 До утра", f"acc:morning:{tg}".encode()),
         Button.inline("↩️ Отмена", f"acc:undo:{tg}".encode()),
         Button.inline("🧾 История", f"acc:log:{tg}".encode())],
        [Button.inline("⬅️ Назад", f"mem:open:{tg}".encode()),
         Button.inline("🔄 Обновить", f"mem:access:{tg}".encode())],
    ]
```

- [ ] **Step 4: Run — expect pass.**

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase5b.md
git commit -m "feat(bot): access preset buttons on the member access card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Handle the new `acc:` actions

**Files:** Modify `src/pulse_desk/bot/service.py`.

- [ ] **Step 1: Replace the `acc` dispatch branch** (currently handles only `close`/`open`) with the full preset set:

```python
                        if seg[0] == "acc" and seg[1] in ("close", "open", "close2h", "morning", "undo", "log"):
                            member = await get_bot_member(tg)
                            if not member:
                                await event.answer("Участник не найден", alert=True)
                                return
                            if seg[1] == "log":
                                log = await get_access_audit(tg)
                                lines = ["🧾 **История доступа**", DIV]
                                if not log:
                                    lines.append("📭 __Пусто.__")
                                else:
                                    lines += [f"`{fmt_dt(a['created_at'])}` · {a['action']} · _{a['actor']}_" for a in log]
                                await safe_edit(
                                    event, "\n".join(lines),
                                    buttons=[[Button.inline("⬅️ Назад", f"mem:access:{tg}".encode())]],
                                )
                                return
                            if seg[1] == "open":
                                cancelled = await _open_member_access(tg, event.sender_id)
                                await event.answer(f"🟢 Доступ открыт ({cancelled})")
                            elif seg[1] == "undo":
                                target = find_undoable(await get_access_audit(tg, limit=50))
                                if not target:
                                    await event.answer("Нечего отменять", alert=True)
                                else:
                                    plan = plan_undo(target)
                                    await _apply_undo(tg, plan)
                                    await record_access_audit(
                                        tg, target.get("schedule_id"), "undo", f"admin:{event.sender_id}",
                                        None, {"undone_audit_id": int(target["id"]), "plan": plan},
                                    )
                                    state.access_cache.pop(tg, None)
                                    await event.answer("↩️ Отменено")
                            else:
                                until_iso = None
                                note = "🔴 Доступ закрыт"
                                if seg[1] == "close2h":
                                    until = datetime.now(timezone.utc) + timedelta(hours=2)
                                    until_iso = until.replace(microsecond=0, tzinfo=None).isoformat()
                                    note = "🔴 Закрыт на 2ч"
                                elif seg[1] == "morning":
                                    target_local = next_hhmm_datetime(datetime.now().astimezone(), "08:00")
                                    if target_local:
                                        until_iso = target_local.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
                                    note = "🔴 Закрыт до 08:00"
                                row = await create_disable_until_window(tg, until_iso, created_by=event.sender_id)
                                await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}", None, {"until": until_iso})
                                state.access_cache.pop(tg, None)
                                await event.answer(note)
                            member = await get_bot_member(tg)
                            await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
                            return
```

(Replace the previous `if seg[0] == "acc" and seg[1] in ("close", "open"):` block in its entirety.)

- [ ] **Step 2: Verify import + compile + full tests**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py && .\.venv\Scripts\python.exe -m pytest -q`

- [ ] **Step 3: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): access presets (2h/morning/undo/history) via buttons

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 5b Self-Review

- [ ] `import main` + full `pytest` green.
- [ ] `next_hhmm_datetime`, `find_undoable`, `plan_undo`, `_apply_undo`,
      `get_access_audit`, `create_disable_until_window` all already in scope (no new imports).
