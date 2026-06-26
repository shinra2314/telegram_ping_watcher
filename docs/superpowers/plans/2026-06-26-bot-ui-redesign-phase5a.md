# Bot UI Redesign — Phase 5a (Management Hub + Members/Access via Buttons) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the scattered admin grid row with one ⚙️ Управление hub, and make Люди (members) + basic Доступ (access) reachable as edit-in-place button screens, plus a "create key" button.

**Architecture:** New pure keyboards (`management_grid`, `members_list_keyboard`, `member_card_keyboard`, `member_access_keyboard`, `keys_keyboard`) and cards (`management_card`, `members_header`, `member_card`). `service.py` adds `render_management`/`render_members`/`open_member_view` wrappers and an `adm:`/`mem:`/`acc:` dispatch family (admin-gated centrally) that reuses the existing access/member DB helpers. The home grid's admin rows collapse to a single ⚙️ Управление button.

**Tech Stack:** Python 3, Telethon, `unittest`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Section 4 — management via buttons).

**Scope split:** access action *presets* (close 2h / until morning / undo / history) are Phase 5b; 5a ships close/open + the hub + members SPA + key creation. `work`/`cron` windows stay CLI.

**This is Phase 5a of 6.**

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/pulse_desk/bot/keyboards.py` | management_grid, members_list_keyboard, member_card_keyboard, member_access_keyboard, keys_keyboard. | Modify (add) |
| `src/pulse_desk/bot/cards.py` | management_card, members_header, member_card. | Modify (add) |
| `src/pulse_desk/bot/service.py` | render wrappers, adm/mem/acc/newkey dispatch, keys keyboard on menu_keys. | Modify |
| `src/pulse_desk/bot/views.py` | Collapse admin grid rows → ⚙️ Управление. | Modify |
| `tests/test_bot_keyboards.py` / `test_bot_cards.py` / `test_bot_views.py` | Tests. | Modify (add) |

---

## Task 1: Management/members keyboards

**Files:** Modify `keyboards.py`; Test `tests/test_bot_keyboards.py`.

- [ ] **Step 1: Add failing tests** (append before `if __name__`):

```python
from pulse_desk.bot.keyboards import (
    keys_keyboard, management_grid, member_access_keyboard,
    member_card_keyboard, members_list_keyboard,
)


class ManagementKeyboardTests(unittest.TestCase):
    def test_hub_has_core_sections(self):
        datas = [b.data for row in management_grid() for b in row]
        for cb in (b"st", b"menu_keys", b"adm:members", b"adm:access",
                   b"menu_scan", b"menu_logs", b"menu_restart", b"menu_main"):
            self.assertIn(cb, datas)

    def test_members_list_rows_open_member(self):
        rows = members_list_keyboard([(7, "🟢 Иван")])
        self.assertEqual(rows[0][0].data, b"mem:open:7")
        self.assertEqual(rows[-1][0].data, b"adm:home")

    def test_member_card_block_toggle(self):
        active = [b.data for row in member_card_keyboard(7, blocked=False) for b in row]
        self.assertIn(b"mem:block:7", active)
        blocked = [b.data for row in member_card_keyboard(7, blocked=True) for b in row]
        self.assertIn(b"mem:unblock:7", blocked)

    def test_member_card_has_access_and_back(self):
        datas = [b.data for row in member_card_keyboard(7, blocked=False) for b in row]
        self.assertIn(b"mem:access:7", datas)
        self.assertIn(b"adm:members", datas)

    def test_member_access_close_open(self):
        datas = [b.data for row in member_access_keyboard(7) for b in row]
        self.assertIn(b"acc:close:7", datas)
        self.assertIn(b"acc:open:7", datas)
        self.assertIn(b"mem:open:7", datas)

    def test_keys_keyboard_create_and_back(self):
        datas = [b.data for row in keys_keyboard() for b in row]
        self.assertIn(b"adm:newkey", datas)
        self.assertIn(b"adm:home", datas)
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 3: Append to `keyboards.py`:**

