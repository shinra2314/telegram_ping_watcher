/* Pulse Desk frontend — app-dashboard.js: dashboard summary, analytics, tasks, debts, market charts.
   Classic script (no modules): top-level bindings are shared across the app-*.js files,
   which must load in the order set in index.html. Split from the former app.js. */

    function safeJson(value, fallback) {
      try { return typeof value === "string" ? JSON.parse(value) : value || fallback; } catch { return fallback; }
    }

    function formatCount(value) {
      return Number(value || 0).toLocaleString("ru-RU");
    }

    function percentOf(part, total) {
      const top = Number(part || 0);
      const base = Number(total || 0);
      return base > 0 ? Math.max(0, Math.min(100, (top / base) * 100)) : 0;
    }

    function pctLabel(value, digits = 1) {
      return `${Number(value || 0).toFixed(digits)}%`;
    }

    function toneByNumber(value, goodAt = 0) {
      const num = Number(value || 0);
      if (num > goodAt) return "good";
      if (num < goodAt) return "bad";
      return "info";
    }

    function renderBriefTile(label, value, hint, icon, tone = "info") {
      return `
        <div class="brief-tile ${tone}">
          <span><i data-lucide="${icon}"></i>${esc(label)}</span>
          <strong>${esc(value ?? "")}</strong>
          <em>${esc(hint || "")}</em>
        </div>
      `;
    }

    function renderProgressItem(label, value, hint, percent, tone = "info") {
      return `
        <div class="progress-item ${tone}">
          <div class="progress-head"><strong>${esc(label)}</strong><span>${esc(value ?? "")}</span></div>
          <div class="mini-bar"><i style="width:${Math.max(3, Math.min(100, Number(percent || 0)))}%"></i></div>
          <small>${esc(hint || "")}</small>
        </div>
      `;
    }

    function renderAnalyticsBrief(data, detailed) {
      const total = Number(data.total_pings || 0);
      const newCount = Number(data.new_pings || 0);
      const important = Number(data.important || 0);
      const wins = Number(data.wins || 0);
      const resolved = Number(data.resolved || 0);
      const channels = Number(data.total_channels || data.channel_chats_total || 0);
      const resolutionRate = percentOf(resolved, total);
      const focusValue = important || newCount;
      const focusLabel = important ? "важных" : "новых";
      const joined = Number(detailed?.conversion?.joined || 0);
      const giveaways = Number(data.giveaways || detailed?.conversion?.total || 0);
      setHtmlIfChanged("analytics-brief", `
        ${renderBriefTile("Фокус", `${formatCount(focusValue)} ${focusLabel}`, `${formatCount(newCount)} новых в ленте`, important ? "flame" : "sparkles", important ? "warn" : "info")}
        ${renderBriefTile("Результат", `${pctLabel(data.win_rate)} win rate`, `${formatCount(wins)} побед из ${formatCount(total)}`, "trophy", wins ? "good" : "info")}
        ${renderBriefTile("Покрытие", `${formatCount(channels)} каналов`, `${formatCount(data.accounts_online)} аккаунтов онлайн`, "radio-tower", channels ? "good" : "warn")}
        ${renderBriefTile("Разбор", `${pctLabel(resolutionRate)} решено`, `${formatCount(resolved)} закрыто, ${formatCount(data.favorites)} в избранном`, "check-check", resolutionRate >= 50 ? "good" : "warn")}
        ${renderBriefTile("Giveaway", `${pctLabel(data.giveaway_rate)} ленты`, `${formatCount(joined)} авто-вступлений из ${formatCount(giveaways)}`, "gift", giveaways ? "info" : "warn")}
      `);
    }

    function renderAnalyticsFlow(data, detailed) {
      const total = Number(data.total_pings || 0);
      const flow = [
        ["Новые", data.new_pings, "не разобрано", percentOf(data.new_pings, total), "sparkles", Number(data.new_pings || 0) ? "warn" : "good"],
        ["Важные", data.important, "высокий приоритет", percentOf(data.important, total), "flame", Number(data.important || 0) ? "bad" : "info"],
        ["Победы", data.wins, "похожие на win", percentOf(data.wins, total), "trophy", Number(data.wins || 0) ? "good" : "info"],
        ["Розыгрыши", data.giveaways, "giveaway поток", percentOf(data.giveaways, total), "gift", Number(data.giveaways || 0) ? "info" : "warn"],
        ["Решено", data.resolved, "закрытый хвост", percentOf(data.resolved, total), "check-circle", percentOf(data.resolved, total) >= 50 ? "good" : "warn"]
      ];
      const dailyQuality = (detailed.daily_quality || []).slice(0, 7);
      const dayTotal = dailyQuality.reduce((sum, row) => sum + Number(row.total || 0), 0);
      const dayWins = dailyQuality.reduce((sum, row) => sum + Number(row.wins || 0), 0);
      setHtmlIfChanged("analytics-flow", `
        <div class="flow-card flow-summary">
          <span class="metric-icon"><i data-lucide="radar"></i></span>
          <div><strong>${formatCount(total)} упоминаний в базе</strong><span>За последние 7 дней: ${formatCount(dayTotal)} записей, ${formatCount(dayWins)} побед.</span></div>
        </div>
        ${flow.map(([label, value, hint, percent, icon, tone]) => `
          <div class="flow-card ${tone}">
            <span class="metric-icon"><i data-lucide="${icon}"></i></span>
            <div><strong>${formatCount(value)}</strong><span>${esc(label)} · ${esc(hint)}</span></div>
            <div class="mini-bar"><i style="width:${Math.max(3, Math.min(100, Number(percent || 0)))}%"></i></div>
          </div>
        `).join("")}
      `);
    }

    function renderRankList(id, rows, options = {}) {
      const el = $(id);
      if (!el) return;
      const values = rows || [];
      const max = Math.max(...values.map(row => Number(options.value ? options.value(row) : row.count || 0)), 1);
      setHtmlIfChanged(el, values.length ? values.map((row, index) => {
        const value = Number(options.value ? options.value(row) : row.count || 0);
        const title = options.title ? options.title(row) : row.title;
        const meta = options.meta ? options.meta(row) : "";
        const badges = options.badges ? options.badges(row) : "";
        const icon = options.icon ? options.icon(row) : "bar-chart-3";
        return `
          <div class="rank-item">
            <span class="rank-index">${index + 1}</span>
            <span class="metric-icon"><i data-lucide="${icon}"></i></span>
            <div class="rank-body">
              <div class="rank-title"><strong>${esc(title || "неизвестно")}</strong><span>${formatCount(value)}</span></div>
              <div class="mini-bar"><i style="width:${Math.max(4, value / max * 100)}%"></i></div>
              <div class="rank-meta">${meta}</div>
            </div>
            <div class="rank-badges">${badges}</div>
          </div>
        `;
      }).join("") : `<div class='muted'>${esc(options.empty || "Данных пока нет.")}</div>`);
    }

    function renderDailyQuality(rows) {
      renderRankList("daily-quality-list", rows || [], {
        title: row => row.day,
        value: row => row.total,
        icon: row => Number(row.wins || 0) ? "trophy" : "calendar-days",
        meta: row => `${formatCount(row.resolved)} решено · ${formatCount(row.important)} важных`,
        badges: row => `
          <span class="badge">${formatCount(row.total)} всего</span>
          <span class="badge good">${formatCount(row.wins)} win</span>
          <span class="badge warn">${formatCount(row.giveaways)} giveaway</span>
        `,
        empty: "Качество по дням появится после накопления истории."
      });
    }

    function renderDashboardInsight(data) {
      const el = $("dashboard-insight");
      if (!el) return;
      const total = Number(data.total_pings || 0);
      const fresh = Number(data.new_pings || 0);
      const important = Number(data.important || 0);
      const resolved = Number(data.resolved || 0);
      const favorites = Number(data.favorites || 0);
      const online = Number(data.accounts_online || 0);
      const mode = fresh || important ? "attention" : total ? "calm" : "empty";
      const headline = fresh
        ? `${fresh} новых упоминаний ждут разбора`
        : important
          ? `${important} важных упоминаний в фокусе`
          : total
            ? "Лента выглядит спокойной"
            : "Данных пока нет";
      const text = total
        ? `${resolved} решено, ${favorites} в избранном, ${online} аккаунтов онлайн.`
        : "Запустите мониторинг или снимите фильтры, если ожидали увидеть упоминания.";
      el.className = `dashboard-insight ${mode}`;
      setHtmlIfChanged(el, `
        <span class="metric-icon"><i data-lucide="${mode === "attention" ? "radar" : mode === "empty" ? "inbox" : "check-circle"}"></i></span>
        <div><strong>${esc(headline)}</strong><span>${esc(text)}</span></div>
      `);
    }

    function attentionToneClass(tone) {
      if (tone === "bad") return "bad";
      if (tone === "warn") return "warn";
      if (tone === "good") return "good";
      return "info";
    }

    function renderDashboardSummary(data) {
      const attentionEl = $("dashboard-attention");
      const scanEl = $("dashboard-scan-card");
      const readinessEl = $("dashboard-readiness");
      if (!attentionEl || !scanEl || !readinessEl) return;

      const healthClass = attentionToneClass(data.health_level);
      const attention = data.attention || [];
      const scan = data.scan_progress || {};
      const percent = Math.max(0, Math.min(100, Number(scan.percent || 0)));
      const readiness = data.readiness || [];

      setHtmlIfChanged(attentionEl, `
        <div class="ops-panel-head">
          <div>
            <div class="kicker">Action center</div>
            <h2>${esc(data.headline || "Что требует внимания")}</h2>
          </div>
          <span class="badge ${healthClass}">${healthClass === "bad" ? "срочно" : healthClass === "warn" ? "внимание" : "ok"}</span>
        </div>
        <div class="ops-list">
          ${attention.map(item => `
            <button class="ops-item tone-${attentionToneClass(item.tone)}" type="button" data-focus-kind="${esc(item.kind || "")}">
              <span class="metric-icon"><i data-lucide="${esc(item.icon || "activity")}"></i></span>
              <div><strong>${esc(item.title || "")}</strong><span>${esc(item.text || "")}</span></div>
              <b>${esc(item.value ?? "")}</b>
            </button>
          `).join("")}
        </div>
      `);

      setHtmlIfChanged(scanEl, `
        <div class="ops-panel-head">
          <div>
            <div class="kicker">Scan</div>
            <h2>${scan.running ? "Скан идет" : "Скан истории"}</h2>
          </div>
          <span class="badge ${scan.last_error ? "bad" : scan.running ? "warn" : "good"}">${scan.last_error ? "ошибка" : scan.running ? percent + "%" : "готов"}</span>
        </div>
        <div class="scan-progress-bar"><span style="width:${percent}%"></span></div>
        <div class="ops-micro-grid">
          <div><span>Аккаунты</span><strong>${Number(scan.accounts_done || 0)}/${Number(scan.accounts_total || 0)}</strong></div>
          <div><span>Каналы</span><strong>${Number(scan.total_channels || data.counts?.total_channels || 0)}</strong></div>
          <div><span>Usernames</span><strong>${Number(scan.usernames_done || 0)}/${Number(scan.usernames_total || 0)}</strong></div>
          <div><span>Найдено</span><strong>${Number(scan.found || 0)}</strong></div>
        </div>
        <div class="ops-note">${esc(scan.last_error || scan.current_username || scan.current_account || "Фоновый мониторинг готов к следующему обновлению.")}</div>
        <div class="deadline-row">
          <span class="badge info">${Number(scan.fast_channels || 0)} быстрых каналов</span>
          <span class="badge">${Number(scan.targeted_channels || 0)} полных проверок</span>
          <span class="badge">${Number(scan.history_limit || 0) > 0 ? "лимит " + Number(scan.history_limit || 0) : "без лимита"}</span>
        </div>
      `);

      setHtmlIfChanged(readinessEl, `
        <div class="ops-panel-head">
          <div>
            <div class="kicker">Readiness</div>
            <h2>Готовность</h2>
          </div>
          <span class="badge ${readiness.every(item => item.ok) ? "good" : "warn"}">${readiness.filter(item => item.ok).length}/${readiness.length}</span>
        </div>
        <div class="readiness-list">
          ${readiness.map(item => `
            <div class="readiness-item ${item.ok ? "ok" : "attention"}">
              <span class="health-dot ${item.ok ? "" : "warn"}"></span>
              <div><strong>${esc(item.label || "")}</strong><span>${esc(item.hint || "")}</span></div>
              <b>${esc(item.value ?? "")}</b>
            </div>
          `).join("")}
        </div>
      `);
    }

    async function loadDashboardSummary() {
      try {
        const data = await api("/api/dashboard/summary");
        renderDashboardSummary(data);
        lucide.createIcons();
      } catch (err) {
        setHtmlIfChanged("dashboard-attention", `
          <div class="ops-panel-head"><div><div class="kicker">Action center</div><h2>Сводка недоступна</h2></div><span class="badge bad">ошибка</span></div>
          <div class="ops-list"><div class="ops-item tone-bad"><span class="metric-icon"><i data-lucide="circle-alert"></i></span><div><strong>Не удалось загрузить пульт</strong><span>${esc(err.message || err)}</span></div><b>!</b></div></div>
        `);
      }
    }

    function taskItem(row, kind = "") {
      const title = row.chat || "Неизвестный источник";
      const deadline = row.deadline_at ? fmtDate(row.deadline_at) : "дедлайн не найден";
      const source = deadlineSourceLabel(row.deadline_source);
      const text = (row.text || "").slice(0, 180);
      return `
        <div class="task-item ${kind}" data-ping='${esc(JSON.stringify(row))}'>
          <div class="row"><strong>${esc(title)}</strong><span class="badge">${esc(actionStatuses[row.action_status] || row.action_status || "new")}</span></div>
          <div class="deadline-row">
            <span class="badge ${row.deadline_at ? "warn" : "bad"}"><i data-lucide="calendar-clock"></i>${esc(deadline)}</span>
            <span class="badge">${esc(source)}</span>
            ${row.reminder_at ? `<span class="badge info"><i data-lucide="bell"></i>${fmtDate(row.reminder_at)}</span>` : ""}
          </div>
          <div class="muted">${esc(text || "Нет текста")}</div>
        </div>
      `;
    }

    function renderTaskBucket(id, rows, kind = "") {
      const el = $(id);
      setHtmlIfChanged(el, (rows || []).map(row => taskItem(row, kind)).join("") || "<div class='muted'>Пусто.</div>");
    }

    async function loadTasks() {
      const data = await api("/api/tasks");
      const buckets = {
        overdue: data.overdue || [],
        today: data.today || [],
        tomorrow: data.tomorrow || [],
        no_deadline: data.no_deadline || [],
        waiting_result: data.waiting_result || [],
        all_open: data.all_open || []
      };
      setHtmlIfChanged("tasks-stats", [
        ["Просрочено", buckets.overdue.length, "triangle-alert", "#fb7185"],
        ["Сегодня", buckets.today.length, "calendar-days", "#f6c453"],
        ["Завтра", buckets.tomorrow.length, "calendar-clock", "#29d3c2"],
        ["Без дедлайна", buckets.no_deadline.length, "help-circle", "#a78bfa"]
      ].map(([label, value, icon, color]) => `
        <div class="panel metric-card" style="--metric-color:${color}">
          <div class="metric-top"><div class="metric-label">${label}</div><span class="metric-icon"><i data-lucide="${icon}"></i></span></div>
          <div class="metric-value">${value}</div>
        </div>
      `).join(""));
      renderTaskBucket("tasks-overdue", buckets.overdue, "overdue");
      renderTaskBucket("tasks-today", buckets.today);
      renderTaskBucket("tasks-tomorrow", buckets.tomorrow);
      renderTaskBucket("tasks-no-deadline", buckets.no_deadline);
      renderTaskBucket("tasks-waiting", buckets.waiting_result);
      renderTaskBucket("tasks-open", buckets.all_open);
      lucide.createIcons();
    }

    async function loadGiveawayBoard() {
      const data = await api("/api/giveaways/board?limit=80");
      window.PulseGiveaways.renderGiveawayBoard(data);
      lucide.createIcons();
    }

    const DEBT_GROUPS = [
      { key: "overdue", label: "Просрочено", cls: "bad" },
      { key: "today", label: "Сегодня", cls: "warn" },
      { key: "week", label: "На неделе", cls: "info" },
      { key: "missing", label: "Без дедлайна", cls: "muted" }
    ];

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

    function groupDebtsByDeadline(rows) {
      const g = { overdue: [], today: [], week: [], missing: [] };
      (rows || []).forEach(r => g[debtGroupKey(r)].push(r));
      return g;
    }

    function isHotDebt(row) {
      if (row.deadline_state === "overdue") return true;
      const s = Number(row.deadline_seconds);
      return Number.isFinite(s) && s >= 0 && s <= 86400;
    }

    function pluralPrizes(n) {
      const m = n % 10, h = n % 100;
      if (m === 1 && h !== 11) return "приз";
      if (m >= 2 && m <= 4 && (h < 12 || h > 14)) return "приза";
      return "призов";
    }

    function debtRow(row) {
      const mentions = safeJson(row.mentions, []);
      const who = mentions[0] ? "@" + String(mentions[0]).replace(/^@/, "") : "@—";
      const nm = (row.text || "").replace(/\s+/g, " ").trim().slice(0, 60) || "Победа";
      const source = row.chat || "источник";
      const hasDeadline = !!row.deadline_at;
      const dl = deadlineMeta(row);
      const pillCls = !hasDeadline ? "info" : (row.deadline_badge_class === "bad" ? "bad" : row.deadline_badge_class === "warn" ? "warn" : "info");
      const pillLabel = hasDeadline ? dl.label : "без срока";
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
          <span class="dq-pill ${pillCls}"><i data-lucide="clock"></i>${esc(pillLabel)}</span>
          ${money ? `<span class="dq-money">${esc(money)}</span>` : ""}
          ${admin ? `<div class="dq-acts">
            <span class="dq-icb good" data-debt-status="claimed" data-id="${row.id}" title="Забрал"><i data-lucide="check"></i></span>
            <span class="dq-icb" data-debt-status="scam" data-id="${row.id}" title="Скам"><i data-lucide="shield-alert"></i></span>
          </div>` : ""}
        </div>
      `;
    }

    function renderDebtHero(data) {
      const rows = data.rows || [];
      const total = rows.length;
      const value = rows.reduce((a, r) => a + Number(r.estimated_value || 0), 0);
      const hot = rows.filter(isHotDebt).sort((a, b) => (Number(a.deadline_seconds ?? 9e9)) - (Number(b.deadline_seconds ?? 9e9)))[0];
      const nearLabel = hot ? deadlineMeta(hot).label : "нет срочных";
      const hotWho = hot ? (safeJson(hot.mentions, [])[0] ? "@" + String(safeJson(hot.mentions, [])[0]).replace(/^@/, "") : "") : "всё спокойно";
      setHtmlIfChanged("debts-hero", `
        <div class="tile acc">
          <div class="lbl">К получению</div>
          <div class="big">${total} ${pluralPrizes(total)}</div>
          <div class="sub mono">${value > 0 ? "≈ ₴" + value.toLocaleString("ru-RU") + " оценкой" : "оценка недоступна"}</div>
        </div>
        <div class="tile">
          <div class="lbl">Ближайший дедлайн</div>
          <div class="big mono" style="color:${hot ? "var(--bad)" : "var(--muted)"}">${esc(nearLabel)}</div>
          <div class="sub">${esc(hotWho)}</div>
        </div>
        <div class="tile">
          <div class="lbl">Новых</div>
          <div class="big">${Number(data.stats?.new || 0)}</div>
          <div class="sub">критичных ${Number(data.stats?.critical || 0)}</div>
        </div>
      `);
    }

    function renderDebtSegments(groups) {
      const seg = state.debtSegment || "all";
      const allCount = groups.overdue.length + groups.today.length + groups.week.length + groups.missing.length;
      const items = [["all", "Все", allCount, false]]
        .concat(DEBT_GROUPS.map(g => [g.key, g.label, groups[g.key].length, g.key === "overdue"]));
      setHtmlIfChanged("debts-segments", items.map(([k, l, n, dot]) =>
        `<span class="dseg ${seg === k ? "active" : ""}" data-debt-seg="${k}">${dot && n ? '<span class="dot"></span>' : ""}${esc(l)} <span class="n">${n}</span></span>`
      ).join(""));
    }

    function renderDebtFocus(rows) {
      const hot = (rows || []).filter(isHotDebt).sort((a, b) => (Number(a.deadline_seconds ?? 9e9)) - (Number(b.deadline_seconds ?? 9e9))).slice(0, 1);
      const label = $("debts-focus-label");
      if (label) label.hidden = hot.length === 0;
      if (!hot.length) { setHtmlIfChanged("debts-focus", ""); return; }
      const r = hot[0];
      const m = safeJson(r.mentions, []);
      const who = m[0] ? "@" + String(m[0]).replace(/^@/, "") : "@—";
      const nm = (r.text || "").replace(/\s+/g, " ").trim().slice(0, 70);
      const link = safeExternalLink(r.link);
      const admin = state.role === "admin";
      setHtmlIfChanged("debts-focus", `
        <div style="text-align:center;flex:none"><div class="cd">${esc(deadlineMeta(r).label)}</div></div>
        <div style="flex:1;min-width:0">
          <div class="dq-l1"><span class="dq-who">${esc(who)}</span><span class="dq-nm">${esc(nm)}</span></div>
        </div>
        <div style="display:flex;gap:8px;flex:none">
          ${link ? `<a class="btn primary" href="${link}" target="_blank" rel="noopener"><i data-lucide="send"></i>Написать</a>` : ""}
          ${admin ? `<button class="btn" data-debt-status="claimed" data-id="${r.id}"><i data-lucide="check"></i>Забрал</button>` : ""}
        </div>
      `);
    }

    function renderDebtLens(data) {
      const profiles = (data.profiles || []).filter(p => Number(p.count) > 0);
      const lines = profiles.length
        ? profiles.map(p => `
          <div class="lr">
            <div><div class="ln">@${esc(p.username)}</div></div>
            <div style="text-align:right"><div class="dq-money">${Number(p.count)}</div></div>
          </div>`).join("")
        : `<div class="lsub">нет открытых долгов</div>`;
      setHtmlIfChanged("debts-lens", `
        <div class="card">
          <div class="ct"><i data-lucide="users"></i>Люди</div>
          ${lines}
        </div>
      `);
    }

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
        return `<div class="dgroup-head ${collapsed ? "muted" : ""}" data-debt-group="${g.key}"><i data-lucide="${collapsed ? "chevron-right" : "chevron-down"}"></i>${g.label} <span style="color:var(--${g.cls})">· ${list.length}</span></div>${body}`;
      }).join("");
      setHtmlIfChanged("debts-list", html
        || emptyState("badge-check", "Долгов нет", "Все призы получены или ещё не появились в очереди."));
      if (typeof updateBulkBtn === "function") updateBulkBtn();
      lucide.createIcons();
    }

    async function loadDebts() {
      const data = await api("/api/debts?limit=180");
      renderDebts(data);
    }

    // Build a tiny inline sparkline (line + area) from a numeric series.
    function sparkSVG(values) {
      const pts = (values || []).map(Number).filter(v => Number.isFinite(v));
      if (pts.length < 2) return "";
      const w = 100, h = 30, pad = 2;
      const max = Math.max(...pts), min = Math.min(...pts);
      const span = max - min || 1;
      const step = (w - pad * 2) / (pts.length - 1);
      const coords = pts.map((v, i) => {
        const x = pad + i * step;
        const y = h - pad - ((v - min) / span) * (h - pad * 2);
        return [Number(x.toFixed(2)), Number(y.toFixed(2))];
      });
      const line = coords.map((c, i) => `${i ? "L" : "M"}${c[0]} ${c[1]}`).join(" ");
      const area = `${line} L${coords[coords.length - 1][0]} ${h} L${coords[0][0]} ${h} Z`;
      return `<svg class="sparkline" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">`
        + `<path class="spark-area" d="${area}"/><path class="spark-line" d="${line}"/></svg>`;
    }

    async function loadAnalytics() {
      const data = await api("/api/analytics");
      const shouldLoadDetailed = state.tab === "analytics";
      const detailed = shouldLoadDetailed ? await api("/api/analytics/detailed") : null;
      const metrics = [
        ["Всего", data.total_pings, "inbox", "#2dd4bf"],
        ["Новые", data.new_pings, "sparkles", "#45d483"],
        ["Каналов всего", data.total_channels, "radio-tower", "#7dd3fc"],
        ["Каналов в базе", data.channel_chats_total, "library", "#a78bfa"],
        ["Важные", data.important, "flame", "#f97373"],
        ["За 24 часа", data.last_24h, "activity", "#29d3c2"],
        ["За 7 дней", data.last_7d, "calendar-days", "#7da7ff"],
        ["Победы", data.wins, "trophy", "#4ade80"],
        ["Win rate", `${Number(data.win_rate || 0).toFixed(1)}%`, "percent", "#f6c453"],
        ["Решено", data.resolved, "check-circle", "#f4b44d"],
        ["Избранное", data.favorites, "star", "#a78bfa"],
        ["Аккаунтов онлайн", data.accounts_online, "radio", "#2dd4bf"]
      ];
      const dailySeries = Array.isArray(data.daily)
        ? data.daily.slice().reverse().map(x => Number(x.count) || 0)
        : [];
      const sparkLabels = new Set(["Всего", "За 24 часа", "За 7 дней"]);
      const spark = sparkLabels.size && dailySeries.length >= 2 ? sparkSVG(dailySeries) : "";
      const metricsHtml = metrics.map(([label, value, icon, color]) => `
        <div class="panel metric-card" style="--metric-color:${color}">
          <div class="metric-top">
            <div class="metric-label">${label}</div>
            <span class="metric-icon"><i data-lucide="${icon}"></i></span>
          </div>
          <div class="metric-value">${value ?? 0}</div>
          ${spark && sparkLabels.has(label) ? spark : ""}
        </div>
      `).join("");
      setHtmlIfChanged("stats-row", metricsHtml);
      setHtmlIfChanged("analytics-stats-row", metricsHtml);
      renderDashboardInsight(data);
      if (state.tab !== "analytics") return;
      renderAnalyticsBrief(data, detailed);
      renderAnalyticsFlow(data, detailed);
      drawChart("dailyChart", "bar", data.daily.slice().reverse().map(x => x.day), data.daily.slice().reverse().map(x => x.count), "#2dd4bf");
      const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
      drawChart("hourlyChart", "bar", hours, hours.map(h => data.hourly[h] || 0), "#f4b44d");
      drawChart("typeChart", "doughnut", ["Личка", "Группы", "Каналы"], [data.by_type.private || 0, data.by_type.group || 0, data.by_type.channel || 0], ["#2dd4bf", "#45d483", "#f4b44d"]);
      const channelAccounts = detailed.channels_by_account || data.channels_by_account || [];
      const channelAccountTotal = Number(detailed.channel_memberships_total || data.channel_memberships_total || 0);
      setHtmlIfChanged("channels-by-account-list", channelAccounts.length ? `
        <div class="source-item analytics-source-summary">
          <div class="row"><strong>Всего каналов по аккаунтам</strong><span class="badge good">${channelAccountTotal}</span></div>
          <div class="deadline-row"><span class="badge">уникальных в базе: ${Number(data.channel_chats_total || 0)}</span><span class="badge">учтено аккаунтов: ${channelAccounts.length}</span></div>
        </div>
        ${channelAccounts.map(a => `
        <div class="rank-item compact">
          <span class="metric-icon"><i data-lucide="${a.status === "online" ? "radio" : "radio-receiver"}"></i></span>
          <div class="rank-body">
            <div class="rank-title"><strong>${esc(a.display || a.session_name || "аккаунт")}</strong><span>${formatCount(a.channels)} каналов</span></div>
            <div class="rank-meta">${a.last_channel_scan_at ? fmtDate(a.last_channel_scan_at) : "скан еще не считал каналы"}</div>
          </div>
          <span class="badge ${a.status === "online" ? "good" : "warn"}">${esc(a.status || "unknown")}</span>
        </div>
      `).join("")}
      ` : "<div class='muted'>Каналы по аккаунтам появятся после ближайшего скана истории.</div>");
      renderDailyQuality(detailed.daily_quality || []);
      renderRankList("senders-list", detailed.senders || [], {
        title: row => row.sender || "неизвестно",
        value: row => row.count,
        icon: row => Number(row.wins || 0) ? "trophy" : "user",
        meta: row => `${formatCount(row.wins)} побед · ${pctLabel(percentOf(row.wins, row.count))} результативность`,
        badges: row => `<span class="badge">${formatCount(row.count)} всего</span><span class="badge good">${formatCount(row.wins)} побед</span>`
      });
      renderRankList("valuable-chats-list", detailed.chats || [], {
        title: row => row.chat || "неизвестно",
        value: row => row.avg_priority || row.count,
        icon: row => Number(row.wins || 0) ? "trophy" : "message-square",
        meta: row => `${formatCount(row.count)} упоминаний · ${formatCount(row.giveaways)} розыгрышей`,
        badges: row => `<span class="badge good">${formatCount(row.wins)} win</span><span class="badge">prio ${Number(row.avg_priority || 0).toFixed(1)}</span>`
      });
      renderRankList("sources-list", detailed.sources || [], {
        title: row => row.chat || "неизвестно",
        value: row => row.score,
        icon: row => Number(row.wins || 0) ? "badge-check" : "radio-tower",
        meta: row => `${formatCount(row.total_pings)} всего · ${formatCount(row.noise)} шум`,
        badges: row => `<span class="badge good">score ${Number(row.score || 0).toFixed(1)}</span><span class="badge warn">${formatCount(row.giveaways)} giveaway</span><span class="badge good">${formatCount(row.wins)} win</span>`,
        empty: "Источники еще не рассчитаны."
      });
      renderRankList("priority-list", detailed.priorities || [], {
        title: row => row.priority_label || "normal",
        value: row => row.count,
        icon: () => "gauge",
        badges: row => `<span class="badge">${formatCount(row.count)}</span>`
      });
      renderRankList("mentions-list", detailed.top_mentions || [], {
        title: row => `@${row.username || ""}`,
        value: row => row.count,
        icon: () => "at-sign",
        badges: row => `<span class="badge info">${formatCount(row.count)} упоминаний</span>`,
        empty: "Упоминаний пока нет."
      });
      renderRankList("status-flow-list", detailed.status_flow || [], {
        title: row => row.status || "unknown",
        value: row => row.count,
        icon: row => row.action_status === "claimed" ? "badge-check" : "git-branch",
        meta: row => actionStatuses[row.action_status] || row.action_status || "new",
        badges: row => `<span class="badge info">${esc(actionStatuses[row.action_status] || row.action_status || "new")}</span><span class="badge">${formatCount(row.count)}</span>`,
        empty: "Статусов пока нет."
      });
    }

    async function loadMarket() {
      const latest = await api("/api/market");
      if (!latest.length) {
        setHtmlIfChanged("market-brief", `
          <div>
            <div class="kicker">Market pulse</div>
            <h2>Котировки пока не загружены</h2>
            <div class="section-meta">Фоновая задача рынка заполнит эту панель после первого успешного запроса.</div>
          </div>
          <div class="brief-grid">${renderBriefTile("Статус", "нет данных", "ожидаю snapshot", "cloud-off", "warn")}</div>
        `);
        $("market-cards").innerHTML = `<div class="panel muted">Котировки пока не загружены.</div>`;
        return;
      }
      const m = latest[0];
      const coins = [
        ["bitcoin", "BTC"], ["ethereum", "ETH"], ["the-open-network", "TON"], ["solana", "SOL"],
        ["binancecoin", "BNB"], ["notcoin", "NOT"], ["dogs-2", "DOGS"], ["tether", "USDT"]
      ];
      const history = (await api("/api/market-history-full?limit=48")).reverse();
      const baseline = history.length > 1 ? history[0] : null;
      renderMarketBrief(m, coins, baseline, history);
      setHtmlIfChanged("market-cards", coins.map(([id, label]) => marketCard(m, id, label, baseline)).join(""));
      setHtmlIfChanged("market-table", `<thead><tr><th>Актив</th><th>USD</th><th>UAH</th><th>24h</th><th>Период</th><th>Обновлено</th></tr></thead><tbody>${coins.map(([id, label]) => marketRow(m, id, label, baseline)).join("")}</tbody>`);
      const labels = history.map(x => new Date(x.fetched_at_iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      drawChart("btcChart", "line", labels, history.map(x => x.bitcoin?.usd || 0), "#f4b44d");
      drawChart("tonChart", "line", labels, history.map(x => x["the-open-network"]?.usd || 0), "#2dd4bf");
      drawChart("ethChart", "line", labels, history.map(x => x.ethereum?.usd || 0), "#a78bfa");
      drawChart("solChart", "line", labels, history.map(x => x.solana?.usd || 0), "#45d483");
    }

    function marketChange(current, baseline) {
      const now = Number(current?.usd || 0);
      const before = Number(baseline?.usd || 0);
      if (!now || !before) return 0;
      return ((now - before) / before) * 100;
    }

    function renderMarketBrief(m, coins, baseline, history) {
      const moves = coins.map(([id, label]) => {
        const item = m[id] || {};
        const change24 = Number(item.usd_24h_change || 0);
        const period = marketChange(item, baseline?.[id]);
        return { id, label, item, change24, period };
      });
      const sorted = moves.slice().sort((a, b) => b.change24 - a.change24);
      const leader = sorted[0] || {};
      const laggard = sorted[sorted.length - 1] || {};
      const volatile = moves.filter(row => Math.abs(row.change24) >= 5).length;
      const stable = moves.find(row => row.label === "USDT") || moves[moves.length - 1] || {};
      setHtmlIfChanged("market-brief", `
        <div>
          <div class="kicker">Market pulse</div>
          <h2>${leader.label || "Рынок"} ${leader.change24 >= 0 ? "держит импульс" : "под давлением"}</h2>
          <div class="section-meta">История: ${history.length} snapshot · обновлено ${fmtDate(m.fetched_at_iso)}</div>
        </div>
        <div class="brief-grid">
          ${renderBriefTile("Лидер 24h", `${leader.label || "-"} ${leader.change24 >= 0 ? "+" : ""}${Number(leader.change24 || 0).toFixed(2)}%`, `период ${Number(leader.period || 0).toFixed(2)}%`, "trending-up", "good")}
          ${renderBriefTile("Слабее рынка", `${laggard.label || "-"} ${laggard.change24 >= 0 ? "+" : ""}${Number(laggard.change24 || 0).toFixed(2)}%`, `период ${Number(laggard.period || 0).toFixed(2)}%`, "trending-down", Number(laggard.change24 || 0) < 0 ? "bad" : "info")}
          ${renderBriefTile("Волатильность", `${volatile} активов`, "движение 5%+ за сутки", "activity", volatile ? "warn" : "good")}
          ${renderBriefTile("Стейбл", `$${Number(stable.item?.usd || 0).toLocaleString()}`, `${stable.label || "USDT"} · ${Number(stable.change24 || 0).toFixed(2)}%`, "badge-dollar-sign", "info")}
        </div>
      `);
    }

    function marketCard(m, id, label, baseline) {
      const d = m[id] || {};
      const c = d.usd_24h_change || 0;
      const period = marketChange(d, baseline?.[id]);
      const tone = c >= 0 ? "good" : "bad";
      return `
        <div class="panel metric-card market-card ${tone}" style="--metric-color:${c >= 0 ? "#45d483" : "#f97373"}">
          <div class="metric-top">
            <div>
              <div class="metric-label">${label} / USD</div>
              <div class="market-subtitle">период ${period >= 0 ? "+" : ""}${period.toFixed(2)}%</div>
            </div>
            <span class="badge ${tone}">${c >= 0 ? "+" : ""}${c.toFixed(2)}%</span>
          </div>
          <div class="metric-value">$${Number(d.usd || 0).toLocaleString()}</div>
          <div class="deadline-row">
            <span class="badge ${period >= 0 ? "good" : "bad"}">${period >= 0 ? "рост" : "снижение"}</span>
            <span class="badge">${Number(d.uah || 0).toLocaleString()} UAH</span>
          </div>
        </div>
      `;
    }

    function marketRow(m, id, label, baseline) {
      const d = m[id] || {};
      const c = d.usd_24h_change || 0;
      const period = marketChange(d, baseline?.[id]);
      return `<tr><td>${label}</td><td>$${Number(d.usd || 0).toLocaleString()}</td><td>${Number(d.uah || 0).toLocaleString()} UAH</td><td><span class="badge ${c >= 0 ? "good" : "bad"}">${c >= 0 ? "+" : ""}${c.toFixed(2)}%</span></td><td><span class="badge ${period >= 0 ? "good" : "bad"}">${period >= 0 ? "+" : ""}${period.toFixed(2)}%</span></td><td>${fmtDate(m.fetched_at_iso)}</td></tr>`;
    }

    function drawChart(id, type, labels, data, color) {
      const canvas = $(id);
      if (!canvas) return;
      state.chartHashes = state.chartHashes || {};
      const signature = hashText(JSON.stringify({ type, labels, data, color }));
      if (state.chartHashes[id] === signature) return;
      state.chartHashes[id] = signature;
      if (state.charts[id]) state.charts[id].destroy();
      const primary = Array.isArray(color) ? color[0] : color;
      state.charts[id] = new Chart(canvas, {
        type,
        data: {
          labels,
          datasets: [{
            data,
            borderColor: type === "doughnut" ? "#101720" : primary,
            backgroundColor: type === "line" ? hexToRgba(primary, .18) : color,
            borderWidth: type === "doughnut" ? 3 : 2,
            borderRadius: type === "bar" ? 5 : 0,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: .35,
            fill: type === "line"
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          resizeDelay: 120,
          cutout: type === "doughnut" ? "58%" : undefined,
          plugins: {
            legend: {
              display: type === "doughnut",
              position: "bottom",
              labels: { color: "#9caab8", boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 14 }
            },
            tooltip: {
              backgroundColor: "#101720",
              borderColor: "rgba(207,219,232,.18)",
              borderWidth: 1,
              titleColor: "#ffffff",
              bodyColor: "#dce6ec",
              displayColors: type === "doughnut"
            }
          },
          scales: type === "doughnut" ? {} : {
            x: { ticks: { color: "#9caab8" }, grid: { display: false } },
            y: { ticks: { color: "#9caab8" }, grid: { color: "rgba(207,219,232,.08)" } }
          }
        }
      });
    }

    function hexToRgba(hex, alpha) {
      const normalized = String(hex || "#29d3c2").replace("#", "");
      const value = normalized.length === 3 ? normalized.split("").map(ch => ch + ch).join("") : normalized;
      const int = Number.parseInt(value, 16);
      const r = (int >> 16) & 255;
      const g = (int >> 8) & 255;
      const b = int & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

