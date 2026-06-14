# Долги tab redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, recommended for this UI work) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rework the Pulse Desk «Долги» web tab into an urgency-first receivables queue (unclaimed prizes owed to me/team) with hybrid hero KPI, a "Сейчас горит" anti-`missed_reply` block, dense row-cards grouped by deadline, a people/dележ + source-risk lens, and a collapsed Obsidian/history section.

**Architecture:** Frontend-first, phased, no backend break. P0 ships the whole new look using data the API already returns (`deadline_state`, `deadline_seconds`, `priority_score`, `estimated_value`, `candidate_score`, `mentions`). P1+ adds exact backend aggregates, source reliability, bulk actions, and the Obsidian→History move.

**Tech Stack:** FastAPI + aiosqlite (backend), vanilla classic-script JS in `static/js/app-*.js` sharing one global scope, `static/app.css` (Bold Bento tokens), PWA `static/sw.js`. No bundler/TS.

**Testing approach:** JS has no unit harness — verify each changed JS file with `node --check static/js/<file>.js` and a manual tab check via `.\run_local.ps1`. Backend changes: `.\.venv\Scripts\python.exe -m pytest tests\test_*.py` + `.\.venv\Scripts\python.exe -c "import main"`. Bump `CACHE_NAME` in `static/sw.js` whenever shell assets change.

---

## File structure

| File | Responsibility | Phase |
|---|---|---|
| `static/app.css` | New component classes: `.dq-*` (queue rows), `.dseg` (segments), `.dhero` (KPI), `.dfocus`, `.dlens` | P0/P2 |
| `static/index.html` | Restructure `#debts` section markup (hero / segments / focus / queue+lens / history) | P0/P4 |
| `static/js/app-dashboard.js` | New renderers (`debtRow`, `groupDebtsByDeadline`, `renderDebtHero`, `renderDebtSegments`, `renderDebtFocus`, `renderDebtLens`); rewrite `renderDebts` | P0/P1/P2 |
| `static/js/app-main.js` | Event handlers: segment filter, group accordion, row claim/write, bulk, hotkeys, swipe | P0/P3 |
| `static/js/app-obsidian.js` | Obsidian panel relocates into collapsed History; feed `split_rule` to lens | P4 |
| `static/sw.js` | Bump `CACHE_NAME` | every shell change |
| `database/boards.py` | `get_debt_board().stats` aggregates + `source_reliability` per row | P1/P2 |
| `routers/boards.py` | Optional `POST /api/debts/bulk` | P3 |
| `tests/test_debt_board.py` | pytest for stats aggregates + reliability | P1/P2 |

State conventions (existing, reuse): `state.debtBoard`, `state.debtProfile`. Add: `state.debtSegment` ("all"|"overdue"|"today"|"week"|"risk"), `state.debtCollapsed` (Set of group keys), `state.debtSelection` (Set of ping ids).

---

## P0 — New look on existing data (frontend only)

### Task 1: CSS foundation for new debt components

**Files:** Modify `static/app.css` (append a new block near the existing `.debt-*` rules ~line 1279; do not delete legacy `.debt-*` yet — removed in Task 5 after the new markup is verified).

- [ ] **Step 1: Add new component classes**

Append to `static/app.css`:

