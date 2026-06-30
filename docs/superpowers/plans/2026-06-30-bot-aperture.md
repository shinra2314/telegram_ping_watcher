# Bot Aperture Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add on-brand Aperture stickers at key bot moments, a home banner, and richer cards — visual + UX only, no monitoring/business-logic changes, existing nav and tests stay intact.

**Architecture:** New pure helpers in `chrome.py` (`bar`, `chip`) feed richer cards. A new `bot/stickers.py` registry + best-effort sender fires `.webp` stickers as standalone messages on events (win/giveaway/check/scan/ping/welcome). A new `scripts/generate_bot_stickers.py` renders the `.webp` set (Aperture reticle ring + Segoe color emoji + mono code); `generate_bot_assets.py` gains the missing `notify_check.png` + `notify_deadline.png`. A global `BOT_STICKERS_ENABLED` flag gates all sticker sends.

**Tech Stack:** Python 3.13, Telethon, Pillow 12.2 (webp+alpha verified), pydantic-settings, unittest (asyncio_mode=auto).

**Run tests:** `.\.venv\Scripts\python.exe -m pytest tests\ -q`

---

### Task 1: chrome `bar` + `chip` helpers

**Files:**
- Modify: `src/pulse_desk/bot/chrome.py`
- Test: `tests/test_bot_chrome.py`

- [ ] **Step 1: Add failing tests** to `tests/test_bot_chrome.py` (import line → `from pulse_desk.bot.chrome import bar, chip, dot, empty, header, kv`):

```python
class BarTests(unittest.TestCase):
    def test_full(self):
        self.assertEqual(bar(5, 5), "▰▰▰▰▰")
    def test_partial_rounds(self):
        self.assertEqual(bar(3, 5), "▰▰▰▱▱")
    def test_zero_total_all_empty(self):
        self.assertEqual(bar(0, 0), "▱▱▱▱▱")
    def test_clamps_over(self):
        self.assertEqual(bar(9, 5), "▰▰▰▰▰")
    def test_custom_width(self):
        self.assertEqual(len(bar(1, 4, width=8)), 8)

class ChipTests(unittest.TestCase):
    def test_wraps(self):
        self.assertEqual(chip("розыгрыш"), "「розыгрыш」")
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_bot_chrome.py -q` → ImportError on `bar`.

- [ ] **Step 3: Implement** in `chrome.py`:

```python
def bar(value: float, total: float, width: int = 5) -> str:
    """Unicode progress bar `▰▱`. Clamps; total<=0 → all empty."""
    if total <= 0:
        return "▱" * width
    filled = round(width * max(0.0, min(float(value), float(total))) / float(total))
    filled = max(0, min(width, filled))
    return "▰" * filled + "▱" * (width - filled)


def chip(label: str) -> str:
    """Uniform tag pill that renders on every Telegram client."""
    return f"「{label}」"
```

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_bot_chrome.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/chrome.py tests/test_bot_chrome.py
git commit -m "feat(bot): add chrome bar() + chip() visual helpers"
```

---

### Task 2: Home card — accounts bar + urgent marker

**Files:**
- Modify: `src/pulse_desk/bot/cards.py` (`home_card`)
- Test: `tests/test_bot_cards.py`

- [ ] **Step 1: Add failing test** to `tests/test_bot_cards.py`:

```python
def test_home_card_has_accounts_bar_and_urgent_flag(self):
    from pulse_desk.bot.cards import home_card
    out = home_card(role="admin", new_pings=4, urgent=2, accounts_online=3,
                    accounts_total=5, fresh_checks=1, last_scan="06-30 12:00")
    self.assertIn("▰", out)          # signal bar present
    self.assertIn("3/5", out)        # ratio shown
    self.assertIn("🔥", out)         # urgent>0 flagged
```

(If `test_bot_cards.py` has no class importing `home_card`, add the import at top and place the test in the existing card test class.)

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_bot_cards.py -q` → AssertionError (no `▰`).

- [ ] **Step 3: Implement** — edit `home_card` in `cards.py`. Add `from .chrome import bar` to the existing chrome import (`from .chrome import bar, empty, header, kv`). Replace the accounts line and the urgent value:

