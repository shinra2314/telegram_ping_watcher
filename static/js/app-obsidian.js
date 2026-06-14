/* Pulse Desk frontend — app-obsidian.js: the "Obsidian · @Долги" panel inside
   the Debts tab. Mirrors the note's dataviewjs progress summary and lets admins
   toggle a checklist item (writes the note + reconciles the matched ping).
   Classic script: shares the global scope with the other app-*.js files. */

    function obsidianProgressColor(pct) {
      return pct >= 100 ? "#10b981" : pct >= 50 ? "#f59e0b" : "#ef4444";
    }

    function renderObsidianStatus(data) {
      const el = $("obsidian-sync-status");
      if (!el) return;
      if (!data || data.enabled === false) {
        el.textContent = "Синхронизация выключена — задайте OBSIDIAN_DEBTS_PATH и OBSIDIAN_SYNC_ENABLED в .env";
        return;
      }
      if (data.reason === "missing_file") {
        el.textContent = `Файл не найден: ${data.path || ""}`;
        return;
      }
      const sync = data.sync || {};
      const parts = [];
      if (sync.last_sync_at) parts.push(`обновлено ${fmtDate(sync.last_sync_at)}`);
      parts.push(sync.write_enabled ? "запись включена" : "только чтение");
      const stats = data.stats || {};
      if (stats.matched != null) parts.push(`${stats.matched} связано с приложением`);
      if (sync.appended) parts.push(`+${sync.appended} добавлено`);
      if (sync.applied_status_updates) parts.push(`${sync.applied_status_updates} статусов обновлено`);
      el.textContent = parts.join(" · ");
    }

    function renderObsidianProgress(data) {
      const overall = data.overall || { done: 0, total: 0, pct: 0 };
      const color = obsidianProgressColor(overall.pct);
      setHtmlIfChanged("obsidian-debts-progress", `
        <div class="obsidian-overall">
          <div class="obsidian-overall-head">
            <strong>Общий прогресс</strong>
            <span style="color:${color}">${overall.done} / ${overall.total} · ${overall.pct}%</span>
          </div>
          <div class="obsidian-bar"><span style="width:${overall.pct}%;background:${color}"></span></div>
        </div>
      `);
    }

    function renderObsidianGroups(data) {
      const groups = data.groups || [];
      const active = state.obsidianGroup || "all";
      const chips = [`
        <button class="obsidian-chip ${active === "all" ? "active" : ""}" data-obs-group="all">
          <span>Все</span><b>${data.overall ? data.overall.done : 0}/${data.overall ? data.overall.total : 0}</b>
        </button>
      `].concat(groups.map(group => {
        const color = obsidianProgressColor(group.pct);
        return `
          <button class="obsidian-chip ${active === group.username ? "active" : ""}" data-obs-group="${esc(group.username)}" style="border-left:3px solid ${color}">
            <span>@${esc(group.username)}</span><b style="color:${color}">${group.done}/${group.total}</b>
            <small>${esc(group.split_rule || "")}</small>
          </button>
        `;
      })).join("");
      setHtmlIfChanged("obsidian-debt-groups", chips);
    }

    function obsidianItem(item, admin) {
      const link = item.link_norm || item.link || "";
      const canToggle = admin && link;
      const icon = item.checked ? "square-check-big" : "square";
      const href = item.link ? safeExternalLink(item.link) : "";
      return `
        <div class="obsidian-item ${item.checked ? "is-done" : ""}">
          <button class="obsidian-check" ${canToggle ? `data-obs-link="${esc(link)}" data-obs-checked="${item.checked ? 1 : 0}"` : "disabled"}>
            <i data-lucide="${icon}"></i>
          </button>
          <div class="obsidian-item-body">
            <div class="obsidian-item-title">${esc(item.title || "(без названия)")}</div>
            <div class="obsidian-item-meta">
              ${item.recipient ? `<span class="badge">${esc(item.recipient)}</span>` : ""}
              ${item.done_date ? `<span class="badge good">✅ ${esc(item.done_date)}</span>` : ""}
              ${href ? `<a class="badge" href="${href}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Telegram</a>` : ""}
              ${item.matched_ping_id ? `<span class="badge info"><i data-lucide="link"></i>в приложении</span>` : ""}
            </div>
          </div>
        </div>
      `;
    }

    function renderObsidianList(data) {
      const admin = state.role === "admin";
      const active = state.obsidianGroup || "all";
      const groups = (data.groups || []).filter(group => active === "all" || group.username === active);
      if (!groups.length) {
        setHtmlIfChanged("obsidian-debts-list", emptyState("notebook-pen", "Пусто", "В заметке нет распознанных позиций или синхронизация выключена."));
        return;
      }
      const html = groups.map(group => {
        const color = obsidianProgressColor(group.pct);
        const items = (group.items || []).map(item => obsidianItem(item, admin)).join("");
        return `
          <div class="obsidian-group">
            <div class="obsidian-group-head">
              <strong>@${esc(group.username)}</strong>
              <span class="obsidian-group-split">${esc(group.split_rule || "")}</span>
              <span class="badge" style="color:${color}">${group.done}/${group.total} · ${group.pct}%</span>
            </div>
            ${items}
          </div>
        `;
      }).join("");
      setHtmlIfChanged("obsidian-debts-list", html);
    }

    function renderObsidianDebts(data) {
      state.obsidianBoard = data;
      const validGroups = new Set(["all"].concat((data.groups || []).map(group => group.username)));
      if (!validGroups.has(state.obsidianGroup)) state.obsidianGroup = "all";
      renderObsidianStatus(data);
      renderObsidianProgress(data);
      renderObsidianGroups(data);
      renderObsidianList(data);
      if (window.lucide) lucide.createIcons();
    }

    function renderObsidianConfig(cfg) {
      setHtmlIfChanged("obsidian-config", `
        <label class="obsidian-switch">
          <input type="checkbox" data-obs-cfg="enabled" ${cfg.enabled ? "checked" : ""}>
          <span>Синхронизация вкл</span>
        </label>
        <label class="obsidian-switch">
          <input type="checkbox" data-obs-cfg="write" ${cfg.write ? "checked" : ""}>
          <span>Запись в файл</span>
        </label>
        <input type="text" id="obsidian-path-input" class="obsidian-path" placeholder="Путь к Долги.md" value="${esc(cfg.path || "")}">
        <button class="btn primary" id="obsidian-save-cfg-btn"><i data-lucide="save"></i>Сохранить</button>
      `);
      if (window.lucide) lucide.createIcons();
    }

    async function loadObsidianConfig() {
      if (state.role !== "admin") {
        setHtmlIfChanged("obsidian-config", "");
        return;
      }
      try {
        renderObsidianConfig(await api("/api/debts/obsidian/config"));
      } catch (err) {
        setHtmlIfChanged("obsidian-config", "");
      }
    }

    async function saveObsidianConfig() {
      const root = $("obsidian-config");
      if (!root) return;
      const enabled = root.querySelector('[data-obs-cfg="enabled"]')?.checked || false;
      const write = root.querySelector('[data-obs-cfg="write"]')?.checked || false;
      const path = ($("obsidian-path-input")?.value || "").trim();
      const btn = $("obsidian-save-cfg-btn");
      if (btn) btn.disabled = true;
      try {
        await api("/api/debts/obsidian/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled, write, path })
        });
        await loadObsidianDebts();
        if (typeof loadDebts === "function") await loadDebts();
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function loadObsidianDebts() {
      try {
        const data = await api("/api/debts/obsidian");
        renderObsidianDebts(data);
        await loadObsidianConfig();
      } catch (err) {
        // Panel is optional; keep the rest of the Debts tab working.
      }
    }

    async function syncObsidianDebts() {
      const btn = $("refresh-obsidian-btn");
      if (btn) btn.disabled = true;
      try {
        await api("/api/debts/obsidian/sync", { method: "POST" });
        await loadObsidianDebts();
        if (typeof loadDebts === "function") await loadDebts();
      } catch (err) {
        const el = $("obsidian-sync-status");
        if (el) el.textContent = "Ошибка синхронизации — нужен admin-токен и доступный файл заметки.";
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function toggleObsidianItem(link, checked) {
      await api("/api/debts/obsidian/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ link, checked })
      });
      await loadObsidianDebts();
      if (typeof loadDebts === "function") await loadDebts();
    }