```css
/* ---- Долги redesign (receivables queue) ---- */
.dhero { display: grid; grid-template-columns: 1.25fr 1fr 1fr; gap: 12px; margin-bottom: 14px; }
.dhero .tile { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 12px 14px; }
.dhero .tile.acc { background: var(--accent-soft); border-color: transparent; }
.dhero .lbl { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--muted-2); }
.dhero .big { font-family: var(--font-display); font-weight: 700; color: var(--ink); font-size: 25px; line-height: 1.05; margin-top: 5px; }
.dhero .big.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.dhero .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
.dseg-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.dseg { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 11px; border: 1px solid var(--line); border-radius: var(--radius-pill); background: var(--surface); font-size: 12px; font-weight: 600; color: var(--muted); cursor: pointer; }
.dseg.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.dseg .n { font-weight: 700; }
.dseg .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--bad); }
.dfocus-label { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--bad); }
.dfocus { background: var(--warn-soft); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 12px 14px; display: flex; align-items: center; gap: 14px; margin-bottom: 16px; box-shadow: inset 3px 0 0 var(--bad); }
.dfocus .cd { font-family: var(--font-mono); font-weight: 700; font-size: 24px; color: var(--bad); font-variant-numeric: tabular-nums; line-height: 1; }
.dfocus:empty { display: none; }
.dqueue-layout { display: grid; grid-template-columns: minmax(0,1fr) 200px; gap: 14px; align-items: start; }
.dgroup-head { display: flex; align-items: center; gap: 7px; margin: 10px 0 8px; font-family: var(--font-display); font-size: 13px; font-weight: 700; color: var(--ink); cursor: pointer; user-select: none; }
.dgroup-head.muted { color: var(--muted-2); }
.dq-row { display: flex; align-items: center; gap: 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 11px; padding: 8px 10px; margin-bottom: 7px; }
.dq-tab { width: 3px; align-self: stretch; border-radius: var(--radius-pill); background: var(--line); flex: none; }
.dq-tab.bad { background: var(--bad); } .dq-tab.warn { background: var(--warn); } .dq-tab.info { background: var(--info); }
.dq-cbx { width: 18px; height: 18px; border: 1.5px solid var(--muted-2); border-radius: 5px; flex: none; display: grid; place-items: center; color: #fff; cursor: pointer; }
.dq-cbx svg { width: 13px; height: 13px; opacity: 0; }
.dq-cbx.on { background: var(--accent); border-color: var(--accent); }
.dq-cbx.on svg { opacity: 1; }
.dq-main { flex: 1; min-width: 0; }
.dq-l1 { display: flex; align-items: center; gap: 7px; min-width: 0; }
.dq-who { font-weight: 700; color: var(--accent); font-size: 13px; white-space: nowrap; }
.dq-nm { font-weight: 600; color: var(--ink); font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dq-l2 { display: flex; align-items: center; gap: 8px; margin-top: 3px; flex-wrap: wrap; }
.dq-src { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--muted); }
.dq-pill { display: inline-flex; align-items: center; gap: 4px; height: 23px; padding: 0 9px; border-radius: var(--radius-pill); font-family: var(--font-mono); font-size: 11px; font-weight: 700; white-space: nowrap; font-variant-numeric: tabular-nums; }
.dq-pill.bad { background: var(--bad-soft); color: #b21e3a; } .dq-pill.warn { background: var(--warn-soft); color: #8a5606; } .dq-pill.info { background: var(--info-soft); color: #0c447c; }
.dq-money { font-family: var(--font-mono); font-weight: 700; font-size: 12.5px; color: var(--ink); font-variant-numeric: tabular-nums; white-space: nowrap; }
.dq-acts { display: flex; gap: 6px; flex: none; }
.dq-icb { width: 28px; height: 28px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); display: grid; place-items: center; color: var(--muted); cursor: pointer; }
.dq-icb.good { color: #fff; background: var(--good); border-color: var(--good); }
.dq-icb.acc { color: #fff; background: var(--accent); border-color: var(--accent); }
.dlens .card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-sm); padding: 11px 12px; margin-bottom: 12px; }
.dlens .ct { font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; color: var(--muted-2); margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
.dlens .lr { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 0; border-top: 1px solid var(--line); }
.dlens .lr:first-of-type { border-top: 0; }
.dhistory { display: flex; align-items: center; gap: 8px; background: var(--surface); border: 1px solid var(--line); border-radius: 11px; padding: 10px 12px; margin-top: 14px; font-size: 12.5px; color: var(--muted); cursor: pointer; }
@media (max-width: 900px) { .dhero { grid-template-columns: 1fr; } .dqueue-layout { grid-template-columns: 1fr; } .dlens { order: -1; } }
```

- [ ] **Step 2: Verify CSS loads (manual)** — start app `.\run_local.ps1`, open `/`, confirm no console errors and existing tab still renders (new classes are unused yet).

- [ ] **Step 3: Commit**

```bash
git add static/app.css
git commit -m "feat(debts): add redesign component CSS (queue/hero/focus/lens)"
```

### Task 2: Restructure `#debts` markup

**Files:** Modify `static/index.html:189-235`.