```python
def management_grid() -> list[list[Button]]:
    return [
        [Button.inline("⚙️ Настройки", b"st"), Button.inline("🔑 Ключи", b"menu_keys")],
        [Button.inline("👥 Люди", b"adm:members"), Button.inline("⏰ Доступ", b"adm:access")],
        [Button.inline("🔄 Скан", b"menu_scan"), Button.inline("📜 Логи", b"menu_logs")],
        [Button.inline("♻️ Рестарт", b"menu_restart"), Button.inline("⬅️ Домой", b"menu_main")],
    ]


def members_list_keyboard(items: list[tuple[int, str]]) -> list[list[Button]]:
    rows: list[list[Button]] = [
        [Button.inline(label, f"mem:open:{tg}".encode())] for tg, label in items
    ]
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"adm:members"),
    ])
    return rows


def member_card_keyboard(tg: int, blocked: bool) -> list[list[Button]]:
    toggle = (
        Button.inline("✅ Разблокировать", f"mem:unblock:{tg}".encode())
        if blocked else
        Button.inline("🚫 Заблокировать", f"mem:block:{tg}".encode())
    )
    return [
        [toggle, Button.inline("⏰ Доступ", f"mem:access:{tg}".encode())],
        [Button.inline("⬅️ Назад", b"adm:members"), Button.inline("🔄 Обновить", f"mem:open:{tg}".encode())],
    ]


def member_access_keyboard(tg: int) -> list[list[Button]]:
    return [
        [Button.inline("🔴 Закрыть", f"acc:close:{tg}".encode()), Button.inline("🟢 Открыть", f"acc:open:{tg}".encode())],
        [Button.inline("⬅️ Назад", f"mem:open:{tg}".encode()), Button.inline("🔄 Обновить", f"mem:access:{tg}".encode())],
    ]


def keys_keyboard() -> list[list[Button]]:
    return [
        [Button.inline("➕ Создать ключ", b"adm:newkey")],
        [Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"menu_keys")],
    ]
```

- [ ] **Step 4: Run — expect pass.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase5a.md
git commit -m "feat(bot): management/members/access/keys keyboards

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Management/members cards

**Files:** Modify `cards.py`; Test `tests/test_bot_cards.py`.

- [ ] **Step 1: Add failing tests** (append before `if __name__`):

```python
from pulse_desk.bot.cards import management_card, member_card, members_header


class ManagementCardTests(unittest.TestCase):
    def test_management_header(self):
        self.assertIn("**УПРАВЛЕНИЕ**", management_card())

    def test_members_header_count(self):
        self.assertIn("3", members_header(3))

    def test_members_header_empty(self):
        self.assertIn("📭", members_header(0))


class MemberCardTests(unittest.TestCase):
    MEMBER = {
        "tg_id": 7, "tg_username": "ivan", "name": "Иван",
        "key_label": "friends", "blocked": 0, "last_seen_at": "2026-06-26T13:50:00",
    }

    def test_shows_username_key_and_open_state(self):
        out = member_card(self.MEMBER, access_open=True)
        self.assertIn("@ivan", out)
        self.assertIn("friends", out)
        self.assertIn("активен", out)
        self.assertIn("открыт", out)

    def test_blocked_and_closed(self):
        out = member_card(dict(self.MEMBER, blocked=1), access_open=False)
        self.assertIn("заблокирован", out)
        self.assertIn("закрыт", out)
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 3: Append to `cards.py`:**

```python
def management_card() -> str:
    return header("⚙️", "Управление", "Домой › Управление") + "\nВыберите раздел 👇"


def members_header(count: int) -> str:
    out = header("👥", "Люди", "Домой › Управление › Люди")
    if count == 0:
        return out + "\n" + empty("Пока никого.")
    return out + f"\nУчастников: `{count}` · нажми на запись 👇"


