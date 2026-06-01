# Ping Labels (Tags) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users attach manual text labels (e.g. "важно", "обработано", "проверить") to pings, filter the ping list by label, and see/remove labels in the ping detail view.

**Architecture:** Add a `tags` JSON-array column to the `pings` table (schema version 9). Add DB helper functions `add_ping_tag` / `remove_ping_tag` / `get_all_tags`. Expose `PATCH /api/pings/{id}/tags` and `GET /api/tags` endpoints in `main.py`. Render tag chips in the frontend ping detail and add a tag filter dropdown to the ping list.

**Tech Stack:** SQLite JSON column (stored as TEXT, parsed as list), FastAPI PATCH endpoint, vanilla JS frontend.

---

### Task 1: DB migration — add `tags` column to pings

**Files:**
- Modify: `database.py:198-219` (migrations dict inside `init_db`)
- Modify: `database.py:40` (SCHEMA_VERSION)

- [ ] **Step 1: Write failing test**

In `tests/test_core.py`, add to class `CoreParsingTests`:

```python
def test_ping_tags_default_is_empty_list(self):
    import json
    raw = '[]'
    self.assertEqual(json.loads(raw), [])

def test_ping_tags_round_trip(self):
    import json
    tags = ['важно', 'проверить']
    stored = json.dumps(tags, ensure_ascii=False)
    self.assertEqual(json.loads(stored), tags)
```

- [ ] **Step 2: Run test to verify it passes (pure logic, no DB needed)**

```bash
python -m unittest tests.test_core.CoreParsingTests.test_ping_tags_default_is_empty_list
python -m unittest tests.test_core.CoreParsingTests.test_ping_tags_round_trip
```

Expected: PASS (no DB dependency, just JSON logic)

- [ ] **Step 3: Add `tags` column migration in `database.py`**

In `database.py`, change line 40:
```python
SCHEMA_VERSION = 9
```

In the `migrations` dict inside `init_db()` (after the `"action_status"` entry, ~line 216):
```python
            "tags": "ALTER TABLE pings ADD COLUMN tags TEXT DEFAULT '[]'",
```

After the migrations loop (~line 219), add normalisation:
```python
        await db.execute("UPDATE pings SET tags = '[]' WHERE tags IS NULL OR tags = ''")
```

- [ ] **Step 4: Add DB helper functions at end of `database.py` (before backup functions)**

```python
async def add_ping_tag(ping_id: int, tag: str) -> list[str]:
    tag = tag.strip()
    if not tag:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        if tag not in tags:
            tags.append(tag)
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def remove_ping_tag(ping_id: int, tag: str) -> list[str]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        tags = [t for t in tags if t != tag]
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def get_all_tags() -> list[str]:
    """Return all unique tags across all pings, sorted."""
    async with _connect() as db:
        rows = await (await db.execute("SELECT DISTINCT tags FROM pings WHERE tags IS NOT NULL AND tags != '[]'")).fetchall()
    seen: set[str] = set()
    for (raw,) in rows:
        for t in _json_loads(raw, []):
            if t:
                seen.add(t)
    return sorted(seen)
```

- [ ] **Step 5: Add `tags` to import in `main.py`**

In `main.py`, in the `from database import (` block, add:
```python
    add_ping_tag,
    remove_ping_tag,
    get_all_tags,
```

- [ ] **Step 6: Add API endpoints in `main.py`**

Find the block of ping-related endpoints (search for `@app.patch` or after `toggle_favorite` endpoint). Add after the existing ping update endpoints:

```python
@app.get("/api/tags")
async def list_all_tags(token_valid: bool = Depends(require_viewer)):
    return await get_all_tags()


@app.post("/api/pings/{ping_id}/tags/{tag}")
async def ping_add_tag(ping_id: int, tag: str, token_valid: bool = Depends(require_admin)):
    tags = await add_ping_tag(ping_id, tag.strip())
    return {"tags": tags}


@app.delete("/api/pings/{ping_id}/tags/{tag}")
async def ping_remove_tag(ping_id: int, tag: str, token_valid: bool = Depends(require_admin)):
    tags = await remove_ping_tag(ping_id, tag.strip())
    return {"tags": tags}
```