- [ ] **Step 1: Replace the `#debts` section body** with new containers (keep `id="debts"`, `id="debts-list"`, and the `#obsidian-panel` block intact — moved under history):

```html
<section id="debts" class="tabs debts-shell">
  <div class="debts-head">
    <div>
      <div class="kicker">Prize debts · что мне должны</div>
      <h2 class="section-title">Долги по призам</h2>
      <div class="section-meta">Очередь побед, которые ещё не выданы. Сверху — что горит.</div>
    </div>
    <div style="display:flex;gap:8px">
      <button class="btn" id="refresh-debts-btn"><i data-lucide="refresh-cw"></i>Обновить</button>
      <button class="btn primary" id="debts-bulk-btn" hidden><i data-lucide="circle-check"></i>Забрать выбранные · <span id="debts-bulk-count">0</span></button>
    </div>
  </div>
  <div class="dhero" id="debts-hero"></div>
  <div class="dseg-bar" id="debts-segments"></div>
  <div class="dfocus-label" id="debts-focus-label" hidden><i data-lucide="flame"></i>Сейчас горит</div>
  <div class="dfocus" id="debts-focus"></div>
  <div class="dqueue-layout">
    <div class="panel debts-main-panel" style="padding:14px">
      <div id="debts-list" class="board-list debts-list"></div>
    </div>
    <aside class="dlens" id="debts-lens"></aside>
  </div>
  <div class="dhistory" id="debts-history-toggle">
    <i data-lucide="history"></i>
    <span style="flex:1">Журнал и Obsidian — забранные, скам, синхронизация Долги.md</span>
    <i data-lucide="chevron-down"></i>
  </div>
  <div class="panel obsidian-debts-panel" id="obsidian-panel" hidden>
    <div class="ops-panel-head">
      <div>
        <div class="kicker">Obsidian · @Долги</div>
        <h2>Журнал розыгрышей из Obsidian</h2>
        <div class="section-meta" id="obsidian-sync-status">Загрузка…</div>
      </div>
      <button class="btn" id="refresh-obsidian-btn"><i data-lucide="refresh-cw"></i>Синхронизировать</button>
    </div>
    <div id="obsidian-config" class="obsidian-config"></div>
    <div id="obsidian-debts-progress" class="obsidian-progress"></div>
    <div id="obsidian-debt-groups" class="obsidian-groups"></div>
    <div id="obsidian-debts-list" class="board-list obsidian-list"></div>
  </div>
</section>
```

