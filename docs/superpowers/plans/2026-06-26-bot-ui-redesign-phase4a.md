# Bot UI Redesign — Phase 4a (Clickable Monitoring Feed + Ping Drilldown) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the dead-text Recent/Checks views into a clickable monitoring feed — each ping is a button-row, with filter chips and a drilldown ping card (full text + ⭐/read for the owner).

**Architecture:** A structured `:`-prefixed callback scheme arrives for the first time (`mon:feed:<filter>`, `mon:open:<id>`, `ping:fav|read:<id>`). Legacy colon-free callbacks (`fav_`, `read_`, `menu_*`) are untouched and never collide. Pure keyboard builders (`feed_keyboard`, `ping_card_keyboard`) and pure card renderers (`feed_header`, `ping_card`, `feed_badge`) are unit-tested; `service.py` adds thin async fetch wrappers (`render_feed`, `open_ping_view`) and a small dispatch block. The home grid's "🕐 Последние"/"💸 Чеки" buttons and the `/recent`,`/latest`,`/checks` slash commands repoint to the feed.

**Tech Stack:** Python 3, Telethon, `unittest`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Section 3 — clickable lists & drilldown).

**Deviations from spec (intentional):** filter set is `Все/Важные/Чеки/Победы` (all map to `get_pings(chat_type=...)`; the spec's "Упоминания" had no clean data backing). The inline "🔎 Поиск" button is deferred — `/search` stays. Giveaways drilldown is Phase 4b.

**This is Phase 4a of 6.**

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/pulse_desk/bot/keyboards.py` | `feed_keyboard`, `ping_card_keyboard`, `MON_FILTERS`. | Modify (add) |
| `src/pulse_desk/bot/cards.py` | `feed_badge`, `feed_header`, `ping_card`. | Modify (add) |
| `src/pulse_desk/bot/service.py` | `render_feed`/`open_ping_view`, `:`-dispatch block, grid/slash repoint. | Modify |
| `src/pulse_desk/bot/views.py` | Repoint Последние/Чеки grid buttons to `mon:feed:*`. | Modify |
| `tests/test_bot_keyboards.py` | Tests for the new keyboards. | Modify (add) |
| `tests/test_bot_cards.py` | Tests for the new cards. | Modify (add) |
| `tests/test_bot_views.py` | Assert grid buttons target the feed. | Modify (add) |

---

## Task 1: Feed + ping-card keyboards

**Files:**
- Modify: `src/pulse_desk/bot/keyboards.py`
- Test: `tests/test_bot_keyboards.py`

- [ ] **Step 1: Add failing tests** to `tests/test_bot_keyboards.py` (append before `if __name__`):

```python
from pulse_desk.bot.keyboards import MON_FILTERS, feed_keyboard, ping_card_keyboard


class FeedKeyboardTests(unittest.TestCase):
    ITEMS = [(842, "🔥 14:02 @chan"), (840, "• 13:40 @chan2")]

    def test_each_item_is_a_row_opening_that_ping(self):
        rows = feed_keyboard(self.ITEMS, "all")
        self.assertEqual(rows[0][0].data, b"mon:open:842")
        self.assertEqual(rows[1][0].data, b"mon:open:840")

    def test_filter_row_has_all_filters(self):
        rows = feed_keyboard(self.ITEMS, "all")
        filt = rows[len(self.ITEMS)]
        datas = [b.data for b in filt]
        self.assertIn(b"mon:feed:all", datas)
        self.assertIn(b"mon:feed:check", datas)
        self.assertIn(b"mon:feed:win", datas)
        self.assertIn(b"mon:feed:important", datas)

    def test_active_filter_is_marked(self):
        rows = feed_keyboard(self.ITEMS, "check")
        filt = rows[len(self.ITEMS)]
        active = [b.text for b in filt if b.data == b"mon:feed:check"][0]
        inactive = [b.text for b in filt if b.data == b"mon:feed:all"][0]
        self.assertNotEqual(active, "Чеки")
        self.assertEqual(inactive, "Все")

    def test_footer_home_and_refresh_keep_filter(self):
        rows = feed_keyboard(self.ITEMS, "win")
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, b"mon:feed:win")

    def test_empty_feed_still_has_filter_and_footer(self):
        rows = feed_keyboard([], "all")
        self.assertEqual(len(rows), 2)  # filter row + footer


