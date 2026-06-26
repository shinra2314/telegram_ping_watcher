# Bot UI Redesign — Phase 1 (Refactor Scaffold) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `src/pulse_desk/bot/` package, extract the bot's pure presentation helpers into a testable module, and move `init_bot` behind a thin `bot_service.py` shim — with zero behavior change.

**Architecture:** `bot_service.py` is a single 1490-line `init_bot()` of nested closures. Phase 1 carves out the genuinely-pure helpers (divider, date formatting, help/menu text, main-menu keyboard) into `bot/views.py`, moves the whole `init_bot` body into `bot/service.py`, and leaves `bot_service.py` as a re-export shim so `main.py` keeps importing `from pulse_desk.bot_service import init_bot`. No rendering, navigation, or callback logic changes yet — those are later phases.

**Tech Stack:** Python 3, Telethon (`telethon.Button`), `unittest` tests under `tests/` (sys.path-inserts `src/`), pytest runner (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Section 5 — Architecture).

**This is Phase 1 of 6.** Phases 2–6 (chrome+dispatcher, dashboard home, clickable lists/drilldown, management buttons, giveaway-join removal) get their own plans once this foundation lands. See the roadmap at the end.

---

## File Structure

| File | Responsibility | Phase 1 action |
|---|---|---|
| `src/pulse_desk/bot/__init__.py` | Package marker | Create (empty/docstring) |
| `src/pulse_desk/bot/views.py` | Pure presentation helpers: `DIV`, `fmt_dt`, `help_text`, `menu_caption`, `main_menu_buttons`. No I/O, no event objects. | Create |
| `src/pulse_desk/bot/service.py` | `init_bot()` and all its current closures, moved verbatim; imports pure helpers from `.views`. | Create (move body) |
| `src/pulse_desk/bot_service.py` | Backward-compat shim: `from .bot.service import init_bot`. | Rewrite to shim |
| `tests/test_bot_views.py` | Unit tests for the pure helpers. | Create |

**Import-depth note:** `bot/service.py` sits one package level deeper than the
old `bot_service.py`. Inside it, every `from .X import …` that referenced a
`pulse_desk` sibling becomes `from ..X import …`. Top-level imports
(`from telegram_ping_watcher import …`, `import database`) stay unchanged.

---

## Task 1: Extract pure presentation helpers into `bot/views.py`

**Files:**
- Create: `src/pulse_desk/bot/__init__.py`
- Create: `src/pulse_desk/bot/views.py`
- Test: `tests/test_bot_views.py`

The helpers being extracted are copied from `src/pulse_desk/bot_service.py`:
`DIV` (line 252), `_fmt_dt` (lines 254–259, renamed to public `fmt_dt`),
`main_menu_buttons` (lines 176–192), `help_text` (lines 261–294),
`menu_caption` (lines 732–734).

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_views.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.views import (
    DIV,
    fmt_dt,
    help_text,
    main_menu_buttons,
    menu_caption,
)


class FmtDtTests(unittest.TestCase):
    def test_none_and_empty_render_dash(self):
        self.assertEqual(fmt_dt(None), "—")
        self.assertEqual(fmt_dt(""), "—")

    def test_iso_trimmed_to_month_day_hour_minute(self):
        self.assertEqual(fmt_dt("2026-06-26T14:02:31"), "06-26 14:02")

    def test_short_value_passed_through(self):
        self.assertEqual(fmt_dt("2026"), "2026")


class MainMenuButtonsTests(unittest.TestCase):
    def _labels(self, role):
        return [b.text for row in main_menu_buttons(role) for b in row]

    def test_viewer_has_no_admin_controls(self):
        labels = self._labels("viewer")
        self.assertIn("📊 Статистика", labels)
        self.assertIn("🔔 Мои уведомления", labels)
        self.assertNotIn("⚙️ Настройки", labels)
        self.assertNotIn("🔑 Ключи", labels)

    def test_admin_has_owner_controls(self):
        labels = self._labels("admin")
        self.assertIn("🔑 Ключи", labels)
        self.assertIn("⚙️ Настройки", labels)
        self.assertIn("♻️ Рестарт", labels)
        self.assertNotIn("🔔 Мои уведомления", labels)


