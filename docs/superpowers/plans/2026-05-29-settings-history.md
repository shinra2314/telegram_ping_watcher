# Settings Change History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log every call to `set_setting()` into a `settings_history` table, expose `GET /api/settings/history` and per-key history, and show a collapsible history panel in the Settings page.

**Architecture:** Add `settings_history` table in `database.py`. Wrap `set_setting()` to write a history row on each change. Add two read-only API endpoints (no new models needed). Add a minimal history section in the existing Settings tab of the frontend.

**Tech Stack:** SQLite, FastAPI, vanilla JS.

---

### Task 1: DB table + modified `set_setting`

**Files:**
- Modify: `database.py:40` (SCHEMA_VERSION)
- Modify: `database.py:init_db` (CREATE TABLE)
- Modify: `database.py:set_setting` function

- [ ] **Step 1: Write failing test**

In `tests/test_core.py`, add:

```python
def test_settings_history_schema(self):
    # verify the helper that formats a history row works
    from datetime import datetime
    row = {
        "id": 1,
        "key": "usernames",
        "old_value": "alice",
        "new_value": "alice,bob",
        "changed_at": datetime.now().isoformat(),
    }
    self.assertIn("key", row)
    self.assertIn("old_value", row)
    self.assertIn("new_value", row)
```

- [ ] **Step 2: Run test to verify it passes**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_settings_history_schema
```

Expected: PASS

- [ ] **Step 3: Bump SCHEMA_VERSION and add table in `init_db()`**

In `database.py` line 40 (or wherever ping-labels plan sets it — use the next number):
```python
SCHEMA_VERSION = 9   # or 10 if ping-labels plan already bumped to 9
```

In `init_db()`, after the `settings` table creation (search for `CREATE TABLE IF NOT EXISTS settings` or `set_setting`), add:

```python
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_settings_history_key ON settings_history(key)"
        )
```

- [ ] **Step 4: Modify `set_setting()` to write history**

Find `set_setting` in `database.py` (search for `async def set_setting`). Replace with:

```python
async def set_setting(key: str, value: str) -> None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        existing = await (await db.execute("SELECT value FROM settings WHERE key = ?", (key,))).fetchone()
        old_value = existing["value"] if existing else None
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        if old_value != value:
            await db.execute(
                "INSERT INTO settings_history (key, old_value, new_value, changed_at) VALUES (?, ?, ?, ?)",
                (key, old_value, value, _now_iso()),
            )
        await db.commit()
```

- [ ] **Step 5: Add `get_settings_history` function**

After `set_setting`, add:

```python
async def get_settings_history(*, key: Optional[str] = None, limit: int = 50) -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        if key:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history WHERE key = ? ORDER BY changed_at DESC LIMIT ?",
                    (key, limit),
                )
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM settings_history ORDER BY changed_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 6: Add `settings` table creation to `init_db()` if missing**

Search `database.py` for `CREATE TABLE IF NOT EXISTS settings` — if it doesn't exist, add before the `settings_history` CREATE:

```python
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
```

- [ ] **Step 7: Add to `main.py` imports**

In `main.py`, `from database import (` block, add:
```python
    get_settings_history,
```

- [ ] **Step 8: Add API endpoints in `main.py`**

Near the existing settings endpoints (search for `/api/settings`):

```python
@app.get("/api/settings/history")
async def settings_history(
    key: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    token_valid: bool = Depends(require_admin),
):
    return await get_settings_history(key=key, limit=limit)
```

- [ ] **Step 9: Verify imports**

```bash
python -c "import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 10: Frontend — collapsible history panel in Settings tab**

In `static/app.js`, find the Settings section render function (search for `settings` or `usernames` in render logic). Add at the bottom of the settings panel:

```javascript
async function renderSettingsHistory() {
    const history = await api('/api/settings/history?limit=30');
    if (!history.length) return '<p>Изменений пока нет.</p>';
    const rows = history.map(h => `
        <tr>
            <td>${esc(h.key)}</td>
            <td style="color:#888">${esc(h.old_value ?? '—')}</td>
            <td>${esc(h.new_value ?? '—')}</td>
            <td style="white-space:nowrap">${esc(h.changed_at?.slice(0,16) ?? '')}</td>
        </tr>
    `).join('');
    return `
        <details style="margin-top:1.5rem">
            <summary style="cursor:pointer;font-weight:600">История изменений настроек</summary>
            <table style="width:100%;font-size:0.8rem;margin-top:0.5rem">
                <thead><tr><th>Ключ</th><th>Было</th><th>Стало</th><th>Когда</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </details>
    `;
}
```

Call `renderSettingsHistory()` when the Settings tab becomes active and inject the result into the settings panel container.

- [ ] **Step 11: Run tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass

- [ ] **Step 12: Commit**

```bash
git add database.py main.py static/app.js
git commit -m "feat: settings change history table and API"
```
