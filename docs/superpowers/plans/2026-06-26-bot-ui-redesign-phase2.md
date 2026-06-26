# Bot UI Redesign — Phase 2 (Chrome + Navigation Footer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the tested chrome component vocabulary (`header`/`kv`/`dot`/`empty`) and keyboard builders (`section_nav`/`back_home`), then replace the "full main-menu dump under every view" with a clean `[⬅️ Домой] [🔄 Обновить]` footer.

**Architecture:** Read-only data views (stats/status/market/giveaways/recent/checks/help/search) currently re-append the entire `main_menu_buttons` grid. We keep `main_menu_buttons` as the **home grid** (shown by `menu_main`/`/menu`/`/start`) and give every *section* view a slim footer instead. "Обновить" reuses the existing `menu_<x>` callback — those handlers already re-render the view via `safe_edit`, so refresh needs no new dispatch logic. `chrome.py` lands as the tested foundation that Phase 3 wires into the renderers.

**Tech Stack:** Python 3, Telethon (`telethon.Button`), `unittest` under `tests/`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Sections 2 — Visual language, 1 — footer chrome).

**This is Phase 2 of 6.** The structured `nav:*` callback dispatcher (spec Section 5) is deferred to the phase that introduces genuinely new callbacks (drilldown/management), where it pays for itself.

---

## File Structure

| File | Responsibility | Phase 2 action |
|---|---|---|
| `src/pulse_desk/bot/chrome.py` | Pure visual components: `header`, `kv`, `dot`, `empty`. Foundation for Phase 3 renderers. | Create |
| `src/pulse_desk/bot/keyboards.py` | Inline-keyboard builders: `section_nav`, `back_home`. | Create |
| `src/pulse_desk/bot/service.py` | Swap per-view `main_menu_buttons(role)` for the new footers. | Modify call sites |
| `tests/test_bot_chrome.py` | Unit tests for chrome components. | Create |
| `tests/test_bot_keyboards.py` | Unit tests for keyboard builders. | Create |

---

## Task 1: `bot/chrome.py` visual components

**Files:**
- Create: `src/pulse_desk/bot/chrome.py`
- Test: `tests/test_bot_chrome.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_chrome.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.chrome import dot, empty, header, kv


class HeaderTests(unittest.TestCase):
    def test_title_uppercased_and_bolded(self):
        out = header("📡", "Мониторинг")
        self.assertTrue(out.startswith("📡 **МОНИТОРИНГ**"))

    def test_breadcrumb_italic_when_given(self):
        out = header("📡", "Чеки", "Домой › Мониторинг › Чеки")
        self.assertIn("__Домой › Мониторинг › Чеки__", out)

    def test_no_breadcrumb_line_when_absent(self):
        out = header("📊", "Сводка")
        self.assertNotIn("__", out)

    def test_ends_with_divider(self):
        self.assertTrue(header("📊", "Сводка").rstrip().endswith("━"))


class KvTests(unittest.TestCase):
    def test_value_monospaced(self):
        self.assertEqual(kv("🆕", "Новых", 12), "🆕 Новых: `12`")


class DotTests(unittest.TestCase):
    def test_bool_true_is_green(self):
        self.assertEqual(dot(True), "🟢")

    def test_bool_false_is_red(self):
        self.assertEqual(dot(False), "🔴")

    def test_known_statuses(self):
        self.assertEqual(dot("online"), "🟢")
        self.assertEqual(dot("offline"), "🔴")
        self.assertEqual(dot("degraded"), "🟡")

    def test_unknown_status_is_amber(self):
        self.assertEqual(dot("connecting"), "🟡")


class EmptyTests(unittest.TestCase):
    def test_empty_wraps_in_box_and_italics(self):
        self.assertEqual(empty("Чеков пока нет."), "📭 __Чеков пока нет.__")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_chrome.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.bot.chrome'`.

- [ ] **Step 3: Create `bot/chrome.py`**

```python
"""Pure visual components for the bot UI — the shared style vocabulary.

No Telethon events, no I/O. Reused by every renderer so the look stays
consistent. Foundation for the Phase 3 renderer rework.
"""
from __future__ import annotations

from typing import Optional, Union

from .views import DIV

_DOTS = {"online": "🟢", "offline": "🔴", "degraded": "🟡"}


def header(icon: str, title: str, crumb: Optional[str] = None) -> str:
    """Section banner: CAPS title, optional breadcrumb subtitle, divider."""
    lines = [f"{icon} **{title.upper()}**"]
    if crumb:
        lines.append(f"__{crumb}__")
    lines.append(DIV)
    return "\n".join(lines)


def kv(icon: str, label: str, value: object) -> str:
    """Key-value row with a monospaced value."""
    return f"{icon} {label}: `{value}`"


def dot(status: Union[bool, str]) -> str:
    """Status indicator: 🟢 online / 🔴 offline / 🟡 anything else."""
    if status is True:
        return "🟢"
    if status is False:
        return "🔴"
    return _DOTS.get(str(status).lower(), "🟡")


def empty(text: str) -> str:
    """Uniform empty-state line."""
    return f"📭 __{text}__"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_chrome.py -q`
Expected: PASS — 9 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/chrome.py tests/test_bot_chrome.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase2.md
git commit -m "feat(bot): add chrome component vocabulary (header/kv/dot/empty)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `bot/keyboards.py` navigation footers