```python
def home_card(*, role, new_pings, urgent, accounts_online, accounts_total,
              fresh_checks, last_scan) -> str:
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    acc_bar = f"{bar(accounts_online, accounts_total)} {accounts_online}/{accounts_total}"
    urgent_str = f"🔥 {urgent}" if urgent else "0"
    return "\n".join([
        header("🛰", "Pulse Desk", badge),
        f"{kv('🆕', 'Новых пингов', new_pings)}   {kv('🎁', 'Срочных', urgent_str)}",
        f"🛰 Аккаунты: {acc_bar}   {kv('💸', 'Чеки', fresh_checks)}",
        kv("🔄", "Скан", last_scan),
        DIV,
        "Выберите раздел 👇",
    ])
```

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_bot_cards.py -q` → PASS. Also run full `pytest tests/ -q` to catch any old home_card assertion that changed; update it to match the new format if present.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py
git commit -m "feat(bot): home card shows accounts signal bar + urgent flag"
```

---

### Task 3: Admin home — quick Scan button

**Files:**
- Modify: `src/pulse_desk/bot/views.py` (`main_menu_buttons`)
- Test: `tests/test_bot_views.py`

- [ ] **Step 1: Add failing test** to `tests/test_bot_views.py`:

```python
def test_admin_menu_has_scan_quick_button(self):
    from pulse_desk.bot.views import main_menu_buttons
    flat = [b for row in main_menu_buttons("admin") for b in row]
    self.assertTrue(any(getattr(b, "data", b"") == b"menu_scan" for b in flat))

def test_viewer_menu_has_no_scan_button(self):
    from pulse_desk.bot.views import main_menu_buttons
    flat = [b for row in main_menu_buttons("viewer") for b in row]
    self.assertFalse(any(getattr(b, "data", b"") == b"menu_scan" for b in flat))
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_bot_views.py -q` → fail (no menu_scan).

- [ ] **Step 3: Implement** — in `views.py` `main_menu_buttons`, change the admin branch:

```python
    if role == "admin":
        rows.append([Button.inline("⚙️ Управление", b"adm:home"),
                     Button.inline("🔄 Скан", b"menu_scan")])
    else:
        rows.append([Button.inline("🔔 Мои уведомления", b"pf")])
```

(The `menu_scan` callback already exists and is admin-gated in `service.py` — no handler change.)

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_bot_views.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/views.py tests/test_bot_views.py
git commit -m "feat(bot): admin home gets a quick Scan button"
```

---

### Task 4: Card tags as chips (ping + giveaway)

**Files:**
- Modify: `src/pulse_desk/bot/cards.py` (`ping_card`)
- Test: `tests/test_bot_cards.py`

- [ ] **Step 1: Add failing test**:

```python
def test_ping_card_tags_are_chips(self):
    from pulse_desk.bot.cards import ping_card
    out = ping_card({"id": 7, "priority_label": "high", "is_giveaway": True,
                     "text": "x", "detected_at": "2026-06-30T10:00:00", "chat": "c"})
    self.assertIn("「🎁 розыгрыш」", out)
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_bot_cards.py -q` → fail.

- [ ] **Step 3: Implement** — in `cards.py` add `chip` to the chrome import (`from .chrome import bar, chip, empty, header, kv`) and rewrite the tag list in `ping_card`:

```python
    tags = [chip(f"🏷 {ping.get('priority_label') or 'normal'}")]
    if ping.get("is_giveaway"):
        tags.append(chip("🎁 розыгрыш"))
    if ping.get("is_win"):
        tags.append(chip("🏆 победа"))
    if ping.get("is_check"):
        tags.append(chip("💸 чек"))