class HelpTextTests(unittest.TestCase):
    def test_admin_help_lists_owner_commands(self):
        text = help_text("admin")
        self.assertIn("/scan", text)
        self.assertIn("/newkey", text)
        self.assertIn("Владелец", text)

    def test_viewer_help_hides_owner_commands(self):
        text = help_text("viewer")
        self.assertNotIn("/scan", text)
        self.assertIn("только просмотр", text)


class MenuCaptionTests(unittest.TestCase):
    def test_caption_reflects_role(self):
        self.assertIn("владелец", menu_caption("admin"))
        self.assertIn("просмотр", menu_caption("viewer"))


class DivTests(unittest.TestCase):
    def test_divider_is_box_drawing_run(self):
        self.assertTrue(set(DIV) == {"━"})
        self.assertGreaterEqual(len(DIV), 10)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.bot'`.

- [ ] **Step 3: Create the package marker**

Create `src/pulse_desk/bot/__init__.py`:

```python
"""Telegram bot UI package: pure views/keyboards, chrome, callbacks, service."""
```

- [ ] **Step 4: Create `bot/views.py` with the pure helpers**

Create `src/pulse_desk/bot/views.py`:

```python
"""Pure presentation helpers for the Telegram bot UI.

No Telethon events, no I/O — strings and keyboard structures only, so these
are unit-testable without a running bot.
"""
from __future__ import annotations

from typing import Optional

from telethon import Button

from .. import APP_VERSION  # noqa: F401  (re-exported for later phases)

DIV = "━━━━━━━━━━━━━━━"


def fmt_dt(value: Optional[str]) -> str:
    """Trim an ISO timestamp to `MM-DD HH:MM` for compact display."""
    if not value:
        return "—"
    text = str(value).replace("T", " ")
    return text[5:16] if len(text) >= 16 else text


def main_menu_buttons(role: str) -> list[list[Button]]:
    rows = [
        [Button.inline("📊 Статистика", b"menu_stats"), Button.inline("🎁 Розыгрыши", b"menu_giveaways")],
        [Button.inline("💸 Чеки", b"menu_checks"), Button.inline("🕐 Последние", b"menu_recent")],
        [Button.inline("💹 Курсы", b"menu_market"), Button.inline("🛰 Статус", b"menu_status")],
        [Button.inline("❓ Помощь", b"menu_help")],
    ]
    if role == "admin":
        rows.append([
            Button.inline("🔑 Ключи", b"menu_keys"),
            Button.inline("🔄 Скан", b"menu_scan"),
            Button.inline("📜 Логи", b"menu_logs"),
        ])
        rows.append([Button.inline("⚙️ Настройки", b"st"), Button.inline("♻️ Рестарт", b"menu_restart")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
    return rows


def help_text(role: str) -> str:
    lines = [
        "🛰 **PULSE DESK**",
        "__Мониторинг каналов и розыгрышей__",
        DIV,
        "📋 **Команды**",
        "• /menu — главное меню",
        "• /stats — статистика",
        "• /status — состояние аккаунтов",
        "• /giveaways — розыгрыши",
        "• /recent `[N]` — последние упоминания",
        "• /checks — найденные чеки",
        "• /search `<текст>` — поиск",
        "• /market — курсы",
        "• /settings — настройки и уведомления",
        "• /ping — проверка связи",
    ]
    if role == "admin":
        lines += [
            "",
            "👑 **Владелец**",
            "• /scan — скан истории",
            "• /logs — последние логи",
            "• /export — CSV выгрузка",
            "• /newkey `[метка]` — создать ключ",
            "• /keys — список ключей",
            "• /members `[запрос]` — пользователи / поиск",
            "• /access `<user>` — доступ по расписанию",
            "• /actions — действия по розыгрышам",
            "• /settings — настройки мониторинга",
        ]
    else:
        lines += ["", "👁 __Режим: только просмотр__"]
    return "\n".join(lines)


def menu_caption(role: str) -> str:
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    return f"🛰 **PULSE DESK** · __{badge}__\nВыберите раздел 👇"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -v`
Expected: PASS — all 9 tests green.

