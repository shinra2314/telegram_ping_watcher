# Telegram Digest Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Send digest to Telegram" button that posts a formatted summary of recent pings/wins to the admin's Telegram account via the existing bot client.

**Architecture:** Add `send_telegram_digest(bot_client, admin_id, pings)` helper in `src/pulse_desk/`. Expose `POST /api/export/telegram-digest` endpoint that reads recent pings from DB and dispatches the message. Frontend adds a button in the Analytics or Dashboard panel. Falls back gracefully if bot is not configured.

**Tech Stack:** Telethon `bot_client.send_message()`, existing `AppState.bot_client` and `AppState.bot_id`, `get_pings()` from database.

---

### Task 1: Digest formatter

**Files:**
- Create: `src/pulse_desk/digest.py`

- [ ] **Step 1: Write failing test**

In `tests/test_core.py`, add:

```python
def test_digest_formats_empty(self):
    from pulse_desk.digest import format_digest
    result = format_digest([])
    self.assertIn("Нет", result)

def test_digest_formats_pings(self):
    from pulse_desk.digest import format_digest
    pings = [
        {"chat": "channel1", "text": "Победитель @alice!", "is_win": True, "link": "https://t.me/c/1/1", "date": "2026-05-29T12:00:00"},
        {"chat": "channel2", "text": "Просто упоминание", "is_win": False, "link": "https://t.me/c/2/2", "date": "2026-05-29T11:00:00"},
    ]
    result = format_digest(pings)
    self.assertIn("channel1", result)
    self.assertIn("🏆", result)
    self.assertIn("2", result)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_digest_formats_empty
python -m unittest tests.test_core.CoreParsingTests.test_digest_formats_pings
```

Expected: ImportError / FAIL

- [ ] **Step 3: Create `src/pulse_desk/digest.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Any


def format_digest(pings: list[dict[str, Any]], *, period_label: str = "за последние 24 ч") -> str:
    if not pings:
        return f"📭 Нет новых пингов {period_label}."

    wins = [p for p in pings if p.get("is_win")]
    mentions = [p for p in pings if not p.get("is_win")]

    lines: list[str] = [f"📊 *Pulse Desk Digest* — {period_label}"]
    lines.append(f"Всего: {len(pings)} | 🏆 Побед: {len(wins)} | 📌 Упоминаний: {len(mentions)}")
    lines.append("")

    if wins:
        lines.append("🏆 *Победы:*")
        for p in wins[:10]:
            chat = p.get("chat") or "?"
            link = p.get("link") or ""
            text = (p.get("text") or "")[:80].replace("\n", " ")
            lines.append(f"• [{chat}]({link}) — {text}")
        lines.append("")

    if mentions:
        lines.append("📌 *Упоминания:*")
        for p in mentions[:10]:
            chat = p.get("chat") or "?"
            link = p.get("link") or ""
            lines.append(f"• [{chat}]({link})")

    lines.append(f"\n_Сгенерировано {datetime.now().strftime('%d.%m.%Y %H:%M')}_")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — should pass now**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_digest_formats_empty
python -m unittest tests.test_core.CoreParsingTests.test_digest_formats_pings
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pulse_desk/digest.py tests/test_core.py
git commit -m "feat: add digest formatter module with tests"
```

---

### Task 2: API endpoint and frontend button

**Files:**
- Modify: `main.py` (import + endpoint)
- Modify: `static/app.js` (button + handler)
- Modify: `static/index.html` (button placement)

- [ ] **Step 1: Add import in `main.py`**

In `main.py`, after the other `from pulse_desk` imports:
```python
from pulse_desk.digest import format_digest
```

- [ ] **Step 2: Add endpoint in `main.py`**

After the existing export endpoints (search for `/api/export`):

```python
@app.post("/api/export/telegram-digest")
async def export_telegram_digest(
    hours: int = Query(24, ge=1, le=168),
    token_valid: bool = Depends(require_admin),
):
    if not state.bot_client or not settings.admin_id:
        raise HTTPException(status_code=503, detail="Бот не настроен: нужны TELEGRAM_BOT_TOKEN и ADMIN_ID в .env")

    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    pings = await get_pings(limit=200, date_from=since)

    text = format_digest(pings, period_label=f"за {hours} ч")
    try:
        await state.bot_client.send_message(
            settings.admin_id,
            text,
            parse_mode="md",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка отправки: {exc}") from exc

    return {"ok": True, "pings_count": len(pings)}
```

> **Note:** Check that `get_pings` accepts a `date_from` parameter. If not, use `limit=200` without date filter and let `format_digest` handle the slice — or add `date_from` filter to `get_pings` first (see Step 3).

- [ ] **Step 3: Verify `get_pings` accepts `date_from` (check `database.py`)**

Search `database.py` for `async def get_pings`. If `date_from` is not in the signature, add it:

```python
async def get_pings(
    *,
    # ... existing params ...
    date_from: Optional[str] = None,
    # ...
) -> list[dict]:
```

And in `_build_pings_filters` or inline:
```python
    if date_from:
        _add_where(where, params, "p.detected_at >= ?", date_from)
```

- [ ] **Step 4: Frontend button in dashboard/analytics panel**

In `static/index.html`, in the Analytics or Dashboard section, add:

```html
<button id="btn-tg-digest" class="btn-secondary" title="Отправить дайджест в Telegram">
  📤 Дайджест в Telegram
</button>
```

In `static/app.js`, add handler:

```javascript
document.getElementById('btn-tg-digest')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-tg-digest');
    btn.disabled = true;
    btn.textContent = 'Отправляю…';
    try {
        const res = await api('/api/export/telegram-digest?hours=24', { method: 'POST' });
        btn.textContent = `✅ Отправлено (${res.pings_count} пингов)`;
    } catch (e) {
        btn.textContent = '❌ Ошибка';
        console.error(e);
    }
    setTimeout(() => { btn.disabled = false; btn.textContent = '📤 Дайджест в Telegram'; }, 4000);
});
```

- [ ] **Step 5: Verify imports**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Run tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add main.py static/app.js static/index.html
git commit -m "feat: send Telegram digest via bot with frontend button"
```