```

Keep the join line `" ".join(tags)` (chips already carry their own borders; drop the old `"  ·  "` separator). Update the line in `ping_card` accordingly.

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_bot_cards.py -q` → PASS, then full `pytest tests/ -q`; fix any prior ping_card tag assertion.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/cards.py tests/test_bot_cards.py
git commit -m "feat(bot): render ping card tags as chips"
```

---

### Task 5: `BOT_STICKERS_ENABLED` config flag

**Files:**
- Modify: `src/pulse_desk/config.py`, `.env.example`
- Test: covered indirectly in Task 6

- [ ] **Step 1: Implement** — add to `Settings` in `config.py` (near `bot_admin_chats`):

```python
    bot_stickers_enabled: bool = Field(default=True, alias="BOT_STICKERS_ENABLED")
```

- [ ] **Step 2: Document** — add to `.env.example` under the bot section:

```env
# Send on-brand Aperture stickers on key bot events (win/scan/etc). Default on.
BOT_STICKERS_ENABLED=true
```

- [ ] **Step 3: Verify import** — `.\.venv\Scripts\python.exe -c "from pulse_desk.config import Settings; print(Settings().bot_stickers_enabled)"` → `True`.

- [ ] **Step 4: Commit**

```bash
git add src/pulse_desk/config.py .env.example
git commit -m "feat(config): BOT_STICKERS_ENABLED toggle"
```

---

### Task 6: `bot/stickers.py` — registry + best-effort sender

**Files:**
- Create: `src/pulse_desk/bot/stickers.py`
- Test: `tests/test_bot_stickers.py`

- [ ] **Step 1: Write failing test** `tests/test_bot_stickers.py`:

```python
from __future__ import annotations
import sys, asyncio
from pathlib import Path
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import unittest
from unittest.mock import patch
from pulse_desk.bot import stickers as st


class RegistryTests(unittest.TestCase):
    def test_known_alt(self):
        self.assertEqual(st.sticker_alt("win"), "🏆")
    def test_unknown_alt_fallback(self):
        self.assertEqual(st.sticker_alt("nope"), "✨")
    def test_path_none_for_unknown(self):
        self.assertIsNone(st.sticker_path("nope"))
    def test_path_none_when_file_absent(self):
        # 'win' is registered but the .webp may not be generated in CI
        p = st.sticker_path("win")
        self.assertTrue(p is None or p.endswith("win.webp"))


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_flag_skips(self):
        class FakeSettings: bot_stickers_enabled = False
        with patch.object(st, "_settings", FakeSettings()):
            sent = await st.send_sticker(object(), 123, "win")
        self.assertFalse(sent)

    async def test_no_client_returns_false(self):
        self.assertFalse(await st.send_sticker(None, 123, "win"))
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/test_bot_stickers.py -q` → ImportError.

- [ ] **Step 3: Implement** `src/pulse_desk/bot/stickers.py`:

```python
"""On-brand Aperture sticker delivery for the Telegram bot.

Registry + a best-effort sender. send_sticker never raises into a handler:
sticker fail → photo fallback → log + return False. A missing .webp resolves
to sticker_path()==None and the send is skipped.
"""
from __future__ import annotations

from typing import Optional

from ..app_ctx import BASE_DIR, logger, settings as _settings

STICKERS_DIR = BASE_DIR / "assets" / "bot" / "stickers"

# name -> (filename, alt-emoji shown in clients without sticker support)
_REGISTRY: dict[str, tuple[str, str]] = {
    "win": ("win.webp", "🏆"),
    "giveaway": ("giveaway.webp", "🎁"),
    "check": ("check.webp", "💸"),
    "scan": ("scan.webp", "🛰"),
    "scan_done": ("scan_done.webp", "✅"),
    "access_on": ("access_on.webp", "🟢"),
    "access_off": ("access_off.webp", "🔴"),
    "welcome": ("welcome.webp", "🛰"),
    "empty": ("empty.webp", "📭"),
    "error": ("error.webp", "⚠️"),
    "pong": ("pong.webp", "🏓"),
}


def sticker_alt(name: str) -> str:
    entry = _REGISTRY.get(name)
    return entry[1] if entry else "✨"


def sticker_path(name: str) -> Optional[str]:
    entry = _REGISTRY.get(name)
    if not entry:
        return None
    path = STICKERS_DIR / entry[0]
    return str(path) if path.exists() else None