def member_card(member: dict, access_open: bool) -> str:
    tg = member.get("tg_id")
    uname = f"@{member['tg_username']}" if member.get("tg_username") else "—"
    blocked = bool(member.get("blocked"))
    crumb = f"Домой › Управление › Люди › {member.get('name') or tg}"
    state_line = (
        f"{'🚫 заблокирован' if blocked else '🟢 активен'}  ·  "
        f"⏰ {'🟢 открыт' if access_open else '🔴 закрыт'}"
    )
    return "\n".join([
        header("👤", str(member.get("name") or tg), crumb),
        uname,
        kv("🔑", "Ключ", member.get("key_label") or "—"),
        state_line,
        kv("🕐", "Был", fmt_dt(member.get("last_seen_at"))),
    ])
```

- [ ] **Step 4: Run — expect pass.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py
git commit -m "feat(bot): management/members/member-card renderers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire the hub + members/access dispatch

**Files:** Modify `src/pulse_desk/bot/service.py`.

- [ ] **Step 1: Extend imports**

Keyboards import — add the five new builders:
```python
from .keyboards import (
    MON_FILTERS, back_home, feed_keyboard, giveaway_card_keyboard,
    giveaway_feed_keyboard, keys_keyboard, management_grid, member_access_keyboard,
    member_card_keyboard, members_list_keyboard, ping_card_keyboard, section_nav,
)
```
Cards import — add the three new renderers:
```python
from .cards import (
    feed_badge, feed_header, giveaway_card, giveaways_header, management_card,
    member_card, members_header, home_card, ping_card, summary_card,
)
```

- [ ] **Step 2: Add render wrappers** right after `open_giveaway_view`:

```python
        async def render_management():
            return management_card(), management_grid()

        async def render_members():
            members = await list_bot_members()
            items = []
            for m in members:
                tg = int(m["tg_id"])
                uname = f"@{m['tg_username']}" if m.get("tg_username") else ""
                dot_ = "🚫" if m.get("blocked") else "🟢"
                label = f"{dot_} {m.get('name') or tg} {uname}".strip()
                items.append((tg, label[:48]))
            return members_header(len(members)), members_list_keyboard(items)

        async def open_member_view(tg: int):
            member = await get_bot_member(tg)
            if not member:
                return None
            rows = await list_access_windows(tg)
            decision = resolve_access(member, [window_from_row(r) for r in rows], datetime.now(timezone.utc))
            return member_card(member, decision.allowed), member_card_keyboard(tg, bool(member.get("blocked")))