class PingCardKeyboardTests(unittest.TestCase):
    def test_admin_gets_fav_and_read(self):
        rows = ping_card_keyboard(842, is_admin=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"ping:fav:842", datas)
        self.assertIn(b"ping:read:842", datas)

    def test_viewer_has_no_mutating_actions(self):
        rows = ping_card_keyboard(842, is_admin=False)
        datas = [b.data for row in rows for b in row]
        self.assertNotIn(b"ping:fav:842", datas)
        self.assertNotIn(b"ping:read:842", datas)

    def test_back_returns_to_feed_and_refresh_reopens(self):
        rows = ping_card_keyboard(842, is_admin=False)
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"mon:feed:all")
        self.assertEqual(footer[1].data, b"mon:open:842")
```

- [ ] **Step 2: Run — expect fail** (`ImportError: cannot import name 'MON_FILTERS'`).

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 3: Append to `src/pulse_desk/bot/keyboards.py`:**

```python
MON_FILTERS = [("all", "Все"), ("important", "Важные"), ("check", "Чеки"), ("win", "Победы")]


def feed_keyboard(items: list[tuple[int, str]], active: str) -> list[list[Button]]:
    """Monitoring feed: one row per ping, a filter row, then home/refresh."""
    rows: list[list[Button]] = [
        [Button.inline(label, f"mon:open:{pid}".encode())] for pid, label in items
    ]
    filt = [
        Button.inline(f"▸{lbl}" if code == active else lbl, f"mon:feed:{code}".encode())
        for code, lbl in MON_FILTERS
    ]
    rows.append(filt)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", f"mon:feed:{active}".encode()),
    ])
    return rows


def ping_card_keyboard(ping_id: int, is_admin: bool) -> list[list[Button]]:
    """Drilldown actions: owner gets ⭐/read; everyone gets back/refresh."""
    rows: list[list[Button]] = []
    if is_admin:
        rows.append([
            Button.inline("⭐ В избранное", f"ping:fav:{ping_id}".encode()),
            Button.inline("✓ Прочитано", f"ping:read:{ping_id}".encode()),
        ])
    rows.append([
        Button.inline("⬅️ Назад", b"mon:feed:all"),
        Button.inline("🔄 Обновить", f"mon:open:{ping_id}".encode()),
    ])
    return rows
```

- [ ] **Step 4: Run — expect pass.**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase4a.md
git commit -m "feat(bot): feed + ping-card keyboards (mon:/ping: scheme)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Feed + ping-card renderers

**Files:**
- Modify: `src/pulse_desk/bot/cards.py`
- Test: `tests/test_bot_cards.py`

- [ ] **Step 1: Add failing tests** to `tests/test_bot_cards.py` (append before `if __name__`):

```python
from pulse_desk.bot.cards import feed_badge, feed_header, ping_card


class FeedRenderTests(unittest.TestCase):
    def test_badge_by_priority(self):
        self.assertEqual(feed_badge("critical"), "🔥")
        self.assertEqual(feed_badge("high"), "⚡")
        self.assertEqual(feed_badge("normal"), "•")

    def test_header_has_breadcrumb_and_count(self):
        out = feed_header("Чеки", 5)
        self.assertIn("**МОНИТОРИНГ**", out)
        self.assertIn("Чеки", out)
        self.assertIn("5", out)

    def test_empty_feed_header(self):
        out = feed_header("Все", 0)
        self.assertIn("📭", out)


class PingCardTests(unittest.TestCase):
    PING = {
        "id": 842, "detected_at": "2026-06-26T14:02:31", "chat": "@chan",
        "priority_label": "critical", "is_giveaway": 1, "is_win": 0, "is_check": 0,
        "text": "Поздравляем, вы выиграли подарок номер 17 в нашем розыгрыше!",
        "link": "https://t.me/chan/123",
    }

    def test_shows_id_chat_and_full_text(self):
        out = ping_card(self.PING)
        self.assertIn("#842", out)
        self.assertIn("@chan", out)
        self.assertIn("подарок номер 17", out)
        self.assertIn("https://t.me/chan/123", out)

    def test_shows_giveaway_tag(self):
        self.assertIn("🎁", ping_card(self.PING))

    def test_missing_text_falls_back(self):
        out = ping_card(dict(self.PING, text=None, link=None))
        self.assertIn("—", out)