**Files:**
- Create: `src/pulse_desk/bot/keyboards.py`
- Test: `tests/test_bot_keyboards.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_keyboards.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.keyboards import back_home, section_nav


class SectionNavTests(unittest.TestCase):
    def test_one_row_home_then_refresh(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(len(rows), 1)
        labels = [b.text for b in rows[0]]
        self.assertEqual(labels, ["⬅️ Домой", "🔄 Обновить"])

    def test_home_button_targets_menu_main(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(rows[0][0].data, b"menu_main")

    def test_refresh_button_carries_given_callback(self):
        rows = section_nav(b"menu_market")
        self.assertEqual(rows[0][1].data, b"menu_market")


class BackHomeTests(unittest.TestCase):
    def test_single_home_button(self):
        rows = back_home()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].data, b"menu_main")
        self.assertEqual(rows[0][0].text, "⬅️ Домой")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.bot.keyboards'`.

- [ ] **Step 3: Create `bot/keyboards.py`**

```python
"""Inline-keyboard builders for the bot UI."""
from __future__ import annotations

from telethon import Button


def section_nav(refresh_cb: bytes) -> list[list[Button]]:
    """Footer for a section view: back to home + refresh.

    `refresh_cb` is the view's own `menu_<x>` callback — those handlers already
    re-render the view, so refresh needs no new dispatch.
    """
    return [[Button.inline("⬅️ Домой", b"menu_main"), Button.inline("🔄 Обновить", refresh_cb)]]


def back_home() -> list[list[Button]]:
    """Footer for views with no refresh target (e.g. search results)."""
    return [[Button.inline("⬅️ Домой", b"menu_main")]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_keyboards.py -q`
Expected: PASS — 5 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/keyboards.py tests/test_bot_keyboards.py
git commit -m "feat(bot): add section_nav/back_home keyboard footers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Swap per-view keyboards to the footer

**Files:**
- Modify: `src/pulse_desk/bot/service.py`

Add the import, then replace each data-view keyboard. `main_menu_buttons` stays
only on home/onboarding surfaces (`menu_main`, `/menu`, `/start`, `grant_access`,
locked/notice flows, and admin list views — those keep their current keyboards
and are re-chromed in Phase 5).

- [ ] **Step 1: Add the keyboards import**

In `src/pulse_desk/bot/service.py`, directly below the existing
`from .views import DIV, fmt_dt, help_text, main_menu_buttons, menu_caption`
line, add:

```python
from .keyboards import back_home, section_nav
```

- [ ] **Step 2: Swap slash-command handler keyboards**

Replace each of these exact lines (slash handlers) in `bot/service.py`:

| Find | Replace `buttons=...` with |
|---|---|
| `await event.respond(help_text(role), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_help")` |
| `await event.respond(await render_stats(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_stats")` |
| `await event.respond(await render_status(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_status")` |
| `await event.respond(await render_giveaways(), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_giveaways"), link_preview=False` |
| `await event.respond(await render_recent(n), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_recent"), link_preview=False` |
| `await event.respond(await render_recent(5), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_recent"), link_preview=False` |
| `await event.respond(await render_checks(10), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_checks"), link_preview=False` |
| `await event.respond(await render_market(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_market")` |

Each becomes e.g.:
`await event.respond(await render_stats(), buttons=section_nav(b"menu_stats"))`

- [ ] **Step 3: Swap search handler keyboards to `back_home()`**

In `search_handler`, the two result branches use `main_menu_buttons(role)`:

```python
await event.respond("🔎 __Ничего не найдено.__", buttons=main_menu_buttons(role))
```
→
```python
await event.respond("🔎 __Ничего не найдено.__", buttons=back_home())
```

and the results branch:
```python
await event.respond("\n\n".join(result), buttons=main_menu_buttons(role), link_preview=False)
```
→
```python
await event.respond("\n\n".join(result), buttons=back_home(), link_preview=False)
```

- [ ] **Step 4: Swap callback-menu keyboards**

In `callback_handler`, replace the read-only nav branches:

| Find | Replace `buttons=...` with |
|---|---|
| `await safe_edit(event, help_text(role), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_help")` |
| `await safe_edit(event, await render_stats(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_stats")` |
| `await safe_edit(event, await render_status(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_status")` |
| `await safe_edit(event, await render_giveaways(), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_giveaways"), link_preview=False` |
| `await safe_edit(event, await render_recent(5), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_recent"), link_preview=False` |
| `await safe_edit(event, await render_checks(10), buttons=main_menu_buttons(role), link_preview=False)` | `buttons=section_nav(b"menu_checks"), link_preview=False` |
| `await safe_edit(event, await render_market(), buttons=main_menu_buttons(role))` | `buttons=section_nav(b"menu_market")` |

Leave `menu_main` (`await safe_edit(event, menu_caption(role), buttons=main_menu_buttons(role))`)
and the admin branches (`menu_keys`/`menu_logs`/members/keys/actions/access) untouched.

- [ ] **Step 5: Verify import + compile**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py`
Expected: exit 0, no output.

- [ ] **Step 6: Confirm no stray data-view dumps remain**

Run: `grep -n "main_menu_buttons" src/pulse_desk/bot/service.py`
Expected: matches only on home/onboarding/admin-list lines — `menu_main`,
`menu_handler`, `start_handler`, `grant_access`, `locked`/notice replies, and the
admin `members`/`keys`/`actions`/`access` overviews. No `render_stats`/`status`/
`market`/`giveaways`/`recent`/`checks`/`help` line should still use it.

- [ ] **Step 7: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: all pass (Phase 1 + new chrome/keyboards tests).

- [ ] **Step 8: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): replace per-view menu dump with home/refresh footer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 2 Self-Review (run before handing off)

- [ ] `import main` and full `pytest` green.
- [ ] `grep -n "section_nav\|back_home" src/pulse_desk/bot/service.py` — every
      data view now carries a footer.
- [ ] `grep -n "main_menu_buttons" src/pulse_desk/bot/service.py` — only home,
      onboarding, and admin-list surfaces remain.
