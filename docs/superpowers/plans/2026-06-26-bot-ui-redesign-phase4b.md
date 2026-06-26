# Bot UI Redesign — Phase 4b (Giveaways Drilldown, read-only) Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Giveaways section a clickable list → read-only candidate card, and fix the stats it shows (the old view read nonexistent board keys).

**Architecture:** `render_giveaways` is rewritten to return `(text, keyboard)` from the real `get_giveaway_board` shape — stats keys `claim_prize/overdue/waiting_result` and the `need_action` bucket. Each need-action item is a `gw:open:<id>` button-row; the card reuses `get_ping_by_id` (board rows are pings) via a read-only `giveaway_card`. The home dashboard's "Срочных" counter is fixed to use `stats['overdue']` (the old `stats['urgent']` key never existed → always 0).

**Tech Stack:** Python 3, Telethon, `unittest`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Section 3 — giveaways drilldown, read-only).

**Bug fixed here:** `render_giveaways` previously read `buckets['urgent']` and `stats['waiting'|'to_claim'|'urgent']`, none of which `get_giveaway_board` emits — so the section always showed `0 · 0 · 0` and "срочных нет". Same wrong key leaked into `home_card`'s urgent counter (Phase 3).

**This is Phase 4b of 6.**

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/pulse_desk/bot/keyboards.py` | `giveaway_feed_keyboard`, `giveaway_card_keyboard`. | Modify (add) |
| `src/pulse_desk/bot/cards.py` | `giveaways_header`, `giveaway_card`. | Modify (add) |
| `src/pulse_desk/bot/service.py` | Rewrite `render_giveaways`→tuple; `open_giveaway_view`; `gw:open` dispatch; fix home urgent key; update call sites. | Modify |
| `tests/test_bot_keyboards.py` | Tests for the giveaway keyboards. | Modify (add) |
| `tests/test_bot_cards.py` | Tests for the giveaway cards. | Modify (add) |

---

## Task 1: Giveaway keyboards

**Files:** Modify `src/pulse_desk/bot/keyboards.py`; Test `tests/test_bot_keyboards.py`.

- [ ] **Step 1: Add failing tests** (append before `if __name__`):

```python
from pulse_desk.bot.keyboards import giveaway_card_keyboard, giveaway_feed_keyboard


class GiveawayKeyboardTests(unittest.TestCase):
    def test_items_open_giveaway(self):
        rows = giveaway_feed_keyboard([(50, "⏰ 06-27 @gw")])
        self.assertEqual(rows[0][0].data, b"gw:open:50")

    def test_feed_footer_home_and_refresh(self):
        rows = giveaway_feed_keyboard([])
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, b"menu_giveaways")

    def test_card_back_to_section_and_refresh(self):
        rows = giveaway_card_keyboard(50)
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_giveaways")
        self.assertEqual(footer[1].data, b"gw:open:50")

    def test_card_has_no_action_buttons(self):
        datas = [b.data for row in giveaway_card_keyboard(50) for b in row]
        self.assertNotIn(b"ping:fav:50", datas)
        self.assertEqual(len(datas), 2)  # read-only: only back + refresh
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 3: Append to `keyboards.py`:**

```python
def giveaway_feed_keyboard(items: list[tuple[int, str]]) -> list[list[Button]]:
    """Giveaways section: one row per candidate, then home/refresh."""
    rows: list[list[Button]] = [
        [Button.inline(label, f"gw:open:{pid}".encode())] for pid, label in items
    ]
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", b"menu_giveaways"),
    ])
    return rows


def giveaway_card_keyboard(ping_id: int) -> list[list[Button]]:
    """Read-only candidate card: back to the section + refresh."""
    return [[
        Button.inline("⬅️ Назад", b"menu_giveaways"),
        Button.inline("🔄 Обновить", f"gw:open:{ping_id}".encode()),
    ]]
```

- [ ] **Step 4: Run — expect pass.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase4b.md
git commit -m "feat(bot): giveaway feed + read-only card keyboards

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Giveaway renderers

**Files:** Modify `src/pulse_desk/bot/cards.py`; Test `tests/test_bot_cards.py`.

- [ ] **Step 1: Add failing tests** (append before `if __name__`):

```python
from pulse_desk.bot.cards import giveaway_card, giveaways_header


class GiveawaysHeaderTests(unittest.TestCase):
    STATS = {"claim_prize": 3, "overdue": 2, "waiting_result": 5}

    def test_shows_counts(self):
        out = giveaways_header(self.STATS, need_count=4)
        self.assertIn("**РОЗЫГРЫШИ**", out)
        self.assertIn("К действию: `4`", out)
        self.assertIn("Призы: `3`", out)
        self.assertIn("Просрочено: `2`", out)

    def test_empty_need_shows_calm_state(self):
        out = giveaways_header(self.STATS, need_count=0)
        self.assertIn("📭", out)


class GiveawayCardTests(unittest.TestCase):
    PING = {
        "id": 50, "detected_at": "2026-06-26T10:00:00", "deadline_at": "2026-06-27T18:00:00",
        "chat": "@gw", "priority_label": "high",
        "text": "Розыгрыш 100 TON среди подписчиков!", "link": "https://t.me/gw/9",
    }

    def test_shows_deadline_id_text_link(self):
        out = giveaway_card(self.PING)
        self.assertIn("#50", out)
        self.assertIn("06-27 18:00", out)
        self.assertIn("100 TON", out)
        self.assertIn("https://t.me/gw/9", out)

    def test_missing_deadline_shows_dash(self):
        out = giveaway_card(dict(self.PING, deadline_at=None))
        self.assertIn("Дедлайн: `—`", out)
```