- [ ] **Step 2: Verify** — reload tab; layout containers present (empty), no JS error. `renderDebts` will be updated next; expect the list temporarily mis-rendered until Task 3.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat(debts): restructure tab markup (hero/segments/focus/queue+lens/history)"
```

### Task 3: New renderers in `app-dashboard.js`

**Files:** Modify `static/js/app-dashboard.js` — replace `renderDebtStats`, `renderDebtProfiles`, `debtItem`, `renderDebts` (lines ~325-455) with the queue model. Reuse helpers `deadlineMeta`, `giveawayStatusMeta`, `fmtDate`, `esc`, `safeJson`, `safeExternalLink`, `renderMentionChips`, `clamp`, `emptyState`.

- [ ] **Step 1: Add grouping + formatting helpers**

```javascript
function debtMoney(row) {
  const v = Number(row.estimated_value || 0);
  return v > 0 ? "≈ ₴" + v.toLocaleString("ru-RU") : "";
}
function debtGroupKey(row) {
  const s = row.deadline_state;
  if (s === "overdue") return "overdue";
  if (s === "today") return "today";
  if (s === "tomorrow" || s === "upcoming") return "week";
  return "missing";
}
const DEBT_GROUPS = [
  { key: "overdue", label: "Просрочено", cls: "bad" },
  { key: "today",   label: "Сегодня",   cls: "warn" },
  { key: "week",    label: "На неделе",  cls: "info" },
  { key: "missing", label: "Без дедлайна", cls: "" }
];
function groupDebtsByDeadline(rows) {
  const g = { overdue: [], today: [], week: [], missing: [] };
  rows.forEach(r => g[debtGroupKey(r)].push(r));
  return g;
}
function isHot(row) {
  if (row.deadline_state === "overdue") return true;
  const s = Number(row.deadline_seconds);
  return Number.isFinite(s) && s >= 0 && s <= 86400;
}
```

- [ ] **Step 2: Add `debtRow` (dense row-card)**

```javascript
function debtRow(row) {
  const mentions = safeJson(row.mentions, []);
  const who = mentions[0] ? "@" + String(mentions[0]).replace(/^@/, "") : "@—";
  const nm = (row.text || "").replace(/\s+/g, " ").trim().slice(0, 60) || "Победа";
  const source = row.chat || "источник";
  const dl = deadlineMeta(row);
  const pillCls = row.deadline_badge_class === "bad" ? "bad" : row.deadline_badge_class === "warn" ? "warn" : "info";
  const tab = row.deadline_state === "overdue" ? "bad" : (row.deadline_state === "today" ? "warn" : "info");
  const money = debtMoney(row);
  const checked = state.debtSelection && state.debtSelection.has(row.id);
  const admin = state.role === "admin";
  return `
    <div class="dq-row" data-debt-id="${row.id}" data-ping='${esc(JSON.stringify(row))}'>
      <span class="dq-tab ${tab}"></span>
      <span class="dq-cbx ${checked ? "on" : ""}" data-debt-select="${row.id}"><i data-lucide="check"></i></span>
      <div class="dq-main">
        <div class="dq-l1"><span class="dq-who">${esc(who)}</span><span class="dq-nm">${esc(nm)}</span></div>
        <div class="dq-l2"><span class="dq-src"><i data-lucide="radio"></i>${esc(source)}</span></div>
      </div>
      <span class="dq-pill ${pillCls}"><i data-lucide="clock"></i>${esc(dl.label)}</span>
      ${money ? `<span class="dq-money">${esc(money)}</span>` : ""}
      ${admin ? `<div class="dq-acts">
        <span class="dq-icb good" data-debt-status="claimed" data-id="${row.id}" title="Забрал"><i data-lucide="check"></i></span>
        <span class="dq-icb" data-debt-status="scam" data-id="${row.id}" title="Скам"><i data-lucide="shield-alert"></i></span>
      </div>` : ""}
    </div>`;
}
```

- [ ] **Step 3: Add hero / segments / focus / lens renderers**

```javascript
function renderDebtHero(data) {
  const rows = data.rows || [];
  const total = rows.length;
  const value = rows.reduce((a, r) => a + Number(r.estimated_value || 0), 0);
  const hot = rows.filter(isHot).sort((a, b) => (a.deadline_seconds ?? 9e9) - (b.deadline_seconds ?? 9e9))[0];
  const nearLabel = hot ? deadlineMeta(hot).label : "нет срочных";
  setHtmlIfChanged("debts-hero", `
    <div class="tile acc"><div class="lbl">К получению</div><div class="big">${total} ${pluralPrizes(total)}</div><div class="sub mono">${value > 0 ? "≈ ₴" + value.toLocaleString("ru-RU") + " оценкой" : "оценка недоступна"}</div></div>
    <div class="tile"><div class="lbl">Ближайший дедлайн</div><div class="big mono" style="color:var(--bad)">${esc(nearLabel)}</div><div class="sub">${hot ? esc((safeJson(hot.mentions, [])[0] ? "@" + hot.mentions[0] : "")) : ""}</div></div>
    <div class="tile"><div class="lbl">Новых</div><div class="big">${data.stats?.new ?? 0}</div><div class="sub">критичных ${data.stats?.critical ?? 0}</div></div>
  `);
}
function pluralPrizes(n) { const m = n % 10, h = n % 100; return (m === 1 && h !== 11) ? "приз" : (m >= 2 && m <= 4 && (h < 12 || h > 14)) ? "приза" : "призов"; }
function renderDebtSegments(groups) {
  const seg = state.debtSegment || "all";
  const items = [["all", "Все", groups.overdue.length + groups.today.length + groups.week.length + groups.missing.length, false]]
    .concat(DEBT_GROUPS.map(g => [g.key, g.label, groups[g.key].length, g.key === "overdue"]));
  setHtmlIfChanged("debts-segments", items.map(([k, l, n, dot]) =>
    `<span class="dseg ${seg === k ? "active" : ""}" data-debt-seg="${k}">${dot && n ? '<span class="dot"></span>' : ""}${esc(l)} <span class="n">${n}</span></span>`
  ).join(""));
}
function renderDebtFocus(rows) {
  const hot = rows.filter(isHot).sort((a, b) => (a.deadline_seconds ?? 9e9) - (b.deadline_seconds ?? 9e9)).slice(0, 1);
  $("debts-focus-label").hidden = hot.length === 0;
  if (!hot.length) { setHtmlIfChanged("debts-focus", ""); return; }
  const r = hot[0]; const m = safeJson(r.mentions, []);
  const link = safeExternalLink(r.link);
  setHtmlIfChanged("debts-focus", `
    <div style="text-align:center;flex:none"><div class="cd">${esc(deadlineMeta(r).label)}</div></div>
    <div style="flex:1;min-width:0"><div class="dq-l1"><span class="dq-who">@${esc(String(m[0] || "—"))}</span><span class="dq-nm">${esc((r.text || "").replace(/\s+/g, " ").trim().slice(0, 70))}</span></div></div>
    <div style="display:flex;gap:8px;flex:none">
      ${link ? `<a class="btn primary" href="${link}" target="_blank" rel="noopener" data-debt-write="${r.id}"><i data-lucide="send"></i>Написать</a>` : ""}
      ${state.role === "admin" ? `<button class="btn" data-debt-status="claimed" data-id="${r.id}"><i data-lucide="check"></i>Забрал</button>` : ""}
    </div>`);
}
function renderDebtLens(data) {
  const profiles = (data.profiles || []).filter(p => Number(p.count) > 0);
  const rows = profiles.map(p => `<div class="lr"><div><div class="ln" style="font-weight:600">@${esc(p.username)}</div></div><div style="text-align:right"><div class="dq-money">${p.count}</div></div></div>`).join("") || `<div class="lsub" style="color:var(--muted-2)">нет открытых</div>`;
  setHtmlIfChanged("debts-lens", `<div class="card"><div class="ct"><i data-lucide="users"></i>Люди</div>${rows}</div>`);
}
```

- [ ] **Step 4: Rewrite `renderDebts` + ensure state init**

```javascript
function renderDebts(data) {
  state.debtBoard = data;
  if (!state.debtSegment) state.debtSegment = "all";
  if (!state.debtSelection) state.debtSelection = new Set();
  if (!state.debtCollapsed) state.debtCollapsed = new Set(["week", "missing"]);
  const rows = data.rows || [];
  const groups = groupDebtsByDeadline(rows);
  renderDebtHero(data);
  renderDebtSegments(groups);
  renderDebtFocus(rows);
  renderDebtLens(data);
  const seg = state.debtSegment;
  const visible = DEBT_GROUPS.filter(g => seg === "all" || g.key === seg);
  const html = visible.map(g => {
    const list = groups[g.key];
    if (!list.length) return "";
    const collapsed = state.debtCollapsed.has(g.key);
    const body = collapsed ? "" : list.map(debtRow).join("");
    return `<div class="dgroup-head ${collapsed ? "muted" : ""}" data-debt-group="${g.key}"><i data-lucide="${collapsed ? "chevron-right" : "chevron-down"}"></i>${g.label} <span style="color:var(--${g.cls || "muted"})">· ${list.length}</span></div>${body}`;
  }).join("");
  setHtmlIfChanged("debts-list", html || emptyState("badge-check", "Долгов нет", "Все призы получены или ещё не появились в очереди."));
  if (window.lucide) lucide.createIcons();
}
```

- [ ] **Step 5: Verify** — `node --check static/js/app-dashboard.js` (Expected: no output). Then manual: tab shows hero, segments, focus (if any hot), grouped rows.

- [ ] **Step 6: Commit**

```bash
git add static/js/app-dashboard.js
git commit -m "feat(debts): urgency-first queue renderers (hero/segments/focus/rows/lens)"
```

### Task 4: Event handlers in `app-main.js`

**Files:** Modify `static/js/app-main.js` — the `#debts-list` listener (~line 192) and add segment/group/bulk/history listeners. Remove the `#debt-profile-tabs` listener (~line 186, element no longer exists).