```

- [ ] **Step 2: Run — expect fail.**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 3: Edit `src/pulse_desk/bot/cards.py`.** Change the import line:

```python
from .views import DIV
```
→
```python
from .views import DIV, fmt_dt
```

Then append:

```python
_BADGES = {"critical": "🔥", "high": "⚡"}
_PING_TEXT_CAP = 3500


def feed_badge(priority_label: Optional[str]) -> str:
    return _BADGES.get(priority_label or "", "•")


def feed_header(active_label: str, count: int) -> str:
    out = header("📡", "Мониторинг", f"Домой › Мониторинг › {active_label}")
    if count == 0:
        return out + "\n" + empty("Пока ничего.")
    return out + f"\nЗаписей: `{count}` · нажми на строку 👇"


def ping_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Мониторинг › #{ping.get('id')}"
    tags = [f"🏷 {ping.get('priority_label') or 'normal'}"]
    if ping.get("is_giveaway"):
        tags.append("🎁 розыгрыш")
    if ping.get("is_win"):
        tags.append("🏆 победа")
    if ping.get("is_check"):
        tags.append("💸 чек")
    text = (ping.get("text") or "—")[:_PING_TEXT_CAP]
    lines = [
        header(badge, f"Пинг #{ping.get('id')}", crumb),
        f"📅 `{fmt_dt(ping.get('detected_at'))}`  ·  {ping.get('chat') or '?'}",
        "  ·  ".join(tags),
        DIV,
        text,
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run — expect pass.**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py
git commit -m "feat(bot): feed_header/ping_card renderers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire feed + drilldown into `service.py`

**Files:**
- Modify: `src/pulse_desk/bot/service.py`

- [ ] **Step 1: Extend the cards/keyboards imports**

```python
from .keyboards import back_home, section_nav
from .cards import home_card, summary_card
```
→
```python
from .keyboards import MON_FILTERS, back_home, feed_keyboard, ping_card_keyboard, section_nav
from .cards import feed_badge, feed_header, home_card, ping_card, summary_card
```

- [ ] **Step 2: Add `render_feed` + `open_ping_view`** right after the `render_summary` closure:

```python
        FEED_LABELS = dict(MON_FILTERS)

        async def render_feed(active: str = "all"):
            if active not in FEED_LABELS:
                active = "all"
            rows = await get_pings(limit=8, chat_type=active)
            items = []
            for r in rows:
                hhmm = fmt_dt(r.get("detected_at"))[-5:]
                label = f"{feed_badge(r.get('priority_label'))} {hhmm} {r.get('chat') or '?'}"
                items.append((int(r["id"]), label[:48]))
            return feed_header(FEED_LABELS[active], len(rows)), feed_keyboard(items, active)

        async def open_ping_view(ping_id: int, is_admin: bool):
            ping = await get_ping_by_id(ping_id)
            if not ping:
                return None
            return ping_card(ping), ping_card_keyboard(ping_id, is_admin)
```

- [ ] **Step 3: Add `get_ping_by_id` to the `from database import (...)` block**

In the big `from database import (...)` import inside `init_bot`, add `get_ping_by_id,`
in alphabetical position (next to `get_pings`).

- [ ] **Step 4: Add the `:`-dispatch block** at the top of `callback_handler`, immediately after the `if role is None:` guard returns:

Find:
```python
            role = await bot_role(event.sender_id)
            if role is None:
                await event.answer("Доступ запрещён", alert=True)
                return
```
Insert directly after it:
```python
            # ---- structured `domain:action:arg` callbacks ----
            if ":" in data:
                seg = data.split(":")
                if seg[0] == "mon" and len(seg) >= 2 and seg[1] == "feed":
                    text, kb = await render_feed(seg[2] if len(seg) > 2 else "all")
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "mon" and len(seg) >= 3 and seg[1] == "open":
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    res = await open_ping_view(pid, role == "admin")
                    if res is None:
                        await event.answer("Запись не найдена", alert=True)
                        return
                    text, kb = res
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "ping" and len(seg) >= 3 and seg[1] in ("fav", "read"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    if seg[1] == "fav":
                        await toggle_favorite(pid)
                        await event.answer("Избранное обновлено")
                    else:
                        await mark_ping_read_db(pid)
                        await event.answer("Отмечено как прочитанное")
                    res = await open_ping_view(pid, True)
                    if res is not None:
                        text, kb = res
                        await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
```

- [ ] **Step 5: Repoint the `/recent`, `/latest`, `/checks` slash handlers to the feed**

`recent_handler` body — replace:
```python
            parts = (event.message.text or "").split(" ", 1)
            try:
                n = max(1, min(20, int(parts[1]))) if len(parts) > 1 else 5
            except (ValueError, IndexError):
                n = 5
            await event.respond(await render_recent(n), buttons=section_nav(b"menu_recent"), link_preview=False)
```
with:
```python
            text, kb = await render_feed("all")
            await event.respond(text, buttons=kb, link_preview=False)
```

`latest_handler` — replace its body line:
```python
            await event.respond(await render_recent(5), buttons=section_nav(b"menu_recent"), link_preview=False)
```
with:
```python
            text, kb = await render_feed("all")
            await event.respond(text, buttons=kb, link_preview=False)
```

`checks_handler` — replace:
```python
            await event.respond(await render_checks(10), buttons=section_nav(b"menu_checks"), link_preview=False)
```
with:
```python
            text, kb = await render_feed("check")
            await event.respond(text, buttons=kb, link_preview=False)
```

- [ ] **Step 6: Repoint the `menu_recent`/`menu_checks` callbacks to the feed**

In `callback_handler`, replace:
```python
            if data == "menu_recent":
                await safe_edit(event, await render_recent(5), buttons=section_nav(b"menu_recent"), link_preview=False)
                return
            if data == "menu_checks":
                await safe_edit(event, await render_checks(10), buttons=section_nav(b"menu_checks"), link_preview=False)
                return
```
with:
```python
            if data == "menu_recent":
                text, kb = await render_feed("all")
                await safe_edit(event, text, buttons=kb, link_preview=False)
                return
            if data == "menu_checks":
                text, kb = await render_feed("check")
                await safe_edit(event, text, buttons=kb, link_preview=False)
                return
```

- [ ] **Step 7: Verify import + compile + full tests**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py && .\.venv\Scripts\python.exe -m pytest -q`
Expected: import ok, compile ok, all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): clickable monitoring feed + ping drilldown

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Repoint the home-grid feed buttons

**Files:**
- Modify: `src/pulse_desk/bot/views.py`
- Test: `tests/test_bot_views.py`

- [ ] **Step 1: Add a failing test** in `MainMenuButtonsTests`:

```python
    def test_feed_buttons_target_monitoring(self):
        rows = main_menu_buttons("viewer")
        data = {b.text: b.data for row in rows for b in row}
        self.assertEqual(data["🕐 Последние"], b"mon:feed:all")
        self.assertEqual(data["💸 Чеки"], b"mon:feed:check")
```

- [ ] **Step 2: Run — expect fail** (still `menu_recent`/`menu_checks`).

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`

- [ ] **Step 3: Repoint in `main_menu_buttons`**

```python
        [Button.inline("🎁 Розыгрыши", b"menu_giveaways"), Button.inline("💸 Чеки", b"menu_checks")],
        [Button.inline("🕐 Последние", b"menu_recent"), Button.inline("📊 Сводка", b"menu_summary")],
```
→
```python
        [Button.inline("🎁 Розыгрыши", b"menu_giveaways"), Button.inline("💸 Чеки", b"mon:feed:check")],
        [Button.inline("🕐 Последние", b"mon:feed:all"), Button.inline("📊 Сводка", b"menu_summary")],
```

- [ ] **Step 4: Run — expect pass.**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/views.py tests/test_bot_views.py
git commit -m "feat(bot): point Последние/Чеки grid buttons at the feed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 4a Self-Review

- [ ] `import main` + full `pytest` green.
- [ ] `grep -n "mon:feed\|mon:open\|ping:fav\|ping:read" src/pulse_desk/bot/service.py`
      — dispatch + wiring present.
- [ ] Legacy `fav_`/`read_` callback branches still present and untouched
      (push notifications keep working).
- [ ] `menu_giveaways` still renders the giveaway board (Phase 4b adds its drilldown).
