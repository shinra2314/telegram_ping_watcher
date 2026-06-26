# Bot UI Redesign — Phase 3 (Dashboard Home + Combined Сводка) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the bare home caption into a live dashboard card (5 counters) and collapse the separate Stats/Market/Status views into one combined read-only Сводка card — wiring the Phase 2 `chrome` vocabulary into real renderers.

**Architecture:** Pure card formatters live in a new `bot/cards.py` (`home_card`, `summary_card`) — they take already-fetched primitives/dicts and return text, so they unit-test without a bot. `service.py` gains thin async wrappers (`render_home`, `render_summary`) that fetch data (analytics, giveaway board, market, system state) and call the pure cards. The home grid loses the three separate Stats/Market/Status buttons in favor of one 📊 Сводка; the underlying `menu_stats`/`menu_status`/`menu_market` callbacks and slash commands stay (still reachable via `/stats` etc. and as refresh targets).

**Tech Stack:** Python 3, Telethon, `unittest` under `tests/`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-bot-ui-redesign-design.md` (Section 1 — home dashboard, Section 2 — Сводка card).

**This is Phase 3 of 6.** Monitoring/Management section grouping arrives in Phases 4–5; the home grid converges to the full taxonomy then.

---

## File Structure

| File | Responsibility | Phase 3 action |
|---|---|---|
| `src/pulse_desk/bot/cards.py` | Pure composed-card renderers: `home_card`, `summary_card`. | Create |
| `src/pulse_desk/bot/views.py` | Home grid (`main_menu_buttons`) — collapse Stats/Market/Status → Сводка. | Modify |
| `src/pulse_desk/bot/service.py` | `render_home` + `render_summary` async wrappers; wire home surfaces + `menu_summary` callback. | Modify |
| `tests/test_bot_cards.py` | Unit tests for the pure cards. | Create |
| `tests/test_bot_views.py` | Update grid assertions for the new Сводка button. | Modify |

---

## Task 1: Pure card renderers in `bot/cards.py`

**Files:**
- Create: `src/pulse_desk/bot/cards.py`
- Test: `tests/test_bot_cards.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_cards.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.cards import home_card, summary_card


class HomeCardTests(unittest.TestCase):
    def _card(self, **over):
        data = dict(
            role="admin", new_pings=12, urgent=3, accounts_online=4,
            accounts_total=4, fresh_checks=2, last_scan="06-26 14:02 · ok",
        )
        data.update(over)
        return home_card(**data)

    def test_shows_brand_and_owner_badge(self):
        out = self._card(role="admin")
        self.assertIn("**PULSE DESK**", out)
        self.assertIn("владелец", out)

    def test_viewer_badge(self):
        self.assertIn("просмотр", self._card(role="viewer"))

    def test_counters_present_and_monospaced(self):
        out = self._card()
        self.assertIn("Новых пингов: `12`", out)
        self.assertIn("Срочных: `3`", out)
        self.assertIn("Аккаунты: `4/4`", out)
        self.assertIn("Чеки: `2`", out)
        self.assertIn("Скан: `06-26 14:02 · ok`", out)

    def test_has_section_prompt(self):
        self.assertIn("Выберите раздел", self._card())