- [ ] **Step 1: Replace debt handlers** with:

```javascript
$("debts-segments").addEventListener("click", (e) => {
  const seg = e.target.closest("[data-debt-seg]");
  if (!seg) return;
  state.debtSegment = seg.dataset.debtSeg;
  if (state.debtBoard) renderDebts(state.debtBoard);
});
$("debts-list").addEventListener("click", async (e) => {
  const head = e.target.closest("[data-debt-group]");
  if (head) { const k = head.dataset.debtGroup; state.debtCollapsed.has(k) ? state.debtCollapsed.delete(k) : state.debtCollapsed.add(k); renderDebts(state.debtBoard); return; }
  const sel = e.target.closest("[data-debt-select]");
  if (sel) { e.stopPropagation(); const id = Number(sel.dataset.debtSelect); state.debtSelection.has(id) ? state.debtSelection.delete(id) : state.debtSelection.add(id); renderDebts(state.debtBoard); updateBulkBtn(); return; }
  const statusBtn = e.target.closest("[data-debt-status]");
  if (statusBtn) {
    e.stopPropagation();
    const status = statusBtn.dataset.debtStatus;
    const mappedAction = { claimed: "claimed", scam: "scam", missed: "missed", missed_reply: "missed" }[status] || "missed";
    await api(`/api/pings/${statusBtn.dataset.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ giveaway_status: status, action_status: mappedAction }) });
    await loadDebts(); await loadDashboardSummary(); return;
  }
  if (e.target.closest("a")) return;
  const item = e.target.closest(".dq-row[data-ping]");
  if (item) openModal(JSON.parse(item.dataset.ping));
});
function updateBulkBtn() {
  const n = state.debtSelection ? state.debtSelection.size : 0;
  const btn = $("debts-bulk-btn"); if (!btn) return;
  btn.hidden = n === 0; $("debts-bulk-count").textContent = n;
}
$("debts-bulk-btn")?.addEventListener("click", async () => {
  const ids = Array.from(state.debtSelection || []);
  for (const id of ids) await api(`/api/pings/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ giveaway_status: "claimed", action_status: "claimed" }) });
  state.debtSelection.clear(); updateBulkBtn(); await loadDebts(); await loadDashboardSummary();
});
$("debts-history-toggle")?.addEventListener("click", () => { const p = $("obsidian-panel"); if (p) p.hidden = !p.hidden; });
```

> Note: confirm the existing status-update verb — current code uses `method: "PATCH"` at `app-main.js:198`. Match it exactly (the example above uses PATCH).

- [ ] **Step 2: Verify** — `node --check static/js/app-main.js`. Manual: click segments (filter), group headers (collapse), checkbox (select → bulk button), Забрал/Скам (row updates), history toggle (Obsidian panel shows/hides), row body (opens modal).

- [ ] **Step 3: Commit**

```bash
git add static/js/app-main.js
git commit -m "feat(debts): handlers for segments, accordions, selection, bulk, history"
```

### Task 5: Remove dead code + bump cache

**Files:** `static/app.css` (remove legacy `.debt-card-head/.debt-signal*/.debt-preview/.debt-footer/.debt-profile-tabs` blocks ~1286-1345 now unused), `static/js/app-dashboard.js` (delete leftover `renderDebtStats`/`renderDebtProfiles`/`debtItem`/`debtProfileRows` if fully replaced), `static/sw.js` (bump `CACHE_NAME`).

- [ ] **Step 1:** Grep for each removed symbol to confirm zero remaining references: `rg "renderDebtProfiles|debtItem\b|debt-profile-tabs|debt-signal"` → expect only definitions to delete.
- [ ] **Step 2:** Delete the unused CSS blocks and JS functions.
- [ ] **Step 3:** Bump `CACHE_NAME` in `static/sw.js` (e.g. `pulse-desk-vNN` → `vNN+1`).
- [ ] **Step 4: Verify** — `node --check static/js/app-dashboard.js`; hard-reload app; full manual pass of the tab.
- [ ] **Step 5: Commit**

```bash
git add static/app.css static/js/app-dashboard.js static/sw.js
git commit -m "refactor(debts): drop legacy card/profile code, bump sw cache"
```

---

## P1 — Exact hero aggregates (backend)

### Task 6: pytest for `get_debt_board` stats

**Files:** Create `tests/test_debt_board.py`.

- [ ] **Step 1: Write failing test** asserting new stat keys exist:

```python
import database
from database import get_debt_board

async def test_debt_board_stats_has_value_and_buckets(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    await database.init_db()
    board = await get_debt_board([], limit=50)
    stats = board["stats"]
    for key in ("value_pending", "overdue", "today", "soon", "claim_rate_30d"):
        assert key in stats
```

- [ ] **Step 2: Run** `.\.venv\Scripts\python.exe -m pytest tests\test_debt_board.py -v` → Expected FAIL (KeyError).

### Task 7: Implement aggregates

**Files:** Modify `database/boards.py` `get_debt_board()` return `stats` (~line 370).

- [ ] **Step 1:** Add to the `stats` dict: `value_pending` (Σ `estimated_value` over `debts`), `overdue`/`today`/`soon` (count rows by `deadline_state`), and `claim_rate_30d` (claimed / (claimed+missed+scam) over last 30 days via a small query on `pings`).
- [ ] **Step 2: Run** the pytest from Task 6 → Expected PASS. Then `.\.venv\Scripts\python.exe -c "import main"`.
- [ ] **Step 3: Commit** `feat(debts): backend hero aggregates (value_pending, buckets, claim_rate)`.

### Task 8: Wire hero to real stats

**Files:** `static/js/app-dashboard.js` `renderDebtHero`.

- [ ] **Step 1:** Use `data.stats.value_pending`, `data.stats.claim_rate_30d` for the third tile (`% забрано / 30д`) instead of the client `new`/`critical` placeholder.
- [ ] **Step 2: Verify** `node --check` + manual. **Commit.**

---

## P2 — Source-risk (reliability)

### Task 9: reliability per row + test
**Files:** `database/boards.py` (join/lookup channel source score → `source_reliability` on each debt row), `tests/test_debt_board.py` (+case). Implement, run pytest, `import main`. Commit.

### Task 10: risk UI
**Files:** `app-dashboard.js` (`debtRow` risk badge when `source_reliability < 0.5`; add `risk` segment filtering those rows), `app.css` (`.dq-risk`), `renderDebtLens` (+ "Надёжность источников" card with bars). `node --check` + manual. Commit.

---

## P3 — Power actions

### Task 11: bulk endpoint (optional)
**Files:** `routers/boards.py` add `POST /api/debts/bulk` (ids + status) OR keep client loop from P0. If endpoint: pytest the route. Commit.

### Task 12: swipe + hotkeys
**Files:** `app-main.js` — keydown (J/K move highlight, C=claimed on highlighted, W=write), touch swipe on `.dq-row` (left=claimed, right=snooze). `node --check` + manual. Commit.

---

## P4 — Obsidian → History + dележ lens

### Task 13: relocate + feed split_rule
**Files:** `app-obsidian.js` (panel already inside collapsed `#obsidian-panel`; ensure `loadObsidianDebts` still wires; gate load until history expanded), `app-dashboard.js` `renderDebtLens` (merge `state.obsidianBoard.groups[].split_rule` into the people card as "дележ 50/50"). `node --check` + manual. Commit.

---

## Self-review notes

- **Spec coverage:** P0 = arch §4 + visual §5 + queue/hero/focus/lens; P1 = §6 aggregates; P2 = §5 risk state + §7 risk auto; P3 = §7 quick actions/bulk/hotkeys; P4 = §4 history + §7 dележ. Metrics (§9) validated post-rollout, not code tasks.
- **Verb consistency:** status updates use `PATCH /api/pings/{id}` (matches existing `app-main.js:198`); confirm at execution and keep identical across debtRow/focus/bulk.
- **No new types** beyond `state.debtSegment/debtSelection/debtCollapsed` (declared in File structure) and the named render functions (all defined in Task 3).
- **Risk:** DOM IDs consumed by handlers (`debts-list`, `debts-segments`, `debts-hero`, `debts-focus`, `debts-lens`, `obsidian-panel`) are created in Task 2 before Task 3/4 use them.