async def send_sticker(client, peer, name: str) -> bool:
    """Best-effort: send the Aperture .webp as a sticker. Never raises."""
    if client is None or peer in (None, ""):
        return False
    if not getattr(_settings, "bot_stickers_enabled", True):
        return False
    path = sticker_path(name)
    if not path:
        return False
    try:
        from telethon.tl.types import DocumentAttributeSticker, InputStickerSetEmpty
        await client.send_file(
            peer, path,
            attributes=[DocumentAttributeSticker(alt=sticker_alt(name),
                                                 stickerset=InputStickerSetEmpty())],
            force_document=False,
        )
        return True
    except Exception:
        try:
            await client.send_file(peer, path)  # fall back to a plain image
            return True
        except Exception:
            logger.warning("Sticker send failed: %s", name, exc_info=True)
            return False
```

- [ ] **Step 4: Run, verify pass** — `pytest tests/test_bot_stickers.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/bot/stickers.py tests/test_bot_stickers.py
git commit -m "feat(bot): Aperture sticker registry + best-effort sender"
```

---

### Task 7: Sticker generator + banner fixes (manual render)

**Files:**
- Create: `scripts/generate_bot_stickers.py`
- Modify: `scripts/generate_bot_assets.py` (add `notify_check.png`, `notify_deadline.png`)

- [ ] **Step 1: Extend** `generate_bot_assets.py` `main()` — after the existing three `_notify(...)` calls add:

```python
    _notify("notify_check.png", CITRON, "ЧЕК", "PD·01 // SIGNAL LOCK · CHECK")
    _notify("notify_deadline.png", AMBER, "ДЕДЛАЙН", "PD·01 // SIGNAL LOCK · DEADLINE")