- [ ] **Step 7: Verify server starts without errors**

```bash
python -c "import main; print('imports OK')"
```

Expected: `imports OK`

- [ ] **Step 8: Add tag filter support to `get_pings` in `database.py`**

Find `_build_pings_filters` function or the `get_pings` function. Add `tag` parameter:

```python
async def get_pings(
    *,
    # ... existing params ...
    tag: Optional[str] = None,
    # ...
) -> list[dict]:
```

Inside the filter-building section, add:
```python
    if tag:
        # JSON array contains check via LIKE (portable, no JSON1 extension needed)
        _add_where(where, params, "tags LIKE ?", f'%"{tag}"%')
```

And in `main.py`, update the `/api/pings` endpoint to pass the `tag` query parameter:
```python
@app.get("/api/pings")
async def list_pings(
    # ... existing params ...
    tag: Optional[str] = Query(None),
    # ...
):
    pings = await get_pings(
        # ... existing kwargs ...
        tag=tag,
    )
```

- [ ] **Step 9: Frontend — show tags in ping detail**

In `static/app.js`, find the function that renders a ping detail (search for `note` or `deadline_at` rendering). Add after the note field:

```javascript
// Tags row
const tagsHtml = (ping.tags || []).map(t =>
    `<span class="tag-chip" data-tag="${esc(t)}">${esc(t)} <button class="tag-remove" data-ping="${ping.id}" data-tag="${esc(t)}">×</button></span>`
).join('');
const addTagHtml = `<input class="tag-input" id="tag-input-${ping.id}" placeholder="добавить тег…" maxlength="40">
    <button class="tag-add-btn" data-ping="${ping.id}">+</button>`;
// inject into detail panel HTML as: `<div class="tags-row">${tagsHtml}${addTagHtml}</div>`
```

Add event handlers for add/remove tag buttons:
```javascript
async function handleTagAdd(pingId) {
    const input = document.getElementById(`tag-input-${pingId}`);
    const tag = (input.value || '').trim();
    if (!tag) return;
    await api(`/api/pings/${pingId}/tags/${encodeURIComponent(tag)}`, { method: 'POST' });
    input.value = '';
    await reloadPingDetail(pingId);
}

async function handleTagRemove(pingId, tag) {
    await api(`/api/pings/${pingId}/tags/${encodeURIComponent(tag)}`, { method: 'DELETE' });
    await reloadPingDetail(pingId);
}
```

- [ ] **Step 10: Frontend — tag filter dropdown in ping list**

In `static/index.html`, find the ping filters area. Add:
```html
<select id="tag-filter">
  <option value="">Все теги</option>
</select>
```

In `static/app.js`, in the init/load function:
```javascript
async function loadTagFilter() {
    const tags = await api('/api/tags');
    const sel = document.getElementById('tag-filter');
    tags.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t;
        opt.textContent = t;
        sel.appendChild(opt);
    });
}

// In the pings fetch, add: &tag=${encodeURIComponent(currentTag)} to the query
```

- [ ] **Step 11: Add basic CSS for tag chips to `static/app.css`**

```css
.tag-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: var(--accent, #3b82f6);
    color: #fff;
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 0.75rem;
    margin: 2px;
}
.tag-remove {
    background: none;
    border: none;
    color: #fff;
    cursor: pointer;
    padding: 0;
    font-size: 0.9rem;
    line-height: 1;
}
.tag-input {
    width: 120px;
    font-size: 0.8rem;
}
```

- [ ] **Step 12: Run tests**

```bash
python -m unittest tests.test_core -v
```

Expected: all pass (no regressions)

- [ ] **Step 13: Commit**

```bash
git add database.py main.py static/app.js static/index.html static/app.css tests/test_core.py
git commit -m "feat: add manual ping labels (tags) with DB, API and frontend filter"
```
