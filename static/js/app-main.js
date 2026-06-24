/* Pulse Desk frontend — app-main.js: refreshData, DOM event bindings, ping modal, theme, init, web push.
   Classic script (no modules): top-level bindings are shared across the app-*.js files,
   which must load in the order set in index.html. Split from the former app.js. */

    async function refreshData(options = {}) {
      if (!state.token && !state.role && $("login-screen").classList.contains("active")) return;
      if (state.refreshInFlight && options.silent) return;
      state.refreshInFlight = true;
      const silent = Boolean(options.silent);
      try {
        await keepScrollStable(Boolean(options.preserveScroll), async () => {
          let status = null;
          try {
            status = await api("/api/status");
            renderCommandMetrics(status);
            setHtmlIfChanged("side-status", `
          <div class="health-row"><strong>Система</strong><span class="health-dot"></span></div>
          <div class="health-list">
            <div class="health-item"><span>Онлайн</span><strong>${status.accounts_online}</strong></div>
            <div class="health-item"><span>Usernames</span><strong>${status.tracked_usernames.length}</strong></div>
            <div class="health-item"><span>Скан</span><strong>${status.scan.running ? "идет" : "ожидает"}</strong></div>
          </div>
        `);
          } catch {}
          if (state.tab === "dashboard") { await Promise.all([loadPings(false, { silent, preserveScroll: options.preserveScroll }), loadAnalytics(), loadDashboardSummary()]); }
          if (state.tab === "debts") await Promise.all([loadDebts(), loadObsidianDebts()]);
          if (state.tab === "market" && !silent) await loadMarket();
          if (state.tab === "analytics") await loadAnalytics();
          if (state.tab === "share" && !silent) await loadShareGuide();
          if (state.tab === "accounts" && !silent) await loadAccounts();
          if (state.tab === "services" && !silent) await loadServices();
          if (state.tab === "settings" && !silent) await loadSettings();
          lucide.createIcons();
        });
      } finally {
        state.refreshInFlight = false;
      }
    }

    function renderCommandMetrics(status) {
      const scanRunning = Boolean(status.scan?.running);
      const tracked = Array.isArray(status.tracked_usernames) ? status.tracked_usernames.length : 0;
      const role = status.role || state.role || "viewer";
      const roleLabel = role === "admin" ? "admin-доступ" : role === "viewer" ? "viewer-доступ" : "нужен токен";
      setHtmlIfChanged("command-metrics", `
        <div class="command-chip is-live"><span class="metric-icon"><i data-lucide="activity"></i></span><div><strong>Онлайн</strong><span>${scanRunning ? "скан активен" : "система ждет задач"}</span></div></div>
        <div class="command-chip ${role === "admin" ? "is-admin" : "is-viewer"}"><span class="metric-icon"><i data-lucide="${role === "admin" ? "shield-check" : "eye"}"></i></span><div><strong>${roleLabel}</strong><span>${role === "admin" ? "управление открыто" : "безопасный просмотр"}</span></div></div>
        <div class="command-chip"><span class="metric-icon"><i data-lucide="radio"></i></span><div><strong>${status.accounts_online ?? 0}</strong><span>аккаунтов онлайн</span></div></div>
        <div class="command-chip ${scanRunning ? "is-running" : ""}"><span class="metric-icon"><i data-lucide="${scanRunning ? "loader-circle" : "scan-line"}"></i></span><div><strong>${scanRunning ? "Идет скан" : tracked + " usernames"}</strong><span>${scanRunning ? "лента обновляется" : "в мониторинге"}</span></div></div>
      `);
    }

    document.querySelectorAll("[data-tab]").forEach(btn => btn.addEventListener("click", () => setTab(btn.dataset.tab)));
    document.querySelectorAll("[data-settings-tab]").forEach(btn => btn.addEventListener("click", () => setSettingsTab(btn.dataset.settingsTab)));
    $("dashboard-attention").addEventListener("click", (event) => {
      const item = event.target.closest("[data-focus-kind]");
      if (!item) return;
      const kind = item.dataset.focusKind;
      if (kind === "new") {
        $("status-filter").value = "new";
        setTab("dashboard");
        loadPings(false);
        return;
      }
      if (kind === "important") {
        $("type-filter").value = "important";
        setTab("dashboard");
        loadPings(false);
        return;
      }
      if (["overdue", "today"].includes(kind)) {
        $("type-filter").value = "giveaway";
        $("sort-by").value = "deadline_at";
        setTab("dashboard");
        loadPings(false);
        return;
      }
      if (["giveaway-action", "no-deadline", "manual"].includes(kind)) {
        setTab("debts");
        return;
      }
      if (kind === "accounts" && state.role === "admin") {
        setTab("accounts");
        return;
      }
      if (["scan", "scan-error", "events"].includes(kind) && state.role === "admin") {
        setTab("settings");
        setSettingsTab("health");
      }
    });
    $("refresh-btn").addEventListener("click", refreshData);
    $("report-btn").addEventListener("click", async () => {
      const res = await fetch("/api/report-html", { headers: { "X-Pulse-Token": state.token } });
      if (!res.ok) throw new Error(await res.text());
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    });
    $("refresh-share-btn").addEventListener("click", loadShareGuide);
    $("copy-friend-message-btn").addEventListener("click", async () => {
      await navigator.clipboard.writeText($("friend-message").value);
      $("share-copy-status").textContent = "Сообщение скопировано. VIEWER_TOKEN отправьте отдельно.";
    });
    $("login-btn").addEventListener("click", loginWithToken);
    $("login-token").addEventListener("keydown", (event) => { if (event.key === "Enter") loginWithToken(); });
    $("logout-btn").addEventListener("click", logout);
    $("scan-btn").addEventListener("click", async () => {
      $("scan-btn").disabled = true;
      await api("/api/scan-history", { method: "POST" });
      setTimeout(() => { $("scan-btn").disabled = false; refreshData({ silent: true, preserveScroll: true }); }, 2000);
    });
    $("backfill-btn").addEventListener("click", async () => {
      if (!confirm("Перечитать историю каналов и добрать пропущенные пинги по имени? Может занять время и нагрузить аккаунты.")) return;
      $("backfill-btn").disabled = true;
      await api("/api/backfill-mentions", { method: "POST" });
      setTimeout(() => { $("backfill-btn").disabled = false; refreshData({ silent: true, preserveScroll: true }); }, 2000);
    });
    $("restart-monitoring-btn")?.addEventListener("click", async () => {
      if (!confirm("Перезапустить мониторинг? Все Telegram-аккаунты переподключатся (приложение не перезапускается).")) return;
      const btn = $("restart-monitoring-btn");
      btn.disabled = true;
      try {
        const res = await api("/api/monitoring/restart", { method: "POST" });
        alert(res.message || "Мониторинг перезапущен");
      } finally {
        setTimeout(() => { btn.disabled = false; refreshData({ silent: true, preserveScroll: true }); }, 2500);
      }
    });
    $("cancel-scan-btn").addEventListener("click", async () => {
      await api("/api/scan-history/cancel", { method: "POST" });
      await refreshData();
    });
    $("quick-filters").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-quick]");
      if (!btn) return;
      $("status-filter").value = "";
      $("favorite-filter").value = "";
      $("type-filter").value = "all";
      if (btn.dataset.quick === "new") $("status-filter").value = "new";
      if (btn.dataset.quick === "favorite") $("favorite-filter").value = "true";
      if (btn.dataset.quick === "win") $("type-filter").value = "win";
      if (btn.dataset.quick === "giveaway") $("type-filter").value = "giveaway";
      if (btn.dataset.quick === "important") { $("type-filter").value = "important"; $("sort-by").value = "priority_score"; }
      if (btn.dataset.quick === "check") $("type-filter").value = "check";
      document.querySelectorAll("#quick-filters .chip").forEach(chip => chip.classList.toggle("primary", chip === btn && btn.dataset.quick !== "reset"));
      loadPings(false);
    });
    $("save-filter-btn").addEventListener("click", async () => {
      const name = $("saved-filter-name").value.trim();
      if (!name) return;
      const current = state.savedFilters || [];
      const filters = [...current.filter(item => item.name !== name), { name, query: currentFilterQuery() }];
      await api("/api/saved-filters", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filters }) });
      $("saved-filter-name").value = "";
      await loadSavedFilters();
    });
    $("saved-filters-list").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-filter-index]");
      if (!btn) return;
      const item = (state.savedFilters || [])[Number(btn.dataset.filterIndex)];
      if (item) applyFilterQuery(item.query);
    });
    $("read-all-btn").addEventListener("click", markFilteredPingsRead);
    ["search-input", "type-filter", "status-filter", "favorite-filter", "mention-filter", "sort-by", "sort-order"].forEach(id => $(id).addEventListener("input", () => { saveFilters(); loadPings(false); }));
    if ($("tag-filter")) $("tag-filter").addEventListener("input", () => { loadPings(false); });
    loadTagFilter();
    $("group-btn").addEventListener("click", () => { state.grouped = !state.grouped; $("group-btn").classList.toggle("primary", state.grouped); loadPings(false); });
    $("load-more-btn").addEventListener("click", () => { if (state.limit > 0) { state.offset += state.limit; loadPings(true); } });
    $("pings-list").addEventListener("click", async (e) => {
      const actionButton = e.target.closest("button[data-action]");
      if (actionButton) {
        e.stopPropagation();
        const id = actionButton.dataset.id;
        if (actionButton.dataset.action === "favorite") await api(`/api/pings/toggle-favorite/${id}`, { method: "POST" });
        if (actionButton.dataset.action === "read") await api(`/api/pings/mark-read/${id}`, { method: "POST" });
        if (actionButton.dataset.action === "giveaway-status") {
          const mappedAction = { claimed: "claimed", scam: "scam", missed: "missed", missed_unsubscribe: "missed", missed_reply: "missed", closed: "closed", pending: "waiting_result" }[actionButton.dataset.status] || undefined;
          await api(`/api/pings/${id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ giveaway_status: actionButton.dataset.status, action_status: mappedAction })
          });
        }
        return loadPings(false);
      }
      if (e.target.closest("a")) return;
      const card = e.target.closest(".card[data-ping]");
      if (card) openModal(JSON.parse(card.dataset.ping));
    });
    function applyDebtStatus(id, status) {
      const mappedAction = { claimed: "claimed", scam: "scam", missed: "missed", missed_reply: "missed" }[status] || "missed";
      return api(`/api/pings/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ giveaway_status: status, action_status: mappedAction })
      });
    }
    function updateBulkBtn() {
      const n = state.debtSelection ? state.debtSelection.size : 0;
      const btn = $("debts-bulk-btn");
      if (!btn) return;
      btn.hidden = n === 0;
      const c = $("debts-bulk-count");
      if (c) c.textContent = n;
    }
    $("debts-segments").addEventListener("click", (e) => {
      const seg = e.target.closest("[data-debt-seg]");
      if (!seg) return;
      state.debtSegment = seg.dataset.debtSeg || "all";
      if (state.debtBoard) renderDebts(state.debtBoard);
    });
    $("debts-list").addEventListener("click", async (e) => {
      const head = e.target.closest("[data-debt-group]");
      if (head) {
        const k = head.dataset.debtGroup;
        if (state.debtCollapsed.has(k)) state.debtCollapsed.delete(k); else state.debtCollapsed.add(k);
        if (state.debtBoard) renderDebts(state.debtBoard);
        return;
      }
      const sel = e.target.closest("[data-debt-select]");
      if (sel) {
        e.stopPropagation();
        const id = Number(sel.dataset.debtSelect);
        if (state.debtSelection.has(id)) state.debtSelection.delete(id); else state.debtSelection.add(id);
        if (state.debtBoard) renderDebts(state.debtBoard);
        return;
      }
      const statusBtn = e.target.closest("[data-debt-status]");
      if (statusBtn) {
        e.stopPropagation();
        await applyDebtStatus(statusBtn.dataset.id, statusBtn.dataset.debtStatus);
        await loadDebts();
        await loadDashboardSummary();
        return;
      }
      if (e.target.closest("a")) return;
      const item = e.target.closest(".dq-row[data-ping]");
      if (item) openModal(JSON.parse(item.dataset.ping));
    });
    $("debts-focus").addEventListener("click", async (e) => {
      const statusBtn = e.target.closest("[data-debt-status]");
      if (!statusBtn) return;
      e.stopPropagation();
      await applyDebtStatus(statusBtn.dataset.id, statusBtn.dataset.debtStatus);
      await loadDebts();
      await loadDashboardSummary();
    });
    $("debts-bulk-btn")?.addEventListener("click", async () => {
      const ids = Array.from(state.debtSelection || []);
      for (const id of ids) await applyDebtStatus(id, "claimed");
      if (state.debtSelection) state.debtSelection.clear();
      updateBulkBtn();
      await loadDebts();
      await loadDashboardSummary();
    });
    $("debts-history-toggle")?.addEventListener("click", () => {
      const p = $("obsidian-panel");
      if (p) p.hidden = !p.hidden;
    });
    $("obsidian-config").addEventListener("click", async (e) => {
      if (e.target.closest("#obsidian-save-cfg-btn")) await saveObsidianConfig();
    });
    $("obsidian-debt-groups").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-obs-group]");
      if (!btn) return;
      state.obsidianGroup = btn.dataset.obsGroup || "all";
      if (state.obsidianBoard) renderObsidianDebts(state.obsidianBoard);
    });
    $("obsidian-debts-list").addEventListener("click", async (e) => {
      if (e.target.closest("a")) return;
      const check = e.target.closest("button.obsidian-check[data-obs-link]");
      if (!check) return;
      check.disabled = true;
      try {
        await toggleObsidianItem(check.dataset.obsLink, check.dataset.obsChecked !== "1");
      } catch (err) {
        check.disabled = false;
      }
    });
    ["tasks-overdue", "tasks-today", "tasks-tomorrow", "tasks-no-deadline", "tasks-waiting", "tasks-open"].forEach(id => {
      $(id).addEventListener("click", (e) => {
        const item = e.target.closest(".task-item[data-ping]");
        if (item) openModal(JSON.parse(item.dataset.ping));
      });
    });
    ["giveaway-need-action", "giveaway-waiting-result", "giveaway-no-deadline", "giveaway-suspicious", "giveaway-done"].forEach(id => {
      $(id).addEventListener("click", async (e) => {
        const btn = e.target.closest("button[data-board-action]");
        if (btn) {
          e.stopPropagation();
          const pingId = btn.dataset.id;
          const action = btn.dataset.boardAction;
          if (action === "status") {
            const mappedAction = { claimed: "claimed", scam: "scam", missed: "missed", missed_unsubscribe: "missed", missed_reply: "missed", closed: "closed", pending: "waiting_result" }[btn.dataset.status] || undefined;
            await api(`/api/pings/${pingId}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ giveaway_status: btn.dataset.status, action_status: mappedAction })
            });
          }
          if (action === "analyze") await api(`/api/giveaways/${pingId}/analyze`, { method: "POST" });
          if (action === "skip") await api(`/api/giveaways/${pingId}/skip`, { method: "POST" });
          if (action === "refresh-profile") await api(`/api/giveaways/${pingId}/refresh-deadline`, { method: "POST" });
          await loadGiveawayBoard();
          if (["dashboard", "tasks"].includes(state.tab)) await refreshData({ silent: true, preserveScroll: true });
          return;
        }
        const item = e.target.closest(".board-item[data-ping]");
        if (item) openModal(JSON.parse(item.dataset.ping));
      });
    });
    $("modal-close").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("modal").classList.contains("active")) closeModal(); });
    $("modal-save-btn").addEventListener("click", async () => {
      if (!state.currentPing) return;
      const body = {
        status: $("modal-status").value,
        note: $("modal-note").value,
        giveaway_status: $("modal-giveaway-status-wrap").classList.contains("hidden") ? undefined : $("modal-giveaway-status").value,
        action_status: $("modal-action-status").value
      };
      const deadlineValue = fromDatetimeLocal($("modal-deadline").value);
      const reminderValue = fromDatetimeLocal($("modal-reminder").value);
      const currentDeadline = (state.currentPing.deadline_at || "").slice(0, 19);
      const currentReminder = (state.currentPing.reminder_at || "").slice(0, 19);
      if (deadlineValue !== currentDeadline) body.deadline_at = deadlineValue;
      if (reminderValue !== currentReminder) body.reminder_at = reminderValue;
      await api(`/api/pings/${state.currentPing.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      closeModal();
      await refreshData();
    });

    function closeModal() {
      const modal = $("modal");
      modal.classList.remove("active");
      modal.setAttribute("aria-hidden", "true");
      document.body.classList.remove("modal-open");
      document.removeEventListener("keydown", _modalKeydown);
      if (state._modalReturnFocus && typeof state._modalReturnFocus.focus === "function") {
        try { state._modalReturnFocus.focus(); } catch {}
      }
      state._modalReturnFocus = null;
    }

    function _focusableInModal() {
      const modal = $("modal");
      if (!modal) return [];
      return Array.from(modal.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter(el => el.offsetParent !== null);
    }

    function _modalKeydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeModal();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = _focusableInModal();
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    async function loadActionHistory(ping) {
      const el = $("modal-actions-history");
      if (!el) return;
      const isGiveaway = Number(ping.is_giveaway) || Number(ping.is_win);
      if (!isGiveaway) {
        el.innerHTML = "";
        return;
      }
      el.innerHTML = "<div class='muted'>Загрузка истории действий...</div>";
      try {
        const data = await api(`/api/giveaways/${ping.id}/actions`);
        const actions = data.actions || [];
        el.innerHTML = `
          <div class="section-meta">История действий</div>
          <div class="modal-history-list">
            ${actions.length ? actions.slice(0, 8).map(row => `
              <div class="history-row">
                <span class="badge info">${esc(row.action || "action")}</span>
                <span class="badge">${esc(row.status || "")}</span>
                <span class="muted">${fmtDate(row.created_at)}</span>
              </div>
            `).join("") : "<span class='muted'>Истории действий пока нет.</span>"}
          </div>
        `;
      } catch {
        el.innerHTML = "<div class='muted'>Историю действий загрузить не удалось.</div>";
      }
      if (window.lucide) lucide.createIcons();
    }

    function renderModalTags(ping) {
      const el = $("modal-tags-row");
      if (!el) return;
      const tags = Array.isArray(ping.tags) ? ping.tags : (ping.tags ? (() => { try { return JSON.parse(ping.tags); } catch { return []; } })() : []);
      const tagsRow = `<div class="tags-row" style="margin-top:0.5rem">
        ${tags.map(t => `<span class="tag-chip">${esc(t)} <button class="tag-remove" onclick="window._removePingTag(${ping.id}, ${JSON.stringify(t)})" style="background:none;border:none;color:#fff;cursor:pointer;padding:0;font-size:0.9rem">×</button></span>`).join('')}
        <input id="tag-input-${ping.id}" placeholder="добавить тег…" maxlength="40" style="width:120px;font-size:0.8rem">
        <button onclick="window._addPingTag(${ping.id})" style="font-size:0.8rem">+</button>
      </div>`;
      el.innerHTML = tagsRow;
    }

    window._addPingTag = async function addPingTag(pingId) {
      const input = document.getElementById(`tag-input-${pingId}`);
      const tag = (input?.value || '').trim();
      if (!tag) return;
      const result = await api(`/api/pings/${pingId}/tags/${encodeURIComponent(tag)}`, { method: 'POST' });
      input.value = '';
      if (state.currentPing && state.currentPing.id === pingId) {
        state.currentPing = Object.assign({}, state.currentPing, { tags: result.tags });
        renderModalTags(state.currentPing);
      }
      await loadTagFilter();
    };

    window._removePingTag = async function removePingTag(pingId, tag) {
      const result = await api(`/api/pings/${pingId}/tags/${encodeURIComponent(tag)}`, { method: 'DELETE' });
      if (state.currentPing && state.currentPing.id === pingId) {
        state.currentPing = Object.assign({}, state.currentPing, { tags: result.tags });
        renderModalTags(state.currentPing);
      }
      await loadTagFilter();
    };

    function openModal(ping) {
      state.currentPing = ping;
      $("modal-title").textContent = ping.chat || "Упоминание";
      $("modal-meta").textContent = `${ping.sender || "неизвестно"} | ${fmtDate(ping.detected_at || ping.date)} | ${ping.chat_type || "unknown"}`;
      $("modal-text").textContent = ping.text || "Нет текста";
      $("modal-link").href = ping.link || "#";
      $("modal-status").value = ping.status || "new";
      $("modal-action-status").value = ping.action_status || "new";
      $("modal-deadline").value = toDatetimeLocal(ping.deadline_at);
      $("modal-reminder").value = toDatetimeLocal(ping.reminder_at);
      const isGiveaway = Number(ping.is_giveaway) || Number(ping.is_win);
      $("modal-giveaway-status-wrap").classList.toggle("hidden", !isGiveaway);
      $("modal-giveaway-status").value = ping.giveaway_status || "pending";
      $("modal-note").value = ping.note || "";
      renderModalTags(ping);
      document.body.classList.add("modal-open");
      const modal = $("modal");
      modal.classList.add("active");
      modal.setAttribute("aria-hidden", "false");
      if (!modal.hasAttribute("role")) {
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-labelledby", "modal-title");
      }
      state._modalReturnFocus = document.activeElement;
      document.addEventListener("keydown", _modalKeydown);
      // Focus the first interactive element after a tick
      setTimeout(() => {
        const focusable = _focusableInModal();
        if (focusable.length) focusable[0].focus();
      }, 30);
      loadActionHistory(ping);
    }

    $("send-code-btn").addEventListener("click", async () => {
      const phone = $("auth-phone").value.trim();
      const sessionName = $("auth-session-name").value.trim();
      if (!phone) return;
      $("auth-status").textContent = "Отправка кода...";
      const data = await api("/api/auth/send-code", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ phone, session_name: sessionName, force_sms: false }) });
      $("auth-status").textContent = data.message;
      if (data.status === "ok") {
        $("auth-code").classList.remove("hidden");
        $("auth-password").classList.remove("hidden");
        $("sign-in-btn").classList.remove("hidden");
      }
    });
    $("sign-in-btn").addEventListener("click", async () => {
      const code = $("auth-code").value.trim();
      if (!code) {
        $("auth-status").textContent = "Введите код из Telegram перед входом.";
        return;
      }
      const body = { phone: $("auth-phone").value.trim(), session_name: $("auth-session-name").value.trim(), code, password: $("auth-password").value };
      const data = await api("/api/auth/sign-in", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      $("auth-status").textContent = data.message || (data.status === "ok" ? `Аккаунт добавлен: ${data.user}` : data.status);
      if (data.status === "ok") loadAccounts();
    });
    $("accounts-list").addEventListener("click", async (e) => {
      const btn = e.target.closest("button[data-action='disconnect']");
      const authBtn = e.target.closest("button[data-action='auth-session']");
      if (authBtn) {
        $("auth-session-name").value = authBtn.dataset.session || "";
        $("auth-status").textContent = `Сессия для входа: ${authBtn.dataset.session || ""}`;
        $("auth-phone").focus();
        return;
      }
      if (!btn) return;
      await api(`/api/accounts/${encodeURIComponent(btn.dataset.session)}/disconnect`, { method: "POST" });
      loadAccounts();
    });
    $("save-usernames-btn").addEventListener("click", async () => {
      const usernames = splitLines($("usernames-input").value);
      const data = await api("/api/settings/usernames", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ usernames }) });
      $("settings-status").textContent = `Сохранено: ${data.usernames.join(", ")}`;
      state.settingsSnapshot = Object.assign({}, state.settingsSnapshot || {}, { tracking: Object.assign({}, data, { source: "saved" }) });
      renderSettingsSummary();
    });
    $("save-runtime-btn").addEventListener("click", async () => {
      const body = readRuntimeInputs();
      const data = await api("/api/settings/runtime", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setRuntimeInputs(data.settings || {});
      $("runtime-status").textContent = "Сохранено и применено в runtime.";
      state.settingsSnapshot = Object.assign({}, state.settingsSnapshot || {}, { runtime: data.settings || {} });
      renderSettingsSummary();
      await refreshData({ silent: true, preserveScroll: true });
    });
    $("save-notifications-btn").addEventListener("click", async () => {
      const body = {
        enabled: $("notify-enabled").checked,
        include_giveaways: $("notify-giveaways").checked,
        include_wins: $("notify-wins").checked,
        cooldown_seconds: Number($("notify-cooldown").value || 120),
        quiet_hours: readQuietHours(),
        rules: state.rulesUi?.rules || [],
        keywords: splitLines($("notify-keywords").value),
        usernames: splitLines($("notify-usernames").value),
        chats: splitLines($("notify-chats").value)
      };
      await api("/api/settings/notifications", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await saveRulesUi();
      $("notifications-status").textContent = "Правила сохранены.";
    });
    $("add-rule-btn").addEventListener("click", async () => {
      const name = $("rule-name").value.trim() || "Новое правило";
      const rule = {
        name,
        event_type: $("rule-event").value,
        notify: $("rule-notify").checked,
        keywords: splitLines($("rule-keywords").value),
        usernames: splitLines($("rule-usernames").value),
        chats: splitLines($("rule-chats").value),
        enabled: true
      };
      state.rulesUi = state.rulesUi || { enabled: true, quiet_hours: readQuietHours(), rules: [] };
      state.rulesUi.rules = [...(state.rulesUi.rules || []), rule];
      ["rule-name", "rule-keywords", "rule-usernames", "rule-chats"].forEach(id => $(id).value = "");
      await saveRulesUi();
    });
    $("rules-ui-list").addEventListener("click", async (event) => {
      const btn = event.target.closest("button[data-rule-index]");
      if (!btn || !state.rulesUi) return;
      state.rulesUi.rules.splice(Number(btn.dataset.ruleIndex), 1);
      await saveRulesUi();
    });
    $("save-keywords-btn").addEventListener("click", async () => {
      const body = {
        win_keywords: splitLines($("kw-win").value),
        giveaway_keywords: splitLines($("kw-giveaway").value),
        high_priority_keywords: splitLines($("kw-priority").value),
        ignore_keywords: splitLines($("kw-ignore").value)
      };
      const data = await api("/api/settings/keywords", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      $("keywords-status").textContent = `Сохранено. Розыгрыш определяется только для каналов с ключевыми словами: ${(data.giveaway_keywords || []).join(", ")}`;
      state.settingsSnapshot = Object.assign({}, state.settingsSnapshot || {}, { keywords: data });
      renderSettingsSummary();
    });
    $("log-level").addEventListener("change", loadLogs);
    $("event-level").addEventListener("change", loadEvents);
    $("refresh-events-btn").addEventListener("click", loadEvents);
    $("refresh-debts-btn").addEventListener("click", loadDebts);
    $("refresh-obsidian-btn")?.addEventListener("click", syncObsidianDebts);
    $("refresh-giveaways-btn").addEventListener("click", loadGiveawayBoard);
    $("refresh-diagnostics-btn").addEventListener("click", loadDiagnostics);
    $("refresh-backups-btn").addEventListener("click", loadBackups);
    $("create-backup-btn").addEventListener("click", async () => {
      await api("/api/backups/create", { method: "POST" });
      await loadBackups();
    });
    $("refresh-botaccess-btn")?.addEventListener("click", loadBotAccess);
    $("create-botkey-btn")?.addEventListener("click", (e) => createBotKey(e.currentTarget));
    document.addEventListener("click", async (e) => {
      const copyBtn = e.target.closest("[data-copy-link]");
      if (copyBtn) {
        await navigator.clipboard.writeText(copyBtn.dataset.copyLink || "");
        showToast("Ссылка скопирована", "success");
        return;
      }
      const revokeBtn = e.target.closest("[data-revoke-key]");
      if (revokeBtn) {
        if (!confirm("Отозвать ключ? Доступ по нему перестанет работать.")) return;
        await api(`/api/bot/access/keys/${revokeBtn.dataset.revokeKey}/revoke`, { method: "POST", button: revokeBtn });
        await loadBotAccess();
        return;
      }
      const blockBtn = e.target.closest("[data-block-member]");
      if (blockBtn) {
        const blocked = blockBtn.dataset.blocked === "1";
        await api(`/api/bot/access/members/${blockBtn.dataset.blockMember}/block`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ blocked: !blocked }), button: blockBtn });
        await loadBotAccess();
        return;
      }
      const accOff = e.target.closest("[data-access-off]");
      if (accOff) {
        let until;
        if (accOff.dataset.mode === "2h") {
          until = new Date(Date.now() + 2 * 3600 * 1000).toISOString();
        } else {
          const d = new Date(); d.setHours(9, 0, 0, 0);
          if (d <= new Date()) d.setDate(d.getDate() + 1);
          until = d.toISOString();
        }
        await api(`/api/access/${accOff.dataset.accessOff}/disable-until`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ until }), button: accOff });
        showToast("Доступ закрыт", "success");
        await loadBotAccess();
        return;
      }
      const accOn = e.target.closest("[data-access-on]");
      if (accOn) {
        await api(`/api/access/${accOn.dataset.accessOn}/enable`, { method: "POST", button: accOn });
        showToast("Доступ открыт", "success");
        await loadBotAccess();
        return;
      }
      const accUndo = e.target.closest("[data-access-undo]");
      if (accUndo) {
        const res = await api(`/api/access/${accUndo.dataset.accessUndo}/undo`, { method: "POST", button: accUndo });
        showToast(res.undone ? `Отменено: ${res.undone.action}` : "Нечего отменять", res.undone ? "success" : "warn");
        await loadBotAccess();
        return;
      }
      const accWin = e.target.closest("[data-access-windows]");
      if (accWin) {
        await renderAccessDetail(accWin.dataset.accessWindows, document.getElementById(`acc-d-${accWin.dataset.accessWindows}`));
        return;
      }
      const accDel = e.target.closest("[data-access-del-window]");
      if (accDel) {
        if (!confirm("Удалить окно расписания?")) return;
        await api(`/api/access/windows/${accDel.dataset.accessDelWindow}`, { method: "DELETE", button: accDel });
        const cont = document.getElementById(`acc-d-${accDel.dataset.tg}`);
        if (cont) cont.dataset.open = "0";
        await renderAccessDetail(accDel.dataset.tg, cont);
        return;
      }
    });
    $("copy-logs-btn").addEventListener("click", () => navigator.clipboard.writeText($("logs-box").textContent));
    $("export-json-btn").addEventListener("click", async () => {
      const data = await api(`/api/export-json?${params(false)}`);
      downloadJson(`pulse_pings_${new Date().toISOString().slice(0, 10)}.json`, data);
    });

    document.getElementById('btn-tg-digest')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-tg-digest');
        const label = btn.querySelector('.digest-label') || btn;
        btn.disabled = true;
        label.textContent = 'Отправляю…';
        try {
            const hours = document.getElementById('digest-hours')?.value || '24';
            const res = await api(`/api/export/telegram-digest?hours=${hours}`, { method: 'POST' });
            label.textContent = `Отправлено (${res.pings_count} пингов)`;
        } catch (e) {
            label.textContent = 'Ошибка';
            console.error(e);
        }
        setTimeout(() => {
            btn.disabled = false;
            label.textContent = 'Дайджест в Telegram';
        }, 4000);
    });

    function applyTheme(theme) {
      // theme: "light" | "dark" | "auto"
      if (!["light", "dark", "auto"].includes(theme)) theme = "auto";
      localStorage.setItem("pulse_theme", theme);
      const effective = theme === "auto"
        ? (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
        : theme;
      document.documentElement.setAttribute("data-theme", effective);
      const btn = document.getElementById("theme-toggle");
      if (btn) {
        const next = effective === "light" ? "dark" : "light";
        btn.setAttribute("aria-label", `Сменить тему (сейчас ${effective})`);
        btn.dataset.next = next;
      }
    }

    function initTheme() {
      const stored = localStorage.getItem("pulse_theme") || "light";
      applyTheme(stored);
      // React to OS theme changes when in auto mode
      if (window.matchMedia) {
        const mq = window.matchMedia("(prefers-color-scheme: light)");
        mq.addEventListener("change", () => {
          if ((localStorage.getItem("pulse_theme") || "auto") === "auto") applyTheme("auto");
        });
      }
      // Wire up button if it exists
      document.body.addEventListener("click", (e) => {
        const btn = e.target.closest && e.target.closest("#theme-toggle");
        if (!btn) return;
        const next = btn.dataset.next || "light";
        applyTheme(next);
      });
    }

    async function initApp() {
      initTheme();
      restoreFilters();
      applyRole();
      const ok = await loadSession();
      if (ok) {
        await loadSavedFilters();
        startLive();
        refreshData();
      }
    }

    initApp();
    setInterval(() => refreshData({ silent: true, preserveScroll: true }), 20000);
    lucide.createIcons();

    function urlBase64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
        const raw = atob(base64);
        return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
    }

    async function subscribeToPush() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
            alert('Push-уведомления не поддерживаются в этом браузере');
            return;
        }
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return;
        const reg = await navigator.serviceWorker.ready;
        let publicKey;
        try {
            const res = await api('/api/push/vapid-public-key');
            publicKey = res.key;
        } catch { return; }
        const subscription = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(publicKey),
        });
        await api('/api/push/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(subscription.toJSON()),
        });
        localStorage.setItem('pulse_browser_notifications', '1');
        const btn = document.getElementById('btn-push-subscribe');
        if (btn) { btn.classList.add('is-on'); const l = btn.querySelector('.notify-label'); if (l) l.textContent = 'Отписаться'; }
    }

    async function unsubscribeFromPush() {
        if (!('serviceWorker' in navigator)) return;
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
            await api('/api/push/subscribe', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ endpoint: sub.endpoint }),
            });
            await sub.unsubscribe();
        }
        localStorage.removeItem('pulse_browser_notifications');
        const btn = document.getElementById('btn-push-subscribe');
        if (btn) { btn.classList.remove('is-on'); const l = btn.querySelector('.notify-label'); if (l) l.textContent = 'Уведомления'; }
    }

    document.getElementById('btn-push-subscribe')?.addEventListener('click', async () => {
        if (!('serviceWorker' in navigator)) return;
        const reg = await navigator.serviceWorker.ready;
        const existing = await reg.pushManager.getSubscription();
        if (existing) await unsubscribeFromPush();
        else await subscribeToPush();
    });

    // Update button state on page load
    (async () => {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
        try {
            const reg = await navigator.serviceWorker.ready;
            const sub = await reg.pushManager.getSubscription();
            const btn = document.getElementById('btn-push-subscribe');
            if (btn && sub) { btn.classList.add('is-on'); const l = btn.querySelector('.notify-label'); if (l) l.textContent = 'Отписаться'; }
        } catch {}
    })();