```

- [ ] **Step 3: Add the admin `adm:`/`mem:`/`acc:` dispatch** at the end of the `:`-block, after the `gw:open` branch:

```python
                if seg[0] in ("adm", "mem", "acc"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    if seg[0] == "adm" and seg[1] == "home":
                        text, kb = await render_management()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "adm" and seg[1] == "members":
                        text, kb = await render_members()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "adm" and seg[1] == "access":
                        await safe_edit(
                            event, await render_access_overview(),
                            buttons=[[Button.inline("⬅️ Управление", b"adm:home")]],
                        )
                        return
                    if seg[0] == "adm" and seg[1] == "newkey":
                        secret = generate_access_key()
                        await create_bot_key("", secret, "viewer", None)
                        link = f"https://t.me/{bot_username}?start={secret}" if bot_username else ""
                        body = "🔑 **Новый ключ**\n" + DIV + f"\n🔐 `{secret}`"
                        if link:
                            body += f"\n🔗 {link}"
                        await event.respond(body, link_preview=False)
                        await event.answer("Ключ создан")
                        return
                    if seg[0] in ("mem", "acc") and len(seg) >= 3:
                        try:
                            tg = int(seg[2])
                        except ValueError:
                            await event.answer("Некорректная команда", alert=True)
                            return
                        if seg[0] == "mem" and seg[1] == "open":
                            res = await open_member_view(tg)
                            if res is None:
                                await event.answer("Участник не найден", alert=True)
                                return
                            text, kb = res
                            await safe_edit(event, text, buttons=kb)
                            return
                        if seg[0] == "mem" and seg[1] in ("block", "unblock"):
                            await set_bot_member_blocked(tg, seg[1] == "block")
                            state.access_cache.pop(tg, None)
                            await event.answer("Заблокирован" if seg[1] == "block" else "Разблокирован")
                            res = await open_member_view(tg)
                            if res is not None:
                                text, kb = res
                                await safe_edit(event, text, buttons=kb)
                            return
                        if seg[0] == "mem" and seg[1] == "access":
                            member = await get_bot_member(tg)
                            if not member:
                                await event.answer("Участник не найден", alert=True)
                                return
                            await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
                            return
                        if seg[0] == "acc" and seg[1] in ("close", "open"):
                            member = await get_bot_member(tg)
                            if not member:
                                await event.answer("Участник не найден", alert=True)
                                return
                            if seg[1] == "close":
                                row = await create_disable_until_window(tg, None, created_by=event.sender_id)
                                await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}", None, {"until": None})
                                state.access_cache.pop(tg, None)
                                await event.answer("🔴 Доступ закрыт")
                            else:
                                cancelled = await _open_member_access(tg, event.sender_id)
                                await event.answer(f"🟢 Доступ открыт ({cancelled})")
                            member = await get_bot_member(tg)
                            await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
                            return
                    await event.answer("Неизвестная команда", alert=True)
                    return
```

- [ ] **Step 4: Give the `menu_keys` view the keys keyboard**

```python
            if data == "menu_keys":
                await safe_edit(event, await render_keys_text(), buttons=main_menu_buttons(role))
                return
```
→
```python
            if data == "menu_keys":
                await safe_edit(event, await render_keys_text(), buttons=keys_keyboard())
                return
```

- [ ] **Step 5: Verify import + compile + full tests**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py && .\.venv\Scripts\python.exe -m pytest -q`
Expected: import ok, compile ok, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): management hub + members/access via buttons

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Collapse the home admin grid → ⚙️ Управление

**Files:** Modify `views.py`; Test `tests/test_bot_views.py`.

- [ ] **Step 1: Update the admin test** — replace `test_admin_has_owner_controls` body:

```python
    def test_admin_has_owner_controls(self):
        labels = self._labels("admin")
        self.assertIn("⚙️ Управление", labels)
        self.assertNotIn("🔔 Мои уведомления", labels)
        self.assertNotIn("🔑 Ключи", labels)  # moved into the hub

    def test_admin_management_button_targets_hub(self):
        data = {b.text: b.data for row in main_menu_buttons("admin") for b in row}
        self.assertEqual(data["⚙️ Управление"], b"adm:home")
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`

- [ ] **Step 3: Collapse the admin rows in `main_menu_buttons`:**

```python
    if role == "admin":
        rows.append([
            Button.inline("🔑 Ключи", b"menu_keys"),
            Button.inline("🔄 Скан", b"menu_scan"),
            Button.inline("📜 Логи", b"menu_logs"),
        ])
        rows.append([Button.inline("⚙️ Настройки", b"st"), Button.inline("♻️ Рестарт", b"menu_restart")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
```
→
```python
    if role == "admin":
        rows.append([Button.inline("⚙️ Управление", b"adm:home")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
```

- [ ] **Step 4: Run — expect pass.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/views.py tests/test_bot_views.py
git commit -m "feat(bot): collapse admin grid row into Управление hub

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 5a Self-Review

- [ ] `import main` + full `pytest` green.
- [ ] `grep -n "adm:\|mem:\|acc:" src/pulse_desk/bot/service.py` — dispatch family present, admin-gated.
- [ ] Old `/members`, `/access`, `/keys`, `/newkey` slash commands + legacy `accshow_/accoff_/accon_/blockmember_/unblockmember_/revokekey_` callbacks still present (backward compatible).