- [ ] **Step 2: Run — expect fail.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 3: Append to `cards.py`:**

```python
def giveaways_header(stats: dict, need_count: int) -> str:
    out = header("🎁", "Розыгрыши", "Домой › Розыгрыши")
    line1 = f"{kv('🟢', 'К действию', need_count)}   {kv('🏆', 'Призы', stats.get('claim_prize', 0))}"
    line2 = f"{kv('⏰', 'Просрочено', stats.get('overdue', 0))}   {kv('⏳', 'Ждут', stats.get('waiting_result', 0))}"
    body = f"{line1}\n{line2}"
    if need_count == 0:
        body += "\n" + empty("Срочных нет — всё под контролем.")
    return f"{out}\n{body}"


def giveaway_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Розыгрыши › #{ping.get('id')}"
    deadline = ping.get("deadline_at")
    lines = [
        header(badge, f"Розыгрыш #{ping.get('id')}", crumb),
        f"⏰ Дедлайн: `{fmt_dt(deadline) if deadline else '—'}`  ·  {ping.get('chat') or '?'}",
        DIV,
        (ping.get("text") or "—")[:_PING_TEXT_CAP],
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run — expect pass.** `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py
git commit -m "feat(bot): giveaways_header/giveaway_card renderers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire giveaways drilldown + fix home urgent

**Files:** Modify `src/pulse_desk/bot/service.py`.

- [ ] **Step 1: Extend imports**

```python
from .keyboards import MON_FILTERS, back_home, feed_keyboard, ping_card_keyboard, section_nav
from .cards import feed_badge, feed_header, home_card, ping_card, summary_card
```
→
```python
from .keyboards import (
    MON_FILTERS, back_home, feed_keyboard, giveaway_card_keyboard,
    giveaway_feed_keyboard, ping_card_keyboard, section_nav,
)
from .cards import (
    feed_badge, feed_header, giveaway_card, giveaways_header,
    home_card, ping_card, summary_card,
)
```

- [ ] **Step 2: Rewrite `render_giveaways`** (it currently returns a str built from wrong keys). Replace the whole closure with:

```python
        async def render_giveaways():
            board = await get_giveaway_board(limit=10)
            stats = board.get("stats") or {}
            need = (board.get("buckets") or {}).get("need_action") or []
            items = []
            for r in need[:8]:
                deadline = r.get("deadline_at")
                when = fmt_dt(deadline) if deadline else fmt_dt(r.get("detected_at"))
                label = f"{feed_badge(r.get('priority_label'))} {when} {r.get('chat') or '?'}"
                items.append((int(r["id"]), label[:48]))
            return giveaways_header(stats, len(need)), giveaway_feed_keyboard(items)

        async def open_giveaway_view(ping_id: int):
            ping = await get_ping_by_id(ping_id)
            if not ping:
                return None
            return giveaway_card(ping), giveaway_card_keyboard(ping_id)
```

(The current `render_giveaways` body — from `async def render_giveaways() -> str:` through its `return "\n".join(lines)` — is replaced entirely by the two closures above.)

- [ ] **Step 3: Fix the home dashboard urgent counter**

In `render_home`, change:
```python
            urgent = (board.get("stats") or {}).get("urgent", 0)
```
→
```python
            urgent = (board.get("stats") or {}).get("overdue", 0)
```

- [ ] **Step 4: Add `gw:open` to the `:`-dispatch block**, after the `ping` branch:

```python
                if seg[0] == "gw" and len(seg) >= 3 and seg[1] == "open":
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    res = await open_giveaway_view(pid)
                    if res is None:
                        await event.answer("Розыгрыш не найден", alert=True)
                        return
                    text, kb = res
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
```

- [ ] **Step 5: Update the `/giveaways` slash + `menu_giveaways` callback to unpack the tuple**

`giveaways_handler`:
```python
            await event.respond(await render_giveaways(), buttons=section_nav(b"menu_giveaways"), link_preview=False)
```
→
```python
            text, kb = await render_giveaways()
            await event.respond(text, buttons=kb, link_preview=False)
```

`menu_giveaways` callback:
```python
                await safe_edit(event, await render_giveaways(), buttons=section_nav(b"menu_giveaways"), link_preview=False)
```
→
```python
                text, kb = await render_giveaways()
                await safe_edit(event, text, buttons=kb, link_preview=False)
```

- [ ] **Step 6: Verify import + compile + full tests**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py && .\.venv\Scripts\python.exe -m pytest -q`
Expected: import ok, compile ok, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): giveaways drilldown + fix urgent/overdue stats

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 4b Self-Review

- [ ] `import main` + full `pytest` green.
- [ ] `grep -n "seg\[0\] == \"gw\"\|open_giveaway_view\|render_giveaways" src/pulse_desk/bot/service.py` — dispatch + tuple call sites consistent.
- [ ] No remaining reads of `stats['urgent']`/`stats['waiting']`/`stats['to_claim']` or `buckets['urgent']` anywhere in `bot/`.