- [ ] **Step 6: Commit (includes the approved spec)**

```bash
git add docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase1.md \
        src/pulse_desk/bot/__init__.py src/pulse_desk/bot/views.py \
        tests/test_bot_views.py
git commit -m "refactor(bot): extract pure view helpers into bot package

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Move `init_bot` into `bot/service.py` and make `bot_service.py` a shim

**Files:**
- Create: `src/pulse_desk/bot/service.py` (moved body of current `bot_service.py`)
- Modify: `src/pulse_desk/bot_service.py` (becomes a re-export shim)
- Test: existing suite + `import main`

This is a mechanical move. The whole current contents of `bot_service.py`
(module docstring, imports, `_ACCESS_DAY_NAMES`, `async def init_bot()` and every
nested closure) move into `bot/service.py`, with three edits:

1. The file now lives in package `pulse_desk.bot`, so each relative import of a
   `pulse_desk` sibling gains a level: `from .X import …` → `from ..X import …`.
   This applies to every `from .` import at the top of the file
   (`from . import APP_VERSION`, `from . import watch_settings as ws`,
   `from .access_control import …`, `from .analytics import …`,
   `from .app_ctx import …`, `from .bot_notify import …`, `from .bot_prefs import …`,
   `from .common import …`, `from .giveaway_actions import …`, `from .live import …`,
   `from .scan_engine import …`, `from .security import …`,
   `from .telegram_accounts import …`). Top-level imports
   (`from telethon …`, `from telegram_ping_watcher import …`, `from fastapi …`)
   are unchanged.
2. Add `from .views import DIV, fmt_dt, help_text, main_menu_buttons, menu_caption`.
3. Delete the now-duplicated local definitions of `DIV`, `_fmt_dt`,
   `main_menu_buttons`, `help_text`, `menu_caption` from inside `init_bot`, and
   rename the two internal call sites `_fmt_dt(` → `fmt_dt(` (the helper is now
   imported under its public name). All other closures stay byte-for-byte.

- [ ] **Step 1: Create `bot/service.py` as the moved module**

Copy the entire current contents of `src/pulse_desk/bot_service.py` into the new
`src/pulse_desk/bot/service.py`, then apply edits 1–3 above. Concretely, the new
import block at the top of `bot/service.py` reads:

```python
"""Telegram bot service: inline menus, slash commands, multi-user access keys."""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from telegram_ping_watcher import normalize_usernames

from .. import APP_VERSION
from .. import watch_settings as ws
from ..access_control import find_undoable, parse_repeat_rule, plan_undo, resolve_access, window_from_row
from ..analytics import build_analytics
from ..app_ctx import ADMIN_ID, API_HASH, API_ID, BOT_TOKEN, CHECK_FRESH_MINUTES, LOG_FILE, logger, settings, state
from ..bot_notify import BOT_ASSETS_DIR
from ..bot_prefs import (
    KEYWORD_SCOPES,
    next_hhmm_datetime,
    parse_duration_to_seconds,
    parse_hhmm,
    parse_member_prefs,
    parse_quiet_hours_input,
    parse_weekday_spec,
    render_keyword_list_text,
    render_member_prefs_text,
    render_notification_settings_text,
    render_tracking_text,
    toggle_member_pref,
)
from ..common import record_app_event
from ..giveaway_actions import confirm_safe_giveaway_join
from ..live import publish_live_event
from ..scan_engine import full_history_scan
from ..security import generate_access_key
from ..telegram_accounts import restart_monitoring, telegram_client_for_session
from .views import DIV, fmt_dt, help_text, main_menu_buttons, menu_caption


_ACCESS_DAY_NAMES = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}
```

Inside `init_bot`, delete these now-imported local definitions:
- `def main_menu_buttons(role): …` (was lines 176–192)
- `DIV = "━━━━━━━━━━━━━━━"` (was line 252)
- `def _fmt_dt(value): …` (was lines 254–259)
- `def help_text(role): …` (was lines 261–294)
- `def menu_caption(role): …` (was lines 732–734)

Then replace the two remaining `_fmt_dt(` call sites with `fmt_dt(`. Find them:

Run: `grep -rn "_fmt_dt" src/pulse_desk/bot/service.py`
Expected after rename: no matches (every use is now `fmt_dt`).

- [ ] **Step 2: Rewrite `bot_service.py` as a shim**

Replace the entire contents of `src/pulse_desk/bot_service.py` with:

```python
"""Backward-compat shim. The bot UI now lives in the `pulse_desk.bot` package.

`main.py` and any external caller still do `from pulse_desk.bot_service import
init_bot`; this re-export keeps that import path stable.
"""
from __future__ import annotations

from .bot.service import init_bot

__all__ = ["init_bot"]
```

- [ ] **Step 3: Verify the app still imports (registers everything)**

Run: `.\.venv\Scripts\python.exe -c "import main"`
Expected: no output, exit code 0 (imports every module + registers all routers,
including `init_bot` via the shim).

- [ ] **Step 4: Verify byte-compilation of touched files**

Run: `.\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot_service.py src/pulse_desk/bot/service.py src/pulse_desk/bot/views.py`
Expected: no output, exit code 0.

- [ ] **Step 5: Run the full test suite (no regressions)**

Run: `.\.venv\Scripts\python.exe -m pytest`
Expected: all tests pass, including the new `tests/test_bot_views.py` and the
existing `tests/test_bot_prefs.py` / `tests/test_bot_member_block.py`.

- [ ] **Step 6: Commit**

```bash
git add src/pulse_desk/bot_service.py src/pulse_desk/bot/service.py
git commit -m "refactor(bot): move init_bot into bot.service behind a shim

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 1 Self-Review (run before handing off)

- [ ] `grep -rn "from pulse_desk.bot_service" .` and `grep -rn "import bot_service" .`
      — confirm `main.py` is the only importer and it still resolves via the shim.
- [ ] `grep -rn "_fmt_dt\|def main_menu_buttons\|def help_text\|def menu_caption" src/pulse_desk/bot/service.py`
      — confirm no leftover duplicate definitions (all come from `.views`).
- [ ] `import main` and full `pytest` both green.

---

## Roadmap — Phases 2–6 (separate plans, written after Phase 1 lands)

Each builds on the package + pure-view seam from Phase 1. Detailed bite-sized
plans are authored once the prior phase is merged, because each locks the API the
next depends on.

- **Phase 2 — Chrome + dispatcher.** Add `bot/chrome.py` (`header`/`kv`/`dot`/
  `footer`/`empty`), `bot/keyboards.py`, and `bot/callbacks.py` (structured
  `prefix:arg` routing table replacing the if/elif chain) with legacy aliases for
  in-the-wild callback data. Switch navigation to edit-in-place (SPA). Unit tests
  on chrome + the routing table.
- **Phase 3 — Dashboard home + Сводка.** Live home card (5 counters from existing
  data sources), combined read-only Сводка card. Split data-fetch from rendering
  so the render functions become pure + tested.
- **Phase 4 — Clickable lists + drilldown.** Monitoring feed as full-width
  button-rows, ping detail card (full text + ⭐/read), filter row; giveaways
  drilldown (read-only).
- **Phase 5 — Management via buttons.** Люди (member card), Доступ (close/open
  presets + undo/history), Ключи (create/revoke). `work`/`cron` stay CLI.
- **Phase 6 — Remove giveaway-join.** Cut `giveaway_actions.py`,
  `confirm_safe_giveaway_join`, `gconfirm/gskip` buttons (bot + `bot_notify.py`),
  the join web endpoints in `routers/giveaways.py`, and `DRY_RUN_GIVEAWAYS`
  (`config.py`, `app_ctx.py`, `.env.example`). Detection + board stay. Touches
  ~11 files — exact map produced in that phase's plan.