```

- [ ] **Step 2: Create** `scripts/generate_bot_stickers.py`:

```python
"""Generate the Pulse Desk bot sticker set (Aperture philosophy).

Static 512x512 .webp stickers, transparent background: an Aperture reticle
ring in the event's signal colour wrapped around a Segoe color-emoji glyph,
with a monospace unit code. Requires Pillow + C:\\Windows\\Fonts. Manual
(needs the Windows fonts); CI only imports it.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot" / "stickers"
FONTS = Path(r"C:\Windows\Fonts")
MONO = str(FONTS / "consola.ttf")
EMOJI = str(FONTS / "seguiemj.ttf")

SS = 2          # supersample then downscale to 512
S = 512

CITRON = (205, 255, 74)
CITRON_DEEP = (168, 216, 31)
CYAN = (78, 216, 255)
AMBER = (255, 178, 62)
RED = (255, 92, 92)
DIM = (150, 158, 150)


def _ring(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int,
          color: tuple[int, int, int], w: int) -> None:
    """Reticle ring: corner brackets, ring, measurement ticks, crosshair —
    no centre dot (the emoji sits there instead)."""
    be, bl = int(r * 1.7), int(r * 0.45)
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x, y = cx + sx * be, cy + sy * be
        d.line([(x, y), (x - sx * bl, y)], fill=color + (255,), width=w)
        d.line([(x, y), (x, y - sy * bl)], fill=color + (255,), width=w)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (255,), width=w)
    for i in range(72):
        ang = 2 * math.pi * i / 72
        t = r * 0.12 if i % 6 == 0 else r * 0.06
        a = 150 if i % 6 == 0 else 70
        d.line([(cx + (r - t) * math.cos(ang), cy + (r - t) * math.sin(ang)),
                (cx + r * math.cos(ang), cy + r * math.sin(ang))],
               fill=color + (a,), width=SS)
    gap, ext = int(r * 0.18), int(r * 0.4)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d.line([(cx + dx * (r + gap), cy + dy * (r + gap)),
                (cx + dx * (r + gap + ext), cy + dy * (r + gap + ext))],
               fill=color + (210,), width=w)


def make_sticker(name: str, color: tuple[int, int, int], glyph: str, code: str) -> None:
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    _ring(d, cx, cy, int(W * 0.30), color, 3 * SS)
    emoji = ImageFont.truetype(EMOJI, int(W * 0.30))
    d.text((cx, cy - int(W * 0.01)), glyph, font=emoji, embedded_color=True, anchor="mm")
    mono = ImageFont.truetype(MONO, int(W * 0.045))
    d.text((cx, int(W * 0.88)), code, font=mono, fill=color + (230,), anchor="mm")
    out = img.resize((S, S), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.webp", "WEBP", lossless=True, quality=100, method=6)
    print(f"  {name}.webp 512x512")


def main() -> None:
    print("Generating Pulse Desk bot stickers (Aperture):")
    make_sticker("win",        AMBER,       "\U0001F3C6", "PD·WIN")    # 🏆
    make_sticker("giveaway",   CITRON,      "\U0001F381", "PD·GIFT")   # 🎁
    make_sticker("check",      CITRON,      "\U0001F4B8", "PD·CHECK")  # 💸
    make_sticker("scan",       CITRON,      "\U0001F6F0", "PD·SCAN")   # 🛰
    make_sticker("scan_done",  CITRON_DEEP, "✅",     "PD·DONE")   # ✅
    make_sticker("access_on",  CITRON,      "\U0001F513", "PD·OPEN")   # 🔓
    make_sticker("access_off", RED,         "\U0001F512", "PD·LOCK")   # 🔒
    make_sticker("welcome",    CITRON,      "\U0001F4E1", "PD·01")     # 📡
    make_sticker("empty",      DIM,         "\U0001F4ED", "PD·NIL")    # 📭
    make_sticker("error",      RED,         "⚠",     "PD·ERR")    # ⚠
    make_sticker("pong",       CITRON,      "\U0001F3D3", "PD·PONG")   # 🏓
    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Compile-check** both scripts:

```bash
.\.venv\Scripts\python.exe -m py_compile scripts/generate_bot_stickers.py scripts/generate_bot_assets.py
```

- [ ] **Step 4: Render** (manual, Windows fonts):

```bash
.\.venv\Scripts\python.exe scripts/generate_bot_assets.py
.\.venv\Scripts\python.exe scripts/generate_bot_stickers.py
```

Expected: 8 PNGs in `assets/bot/` (now incl. `notify_check.png`, `notify_deadline.png`) and 11 `.webp` in `assets/bot/stickers/`. Eyeball a couple in an image viewer.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_bot_stickers.py scripts/generate_bot_assets.py assets/bot/
git commit -m "feat(assets): Aperture sticker set + notify_check/deadline banners"
```

---

### Task 8: Wire sticker hooks into handlers + notifications

**Files:**
- Modify: `src/pulse_desk/bot_notify.py`, `src/pulse_desk/bot/service.py`

No new tests (these are I/O glue around Telethon; `send_sticker` itself is tested in Task 6, gating verified there). Verify by `import main` + manual run.

- [ ] **Step 1: `bot_notify.py` — win + check stickers.** At top, add `from .bot.stickers import send_sticker`. In `send_check_notification`, just before `sent = await _send_bot_message(target, ...)`:

```python
        await send_sticker(state.bot_client, target, "check")
```

In `send_bot_notification`, just before `sent = await send_admin_bot_message(msg, ...)`:

```python
        if record.get("is_win"):
            await send_sticker(state.bot_client, ADMIN_ID, "win")
        elif record.get("is_giveaway") and record.get("priority_label") in ("critical", "high"):
            await send_sticker(state.bot_client, ADMIN_ID, "giveaway")
```

(Admin-only — the member broadcast above stays sticker-free, so friends are not spammed. All sends already sit behind the function's `enabled`/quiet gating.)

- [ ] **Step 2: `service.py` — import.** Add to the `.keyboards`/`.cards` import block:

```python
from .stickers import send_sticker
```

- [ ] **Step 3: `/ping` handler** — replace body:

```python
        async def ping_handler(event, role):
            await send_sticker(bot_client, event.chat_id, "pong")
            await event.respond("🏓 **Понг!** Бот на связи.")
```

- [ ] **Step 4: `/scan` handler** — after `asyncio.create_task(full_history_scan())`:

```python
            await send_sticker(bot_client, event.chat_id, "scan")
            await event.respond("🔄 **Сканирование истории запущено.**")
```

- [ ] **Step 5: `menu_scan` callback** — in the `if data == "menu_scan":` branch, in the else (scan started) path, before `await event.answer("Скан запущен")`:

```python
                    await send_sticker(bot_client, event.chat_id, "scan")
```

- [ ] **Step 6: `grant_access`** — before the `await event.respond("✅ **Доступ открыт!**...` call:

```python
            await send_sticker(bot_client, event.chat_id, "welcome")
```

- [ ] **Step 7: Verify** — `.\.venv\Scripts\python.exe -c "import main"` (imports every module + registers routers) → no error. Then full suite `pytest tests/ -q` → all green.

- [ ] **Step 8: Commit**

```bash
git add src/pulse_desk/bot_notify.py src/pulse_desk/bot/service.py
git commit -m "feat(bot): fire Aperture stickers on win/giveaway/check/scan/ping/welcome"
```

---

### Task 9: Docs + final verification

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `CLAUDE.md`** — in the `src/pulse_desk/` architecture map add a line under the bot area, and in `scripts/` note the new generator. Add near the bot/service description:

```
  bot/stickers.py   — Aperture sticker registry + best-effort sender (gated by
                      BOT_STICKERS_ENABLED); .webp set lives in assets/bot/stickers/
```

And extend the `scripts/` line to mention `generate_bot_stickers.py` (sticker .webp set).

- [ ] **Step 2: Full verification**:

```bash
.\.venv\Scripts\python.exe -m pytest tests\ -q
.\.venv\Scripts\python.exe -c "import main"
node --check static/js/app-core.js   # unchanged but cheap sanity
```

Expected: all tests pass; `import main` clean.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note bot sticker module + generator in CLAUDE.md"
```

---

## Self-Review

**Spec coverage:**
- Sticker assets → Task 7. Banner fix (`notify_check`) → Task 7. ✓
- `bot/stickers.py` sender → Task 6. ✓
- Event hooks (win/giveaway/check/scan/ping/welcome) → Task 8. ✓
- Home hero banner: `/start` already sends `welcome.png`; `/menu` parity is a one-line addition folded into Task 8 (mirror `/start`'s banner send in `menu_handler`). Added as Task 8 Step 6b below if missing. *(Deviation: home banner reuses existing `welcome.png`, not a new file — spec allowed.)*
- Home card bar + urgent → Task 2; admin Scan quick button → Task 3. ✓
- Chrome `bar`/`chip` + card chips → Tasks 1, 4. ✓
- Config flag + `.env.example` + CLAUDE.md → Tasks 5, 9. ✓
- New `test_bot_stickers.py` + chrome/cards/views test updates → Tasks 1–4, 6. ✓

**Deliberate simplifications (YAGNI):** `empty` and `error` stickers are generated + registered but not wired to handlers (would add duplicate board queries / over-instrument error paths); they stay available for later. Giveaway-deadline countdown bar dropped (would force `datetime.now` into the pure, unit-tested `cards.py`); chips deliver the card polish instead.

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `send_sticker(client, peer, name)`, `sticker_path(name)`, `sticker_alt(name)`, `bar(value, total, width=5)`, `chip(label)` used identically across Tasks 1, 4, 6, 8. `_settings` is the patched name in Task 6's test and the import alias in `stickers.py`.

**Task 8 Step 6b (folded in):** in `menu_handler` (the `/menu` command), mirror `/start`'s welcome-banner send so home is image-backed from both entry points:

```python
        async def menu_handler(event):
            role = await bot_role(event.sender_id)
            if role is None:
                await event.respond(await access_block_notice(event.sender_id) or locked_text)
                return
            banner = BOT_ASSETS_DIR / "welcome.png"
            home = await render_home(role)
            if banner.exists():
                try:
                    await event.respond(home, buttons=main_menu_buttons(role), file=str(banner))
                    return
                except Exception:
                    logger.warning("menu banner failed, text fallback", exc_info=True)
            await event.respond(home, buttons=main_menu_buttons(role))
```