class SummaryCardTests(unittest.TestCase):
    ANALYTICS = {"total_pings": 1240, "new_pings": 12, "favorites": 8}
    MARKET = {"btc": 98420, "eth": 3510, "ton": 5.23, "sol": 182.4}
    SYSTEM = {
        "version": "1.5.0", "uptime": "12ч 30м", "db_mb": 8.4,
        "accounts_online": 4, "accounts_total": 4, "last_scan": "06-26 14:02 · ok",
    }

    def test_header_and_three_blocks(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("**СВОДКА**", out)
        self.assertIn("Записей: `1240`", out)
        self.assertIn("BTC", out)
        self.assertIn("Система", out)
        self.assertIn("v1.5.0", out)

    def test_market_values_rendered(self):
        out = summary_card(analytics=self.ANALYTICS, market=self.MARKET, system=self.SYSTEM)
        self.assertIn("98,420", out)
        self.assertIn("5.230", out)

    def test_missing_market_shows_empty_state(self):
        out = summary_card(analytics=self.ANALYTICS, market=None, system=self.SYSTEM)
        self.assertIn("📭", out)
        self.assertNotIn("BTC", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pulse_desk.bot.cards'`.

- [ ] **Step 3: Create `bot/cards.py`**

```python
"""Pure composed-card renderers for the bot UI.

Take already-fetched data, return Markdown text. No I/O, no Telethon — so they
unit-test without a running bot. Data fetching lives in service.py.
"""
from __future__ import annotations

from typing import Optional

from .chrome import empty, header, kv
from .views import DIV


def home_card(
    *,
    role: str,
    new_pings: int,
    urgent: int,
    accounts_online: int,
    accounts_total: int,
    fresh_checks: int,
    last_scan: str,
) -> str:
    """Live dashboard shown on the home screen."""
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    return "\n".join([
        header("🛰", "Pulse Desk", badge),
        f"{kv('🆕', 'Новых пингов', new_pings)}   {kv('🎁', 'Срочных', urgent)}",
        f"{kv('🛰', 'Аккаунты', f'{accounts_online}/{accounts_total}')}   {kv('💸', 'Чеки', fresh_checks)}",
        kv("🔄", "Скан", last_scan),
        DIV,
        "Выберите раздел 👇",
    ])


def summary_card(*, analytics: dict, market: Optional[dict], system: dict) -> str:
    """Combined read-only card: pings stats + market + system status."""
    lines = [
        header("📊", "Сводка"),
        f"{kv('📨', 'Записей', analytics['total_pings'])}   "
        f"{kv('🆕', 'Новых', analytics['new_pings'])}   "
        f"{kv('⭐', 'Избр', analytics['favorites'])}",
        DIV,
        "💹 **Курсы**",
    ]
    if market:
        lines.append(f"🟠 BTC `${market['btc']:,}`   🔷 ETH `${market['eth']:,}`")
        lines.append(f"💎 TON `${market['ton']:.3f}`   🟣 SOL `${market['sol']:.2f}`")
    else:
        lines.append(empty("Курсы пока недоступны."))
    lines += [
        DIV,
        f"🛰 **Система** · `v{system['version']}`",
        f"{kv('⏱', 'Uptime', system['uptime'])}   {kv('💾', 'База', f\"{system['db_mb']:.1f} MB\")}",
        f"{kv('🛰', 'Аккаунты', f\"{system['accounts_online']}/{system['accounts_total']}\")}   "
        f"{kv('🔄', 'Скан', system['last_scan'])}",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_cards.py -q`
Expected: PASS — 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py \
        docs/superpowers/plans/2026-06-26-bot-ui-redesign-phase3.md
git commit -m "feat(bot): add pure home_card/summary_card renderers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Collapse the home grid (Stats/Market/Status → Сводка)

**Files:**
- Modify: `src/pulse_desk/bot/views.py` (`main_menu_buttons`)
- Modify: `tests/test_bot_views.py` (grid assertions)

- [ ] **Step 1: Update the grid test first**

In `tests/test_bot_views.py`, replace the body of `test_viewer_has_no_admin_controls`:

```python
    def test_viewer_has_no_admin_controls(self):
        labels = self._labels("viewer")
        self.assertIn("📊 Сводка", labels)
        self.assertIn("🎁 Розыгрыши", labels)
        self.assertIn("🔔 Мои уведомления", labels)
        self.assertNotIn("📈 Статистика", labels)
        self.assertNotIn("⚙️ Настройки", labels)
        self.assertNotIn("🔑 Ключи", labels)
```

Add a new test in `MainMenuButtonsTests`:

```python
    def test_summary_replaces_split_views(self):
        for role in ("viewer", "admin"):
            labels = self._labels(role)
            self.assertIn("📊 Сводка", labels)
            self.assertNotIn("🛰 Статус", labels)
            self.assertNotIn("💹 Курсы", labels)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`
Expected: FAIL — old grid still has "🛰 Статус"/"💹 Курсы" and no "📊 Сводка".

- [ ] **Step 3: Rewrite `main_menu_buttons` in `bot/views.py`**

Replace the whole `main_menu_buttons` function with:

```python
def main_menu_buttons(role: str) -> list[list[Button]]:
    rows = [
        [Button.inline("🎁 Розыгрыши", b"menu_giveaways"), Button.inline("💸 Чеки", b"menu_checks")],
        [Button.inline("🕐 Последние", b"menu_recent"), Button.inline("📊 Сводка", b"menu_summary")],
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
    rows.append([Button.inline("❓ Помощь", b"menu_help")])
    return rows
```

- [ ] **Step 4: Run to verify it passes**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_views.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/views.py tests/test_bot_views.py
git commit -m "feat(bot): collapse stats/market/status into one Сводка grid button

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Wire `render_home` + `render_summary` into `service.py`

**Files:**
- Modify: `src/pulse_desk/bot/service.py`

- [ ] **Step 1: Add the cards import**

Below `from .keyboards import back_home, section_nav` add:

```python
from .cards import home_card, summary_card
```

- [ ] **Step 2: Add `render_home` and `render_summary` next to the other renderers**

Inside `init_bot`, just after the `render_market` closure, add:

```python
        async def render_home(role: str) -> str:
            analytics = await build_analytics()
            board = await get_giveaway_board(limit=10)
            urgent = (board.get("stats") or {}).get("urgent", 0)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=CHECK_FRESH_MINUTES)
            ).replace(microsecond=0).isoformat()
            fresh = await get_pings(limit=50, chat_type="check", message_date_from=cutoff)
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            if state.last_scan_status:
                last_scan = f"{last_scan} · {state.last_scan_status}"
            return home_card(
                role=role,
                new_pings=analytics["new_pings"],
                urgent=urgent,
                accounts_online=analytics["accounts_online"],
                accounts_total=len(state.accounts_state),
                fresh_checks=len(fresh),
                last_scan=last_scan,
            )

        async def render_summary() -> str:
            analytics = await build_analytics()
            market_rows = await get_market_history(limit=1)
            market = None
            if market_rows:
                m = market_rows[0]
                market = {
                    "btc": m.get("bitcoin", {}).get("usd", 0),
                    "eth": m.get("ethereum", {}).get("usd", 0),
                    "ton": m.get("the-open-network", {}).get("usd", 0),
                    "sol": m.get("solana", {}).get("usd", 0),
                }
            accounts_online = sum(1 for a in list(state.accounts_state.values()) if a.get("status") == "online")
            try:
                db_mb = database.DB_PATH.stat().st_size / 1024 / 1024 if database.DB_PATH.exists() else 0
            except Exception:
                db_mb = 0
            uptime_sec = int((datetime.now() - state.started_at).total_seconds())
            uptime = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            system = {
                "version": APP_VERSION,
                "uptime": uptime,
                "db_mb": db_mb,
                "accounts_online": accounts_online,
                "accounts_total": len(state.accounts_state),
                "last_scan": f"{last_scan} · {state.last_scan_status or '—'}",
            }
            return summary_card(analytics=analytics, market=market, system=system)
```

- [ ] **Step 3: Swap home surfaces to the dashboard**

Replace these three exact lines with the dashboard render:

`menu_handler`:
```python
            await event.respond(menu_caption(role), buttons=main_menu_buttons(role))
```
→
```python
            await event.respond(await render_home(role), buttons=main_menu_buttons(role))
```

`start_handler` banner branch:
```python
                    await event.respond(menu_caption(role), buttons=main_menu_buttons(role), file=str(welcome_banner))
```
→
```python
                    await event.respond(await render_home(role), buttons=main_menu_buttons(role), file=str(welcome_banner))
```

`start_handler` text fallback (the line immediately after the banner `try/except`):
```python
            await event.respond(menu_caption(role), buttons=main_menu_buttons(role))
```
→
```python
            await event.respond(await render_home(role), buttons=main_menu_buttons(role))
```

> Note: `menu_handler` and the `start_handler` fallback share the identical text
> `await event.respond(menu_caption(role), buttons=main_menu_buttons(role))`. Apply
> the replacement to **both** occurrences (replace-all is safe — both become the
> dashboard).

- [ ] **Step 4: Swap the `menu_main` callback to the dashboard**

```python
                await safe_edit(event, menu_caption(role), buttons=main_menu_buttons(role))
```
→
```python
                await safe_edit(event, await render_home(role), buttons=main_menu_buttons(role))
```

- [ ] **Step 5: Add the `menu_summary` callback branch**

In `callback_handler`, right after the `menu_market` branch:

```python
            if data == "menu_market":
                await safe_edit(event, await render_market(), buttons=section_nav(b"menu_market"))
                return
```

add:

```python
            if data == "menu_summary":
                await safe_edit(event, await render_summary(), buttons=section_nav(b"menu_summary"))
                return
```

- [ ] **Step 6: Verify import + compile**

Run: `.\.venv\Scripts\python.exe -c "import main" && .\.venv\Scripts\python.exe -m py_compile src/pulse_desk/bot/service.py`
Expected: exit 0, no output.

- [ ] **Step 7: Run full test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/pulse_desk/bot/service.py
git commit -m "feat(bot): live dashboard home + combined Сводка card

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase 3 Self-Review (run before handing off)

- [ ] `import main` and full `pytest` green.
- [ ] `grep -n "menu_summary" src/pulse_desk/bot/service.py` — grid button + callback wired.
- [ ] `grep -n "render_home\|render_summary" src/pulse_desk/bot/service.py` — home
      surfaces use `render_home`; `menu_summary` uses `render_summary`.
- [ ] Confirm `menu_stats`/`menu_status`/`menu_market` callbacks and `/stats`,
      `/status`, `/market` slash commands still exist (kept as power-user paths).
