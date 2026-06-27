/* Pulse Desk frontend — app-settings.js: accounts, share guide, settings tabs, backups, bot access, logs/events/diagnostics.
   Classic script (no modules): top-level bindings are shared across the app-*.js files,
   which must load in the order set in index.html. Split from the former app.js. */

    async function loadAccounts() {
      const rows = await api("/api/accounts");
      $("accounts-list").innerHTML = rows.map(a => `
        <div class="panel account-card">
          <div>
            <div class="row" style="gap:8px"><strong>${esc(a.display || a.username || a.session_name)}</strong><span class="status-pill ${esc(a.status || "")}">${esc(a.status || "unknown")}</span></div>
            <div class="muted">${esc(a.session_name)}${a.auth_delivery_type ? " | delivery: " + esc(a.auth_delivery_type) : ""}${a.last_error ? " | " + esc(a.last_error) : ""}</div>
          </div>
          <div class="row" style="gap:8px">
            <button class="btn" data-action="auth-session" data-session="${esc(a.session_name)}">Войти</button>
            <button class="btn bad" data-action="disconnect" data-session="${esc(a.session_name)}">Отключить</button>
          </div>
        </div>`).join("") || emptyState("user-x", "Аккаунты не найдены", "Добавьте аккаунт справа или проверьте session-файлы.");
      const health = await api("/api/accounts/health");
      $("account-health-list").innerHTML = (health.accounts || []).map(a => `
        <div class="panel">
          <div class="row"><strong>${esc(a.display || a.username || a.session_name)}</strong><span class="badge ${a.healthy ? "good" : "warn"}">${esc(a.health_label)}</span></div>
          <div class="muted">${esc(a.last_error || "ошибок нет")}<br>Пингов: ${a.pings_total || 0}, побед: ${a.wins || 0}, розыгрышей: ${a.giveaways || 0}<br>Последнее: ${fmtDate(a.last_ping_at)}</div>
        </div>`).join("") || "<div class='muted'>Нет данных.</div>";
      lucide.createIcons();
    }

    async function loadShareGuide() {
      const guide = await api("/api/share-guide");
      const warnings = [];
      if (!guide.public_share_mode) warnings.push("PUBLIC_SHARE_MODE выключен");
      if (!guide.viewer_token_configured) warnings.push("VIEWER_TOKEN не задан");
      if (guide.viewer_token_looks_weak) warnings.push("VIEWER_TOKEN выглядит слабым");
      if (guide.admin_token_looks_weak) warnings.push("ADMIN_TOKEN выглядит слабым");
      $("share-status").innerHTML = `
        <div class="badges">
          <span class="badge ${guide.public_share_mode ? "good" : "warn"}">${guide.public_share_mode ? "share mode включен" : "share mode выключен"}</span>
          <span class="badge ${guide.viewer_token_configured ? "good" : "bad"}">viewer token ${guide.viewer_token_configured ? "есть" : "не задан"}</span>
          <span class="badge">Cloudflare Quick Tunnel</span>
        </div>
        ${warnings.length ? `<div class="panel"><strong>Проверь перед отправкой:</strong><br>${warnings.map(esc).join("<br>")}</div>` : `<div class="panel muted">Все базовые условия для безопасного viewer-доступа выглядят нормально.</div>`}
      `;
      const steps = [
        ["Запустите Pulse Desk", "python main.py"],
        ["Откройте приватный tunnel", guide.tunnel_command],
        ["Скопируйте HTTPS-ссылку", "Формат ссылки: https://....trycloudflare.com"],
        ["Соберите сообщение", "Вставьте ссылку справа и скопируйте готовый текст."],
        ["Передайте доступ безопасно", "VIEWER_TOKEN отправьте отдельно. ADMIN_TOKEN не отправляйте."]
      ];
      $("share-steps").innerHTML = steps.map(([title, text], index) => `
        <div class="share-step">
          <span class="share-step-number">${index + 1}</span>
          <div><strong>${esc(title)}</strong><div class="muted">${esc(text)}</div></div>
        </div>
      `).join("");
      $("share-never").innerHTML = (guide.never_share || []).map(item => `<span class="badge bad">${esc(item)}</span>`).join("");
      $("friend-message").value = buildFriendMessage(guide);
      $("share-url-input").oninput = () => { $("friend-message").value = buildFriendMessage(guide); };
      lucide.createIcons();
    }

    function renderSettingsSummary() {
      const snapshot = state.settingsSnapshot || {};
      const tracking = snapshot.tracking || {};
      const runtime = snapshot.runtime || {};
      const usernames = Array.isArray(tracking.usernames) ? tracking.usernames : [];
      const source = tracking.source === "saved" ? "SQLite" : "env";
      const scanInterval = Number(runtime.scan_interval_seconds || 0);
      const scanConcurrency = Number(runtime.scan_account_concurrency || 0);
      const scanLimit = Number(runtime.scan_history_limit || 0);
      const editSweep = Number(runtime.edit_scan_recent_messages || 0);
      const marketInterval = Number(runtime.market_poll_seconds || 0);
      if ($("settings-summary-line")) {
        $("settings-summary-line").textContent = `${usernames.length || 0} usernames · источник ${source}`;
      }
      if ($("settings-summary")) {
        $("settings-summary").innerHTML = `
          <div class="summary-tile"><span>Usernames</span><strong>${usernames.length || 0}</strong></div>
          <div class="summary-tile"><span>Скан</span><strong>${scanInterval ? scanInterval + " сек." : "..."}</strong></div>
          <div class="summary-tile"><span>Параллель</span><strong>${scanConcurrency || "..."}</strong></div>
          <div class="summary-tile"><span>Лимит истории</span><strong>${scanLimit > 0 ? scanLimit : "без лимита"}</strong></div>
          <div class="summary-tile"><span>Правки</span><strong>${editSweep > 0 ? editSweep : "off"}</strong></div>
        `;
      }
      if ($("tracking-source")) $("tracking-source").textContent = `Источник: ${source}`;
    }

    function setRuntimeInputs(values = {}) {
      $("runtime-scan-interval").value = values.scan_interval_seconds ?? 900;
      $("runtime-scan-concurrency").value = values.scan_account_concurrency ?? 3;
      $("runtime-scan-limit").value = values.scan_history_limit ?? 0;
      $("runtime-edit-scan-recent").value = values.edit_scan_recent_messages ?? 20;
      $("runtime-startup-delay").value = values.startup_scan_delay_seconds ?? 8;
      $("runtime-market-poll").value = values.market_poll_seconds ?? 300;
      $("runtime-market-alert").value = values.market_alert_change_pct ?? 5;
      $("runtime-market-retention").value = values.market_retention_days ?? 7;
      $("runtime-action-account").value = values.giveaway_action_account || "";
      $("runtime-review-mode").value = values.giveaway_review_mode || "manual";
      $("runtime-analyze-recent").value = values.giveaway_analyze_recent_messages ?? 50;
      $("runtime-inactive-days").value = values.giveaway_inactive_channel_days ?? 14;
      $("runtime-action-delay").value = values.giveaway_min_action_delay_seconds ?? 45;
    }

    function readRuntimeInputs() {
      const numberValue = (id, fallback) => Number($(id).value || fallback);
      return {
        scan_interval_seconds: numberValue("runtime-scan-interval", 900),
        scan_account_concurrency: numberValue("runtime-scan-concurrency", 3),
        scan_history_limit: numberValue("runtime-scan-limit", 0),
        edit_scan_recent_messages: numberValue("runtime-edit-scan-recent", 20),
        startup_scan_delay_seconds: numberValue("runtime-startup-delay", 8),
        market_poll_seconds: numberValue("runtime-market-poll", 300),
        market_alert_change_pct: numberValue("runtime-market-alert", 5),
        market_retention_days: numberValue("runtime-market-retention", 7),
        giveaway_action_account: $("runtime-action-account").value.trim().replace(/^@/, ""),
        giveaway_review_mode: $("runtime-review-mode").value,
        giveaway_analyze_recent_messages: numberValue("runtime-analyze-recent", 50),
        giveaway_inactive_channel_days: numberValue("runtime-inactive-days", 14),
        giveaway_min_action_delay_seconds: numberValue("runtime-action-delay", 45)
      };
    }

    function renderRulesUi() {
      const rules = state.rulesUi?.rules || [];
      $("rules-ui-list").innerHTML = rules.map((rule, index) => `
        <button class="btn chip" data-rule-index="${index}" title="Удалить правило">
          <i data-lucide="${rule.notify === false ? "bell-off" : "bell"}"></i>${esc(rule.name || "Правило")} · ${esc(rule.event_type || "any")}
        </button>
      `).join("") || "<span class='muted'>Визуальных правил пока нет.</span>";
      $("notify-rules").value = JSON.stringify(rules, null, 2);
    }

    function readQuietHours() {
      const raw = $("quiet-hours").value.trim() || "23:00-08:00";
      const [from = "23:00", to = "08:00"] = raw.split("-");
      return { enabled: $("quiet-enabled").checked, from: from.trim(), to: to.trim() };
    }

    async function saveRulesUi() {
      const payload = {
        enabled: $("notify-enabled").checked,
        quiet_hours: readQuietHours(),
        rules: state.rulesUi?.rules || []
      };
      state.rulesUi = await api("/api/settings/rules-ui", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      renderRulesUi();
    }

    async function loadBackups() {
      const data = await api("/api/backups");
      $("backups-list").innerHTML = `<div class="backup-list">${(data.backups || []).map(item => `
        <div class="backup-item">
          <div class="row"><strong>${esc(item.name)}</strong><span class="badge">${(Number(item.size || 0) / 1024 / 1024).toFixed(2)} MB</span></div>
          <div class="deadline-row"><span>${fmtDate(item.created_at)}</span><a class="btn" href="/api/backups/${encodeURIComponent(item.name)}/download" target="_blank" rel="noopener"><i data-lucide="download"></i>Скачать</a></div>
        </div>
      `).join("") || "<div class='muted'>Бэкапов пока нет.</div>"}</div>`;
      lucide.createIcons();
    }

    async function loadBotAccess() {
      const keysBox = $("botkeys-list");
      const membersBox = $("botmembers-list");
      if (!keysBox || !membersBox) return;
      let data;
      try {
        data = await api("/api/bot/access", { silent: true });
      } catch (e) {
        keysBox.innerHTML = "<div class='muted'>Недоступно.</div>";
        membersBox.innerHTML = "";
        return;
      }
      const meta = $("botaccess-meta");
      if (meta) meta.textContent = data.bot_username ? `Бот: @${esc(data.bot_username)} · только просмотр` : "Бот не настроен — ключи всё равно сохранятся";
      const keys = data.keys || [];
      keysBox.innerHTML = keys.length ? `<div class="backup-list">${keys.map(k => `
        <div class="backup-item">
          <div class="row"><strong>#${k.id} ${esc(k.label || "—")}</strong><span class="badge">👥 ${k.member_count || 0}</span><span class="badge">${k.expires_at ? "до " + fmtDate(k.expires_at) : "бессрочно"}</span></div>
          <div class="deadline-row" style="gap:.4rem;flex-wrap:wrap">
            <code style="font-size:.75rem;word-break:break-all">${esc(k.secret)}</code>
            <button class="btn" data-copy-link="${esc(k.share_link || "")}"><i data-lucide="link"></i>Ссылка</button>
            <button class="btn bad" data-revoke-key="${k.id}"><i data-lucide="trash-2"></i>Отозвать</button>
          </div>
        </div>`).join("")}</div>` : "<div class='muted'>Ключей пока нет.</div>";
      const members = data.members || [];
      let restricted = {};
      try {
        const r = await api("/api/access/restricted", { silent: true });
        (r.users || []).forEach(u => { restricted[u.tg_id] = u; });
      } catch (e) { /* access API optional */ }
      membersBox.innerHTML = members.length ? `<div class="backup-list">${members.map(m => {
        const acc = restricted[m.tg_id];
        const accBadge = m.blocked
          ? ""
          : (acc
              ? `<span class="badge bad">🔴 закрыт${acc.until ? " до " + fmtDate(acc.until) : ""}</span>`
              : `<span class="badge good">🟢 доступ открыт</span>`);
        return `
        <div class="backup-item">
          <div class="row"><strong>${esc(m.name || "—")}</strong><span class="badge">${m.tg_username ? "@" + esc(m.tg_username) : "—"}</span><span class="badge ${m.blocked ? "bad" : "good"}">${m.blocked ? "заблокирован" : "активен"}</span>${accBadge}</div>
          <div class="deadline-row" style="gap:.4rem;flex-wrap:wrap">
            <span class="muted">ключ: ${esc(m.key_label || "—")} · ${m.last_seen_at ? fmtDate(m.last_seen_at) : "—"}</span>
            <button class="btn ${m.blocked ? "" : "bad"}" data-block-member="${m.tg_id}" data-blocked="${m.blocked ? 1 : 0}"><i data-lucide="${m.blocked ? "user-check" : "user-x"}"></i>${m.blocked ? "Разблокировать" : "Заблокировать"}</button>
          </div>
          ${m.blocked ? "" : `<div class="deadline-row" style="gap:.4rem;flex-wrap:wrap;margin-top:.3rem">
            <button class="btn" data-access-off="${m.tg_id}" data-mode="2h"><i data-lucide="clock"></i>Выкл 2ч</button>
            <button class="btn" data-access-off="${m.tg_id}" data-mode="morning"><i data-lucide="moon"></i>До утра</button>
            <button class="btn good" data-access-on="${m.tg_id}"><i data-lucide="unlock"></i>Открыть</button>
            <button class="btn" data-access-windows="${m.tg_id}"><i data-lucide="calendar-clock"></i>Окна</button>
            <button class="btn" data-access-undo="${m.tg_id}"><i data-lucide="undo-2"></i>Отменить</button>
          </div>
          <div id="acc-d-${m.tg_id}" class="muted" style="margin-top:.3rem"></div>`}
        </div>`;
      }).join("")}</div>` : "<div class='muted'>Пользователей пока нет.</div>";
      lucide.createIcons();
    }

    async function renderAccessDetail(tgId, container) {
      if (!container) return;
      if (container.dataset.open === "1") { container.innerHTML = ""; container.dataset.open = "0"; return; }
      let data;
      try {
        data = await api(`/api/access/${tgId}`, { silent: true });
      } catch (e) {
        container.innerHTML = "<span class='muted'>Недоступно.</span>";
        return;
      }
      container.dataset.open = "1";
      const wins = data.windows || [];
      const eff = data.effective || {};
      const describe = (w) => {
        let rep = {};
        try { rep = typeof w.repeat_rule === "string" ? JSON.parse(w.repeat_rule) : (w.repeat_rule || {}); } catch (e) {}
        const tz = w.timezone || "UTC";
        if (rep.type === "daily") return `ежедневно ${rep.from}–${rep.to} (${tz})`;
        if (rep.type === "weekly") return `дни ${(rep.days || []).join(",")} ${rep.from}–${rep.to} (${tz})`;
        if (rep.type === "cron") return `cron «${rep.expr}» · ${rep.dur_min} мин (${tz})`;
        return `разово ${w.start_at ? fmtDate(w.start_at) : "—"} → ${w.end_at ? fmtDate(w.end_at) : "бессрочно"}`;
      };
      const rows = wins.map(w => `
        <div class="row" style="gap:.4rem;justify-content:space-between;border-top:1px solid var(--border,#333);padding:.25rem 0">
          <span>${w.enabled ? "✅" : "🚫"} <code>#${w.id}</code> prio ${w.priority} · ${esc(describe(w))}</span>
          <button class="btn bad" data-access-del-window="${w.id}" data-tg="${tgId}"><i data-lucide="trash-2"></i></button>
        </div>`).join("") || "<div class='muted'>Окон нет — действует политика по умолчанию.</div>";
      container.innerHTML = `
        <div style="border:1px solid var(--border,#333);border-radius:8px;padding:.5rem;margin-top:.2rem">
          <div class="row" style="gap:.4rem"><strong>Сейчас:</strong> ${eff.allowed ? "🟢 открыт" : "🔴 закрыт"} <span class="badge">по умолчанию: ${esc(data.default_policy || "allow")}</span>${data.timezone ? `<span class="badge">TZ: ${esc(data.timezone)}</span>` : ""}</div>
          ${rows}
        </div>`;
      lucide.createIcons();
    }

    async function createBotKey(button) {
      const label = ($("botkey-label").value || "").trim();
      const rawExpires = ($("botkey-expires").value || "").trim();
      const body = { label, expires_at: rawExpires ? new Date(rawExpires).toISOString() : null };
      const res = await api("/api/bot/access/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), button });
      const key = res.key || {};
      const box = $("botkey-new");
      box.style.display = "block";
      box.innerHTML = `
        <div class="row"><strong>🔑 Ключ создан</strong><span class="badge">${esc(key.label || "—")}</span></div>
        <div style="margin:.4rem 0"><code style="font-size:.8rem;word-break:break-all">${esc(key.secret || "")}</code></div>
        ${key.share_link ? `<div class="deadline-row" style="gap:.4rem"><a class="btn primary" href="${esc(key.share_link)}" target="_blank" rel="noopener"><i data-lucide="send"></i>Открыть ссылку</a><button class="btn" data-copy-link="${esc(key.share_link)}"><i data-lucide="copy"></i>Скопировать ссылку</button></div>` : "<div class='muted'>Бот не настроен — отправьте ключ вручную.</div>"}`;
      $("botkey-label").value = "";
      $("botkey-expires").value = "";
      lucide.createIcons();
      await loadBotAccess();
    }

    async function renderSettingsHistory(container) {
      try {
        const history = await api('/api/settings/history?limit=30');
        if (!history || !history.length) {
          container.innerHTML = '<p style="color:#888;font-size:0.85rem">Изменений пока нет.</p>';
          return;
        }
        const rows = history.map(h => `
            <tr>
                <td style="padding:4px 8px">${esc(h.key)}</td>
                <td style="padding:4px 8px;color:#888">${esc(h.old_value ?? '—')}</td>
                <td style="padding:4px 8px">${esc(h.new_value ?? '—')}</td>
                <td style="padding:4px 8px;white-space:nowrap;color:#888">${esc((h.changed_at || '').slice(0, 16))}</td>
            </tr>
        `).join('');
        container.innerHTML = `
            <table style="width:100%;font-size:0.8rem;border-collapse:collapse">
                <thead><tr style="text-align:left;border-bottom:1px solid var(--border,#333)">
                    <th style="padding:4px 8px">Ключ</th>
                    <th style="padding:4px 8px">Было</th>
                    <th style="padding:4px 8px">Стало</th>
                    <th style="padding:4px 8px">Когда</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        `;
      } catch (e) {
        container.innerHTML = '<p style="color:#888;font-size:0.85rem">Недоступно.</p>';
      }
    }

    async function loadSettings() {
      const data = await api("/api/settings/usernames");
      $("usernames-input").value = data.usernames.join("\n");
      if ($("tracking-source")) $("tracking-source").textContent = `Источник: ${data.source === "saved" ? "SQLite" : "env"}`;
      const notifications = await api("/api/settings/notifications");
      $("notify-enabled").checked = notifications.enabled !== false;
      $("notify-giveaways").checked = notifications.include_giveaways !== false;
      $("notify-wins").checked = notifications.include_wins !== false;
      $("notify-keywords").value = (notifications.keywords || []).join("\n");
      $("notify-usernames").value = (notifications.usernames || []).join("\n");
      $("notify-chats").value = (notifications.chats || []).join("\n");
      $("notify-cooldown").value = notifications.cooldown_seconds ?? 120;
      state.rulesUi = await api("/api/settings/rules-ui");
      const quiet = state.rulesUi.quiet_hours || notifications.quiet_hours || {};
      $("quiet-enabled").checked = Boolean(quiet.enabled);
      $("quiet-hours").value = `${quiet.from || "23:00"}-${quiet.to || "08:00"}`;
      renderRulesUi();
      const keywords = await api("/api/settings/keywords");
      $("kw-win").value = (keywords.win_keywords || []).join("\n");
      $("kw-giveaway").value = (keywords.giveaway_keywords || []).join("\n");
      $("kw-priority").value = (keywords.high_priority_keywords || []).join("\n");
      $("kw-ignore").value = (keywords.ignore_keywords || []).join("\n");
      const runtime = await api("/api/settings/runtime");
      setRuntimeInputs(runtime.settings || {});
      state.settingsSnapshot = { tracking: data, keywords, runtime: runtime.settings || {} };
      renderSettingsSummary();
      const scan = await api("/api/scan-status");
      $("scan-status").innerHTML = `
        <div class="scan-card">
          <div class="badges"><span class="badge ${scan.running ? "warn" : "good"}">${scan.running ? "идет" : "ожидает"}</span><span class="badge">${scan.current_account ? esc(scan.current_account) : "нет активного аккаунта"}</span></div>
          <div class="scan-grid">
            <div class="scan-cell"><span class="muted">Аккаунты</span><strong>${scan.processed_accounts}/${scan.total_accounts}</strong></div>
            <div class="scan-cell"><span class="muted">Найдено</span><strong>${scan.found}</strong></div>
            <div class="scan-cell"><span class="muted">Ошибки</span><strong>${scan.last_error ? "1" : "0"}</strong></div>
          </div>
          <div class="muted">Старт: ${fmtDate(scan.started_at)}<br>Финиш: ${fmtDate(scan.finished_at)}${scan.last_error ? "<br>Ошибка: " + esc(scan.last_error) : ""}</div>
        </div>`;
      const setup = await api("/api/setup-check");
      $("setup-check-list").innerHTML = setup.checks.map(item => `<div class="row panel"><span class="badge ${item.ok ? "good" : "bad"}">${item.ok ? "ok" : "fix"}</span><strong>${esc(item.label)}</strong>${item.details ? `<span class="muted">${esc(Array.isArray(item.details) ? item.details.join(", ") : item.details)}</span>` : ""}</div>`).join("");
      const runs = await api("/api/scan-runs?limit=12");
      $("scan-runs-list").innerHTML = (runs.runs || []).map(run => `<div class="panel"><div class="row"><strong>#${run.id} ${esc(run.status)}</strong><span class="badge">${run.found || 0} найдено</span><span class="badge">${run.processed_accounts || 0}/${run.total_accounts || 0} акк.</span></div><div class="muted">${fmtDate(run.started_at)} - ${fmtDate(run.finished_at)}${run.last_error ? "<br>" + esc(run.last_error) : ""}</div></div>`).join("") || "<div class='muted'>Сканов пока нет.</div>";
      await loadBackups();
      await loadBotAccess();
      await loadDiagnostics();
      await loadLogs();
      await loadEvents();
      const histContainer = document.getElementById('settings-history-content');
      if (histContainer) renderSettingsHistory(histContainer);
    }

    async function loadLogs() {
      const level = $("log-level").value;
      const data = await api(`/api/logs?limit=120${level ? "&level=" + encodeURIComponent(level) : ""}`);
      $("logs-box").textContent = data.logs.join("\n") || "Логов пока нет.";
    }

    async function loadEvents() {
      const level = $("event-level").value;
      const data = await api(`/api/events?limit=120${level ? "&level=" + encodeURIComponent(level) : ""}`);
      $("events-box").textContent = (data.events || []).map(event => {
        const context = event.context && Object.keys(event.context).length ? " " + JSON.stringify(event.context) : "";
        return `${event.created_at} [${event.level}] ${event.source}: ${event.message}${context}`;
      }).join("\n") || "Событий пока нет.";
    }

    async function loadDiagnostics() {
      const data = await api("/api/diagnostics");
      window.PulseDiagnostics.renderDiagnostics(data);
      lucide.createIcons();
    }

