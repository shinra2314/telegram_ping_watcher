/* Pulse Desk frontend — app-core.js: shared utils, api(), auth/session, SSE live feed, tabs, filters.
   Classic script (no modules): top-level bindings are shared across the app-*.js files,
   which must load in the order set in index.html. Split from the former app.js. */

    const { state, $, esc, fmtDate, loadingCards, emptyState, splitLines, downloadJson } = window.PulseCore;
    const apiUrl = (url) => url;

    function saveFilters() {
      const filters = {};
      ["search-input", "type-filter", "status-filter", "favorite-filter", "mention-filter", "sort-by", "sort-order"].forEach(id => filters[id] = $(id).value);
      localStorage.setItem("pulse_filters", JSON.stringify(filters));
    }

    function restoreFilters() {
      try {
        const filters = JSON.parse(localStorage.getItem("pulse_filters") || "{}");
        Object.entries(filters).forEach(([id, value]) => { if ($(id)) $(id).value = value; });
      } catch {}
    }

    function buildFriendMessage(guide) {
      const tunnelUrl = $("share-url-input")?.value.trim() || "https://....trycloudflare.com";
      return (guide.friend_message_template || "")
        .replace("{tunnel_url}", tunnelUrl)
        .replace("VIEWER_TOKEN", "VIEWER_TOKEN");
    }

    function showToast(message, kind = "info", timeoutMs = 4000) {
      let host = document.getElementById("toast-host");
      if (!host) {
        host = document.createElement("div");
        host.id = "toast-host";
        host.setAttribute("aria-live", "polite");
        host.setAttribute("role", "status");
        document.body.appendChild(host);
      }
      const toast = document.createElement("div");
      toast.className = `toast toast-${kind}`;
      toast.textContent = message;
      host.appendChild(toast);
      // Force reflow then add visible class for fade-in
      requestAnimationFrame(() => toast.classList.add("visible"));
      setTimeout(() => {
        toast.classList.remove("visible");
        setTimeout(() => toast.remove(), 250);
      }, timeoutMs);
    }

    async function api(url, options = {}) {
      const headers = Object.assign({}, options.headers || {});
      if (state.token) headers["X-Pulse-Token"] = state.token;
      const button = options.button || null;
      const silent = options.silent === true;
      let originalLabel = "";
      if (button) {
        button.disabled = true;
        originalLabel = button.dataset._busyLabel = button.innerHTML;
        button.classList.add("is-loading");
      }
      // Strip our custom keys before forwarding to fetch
      const { button: _b, silent: _s, ...fetchOpts } = options;
      try {
        const res = await fetch(apiUrl(url), Object.assign({}, fetchOpts, { headers }));
        if (res.status === 401 || res.status === 403) {
          const message = res.status === 403 ? "Для этого действия нужен admin-токен." : "Нужен токен доступа.";
          showLogin(message);
          throw new Error(message);
        }
        if (!res.ok) {
          const text = await res.text();
          if (!silent) showToast(text || `Ошибка ${res.status}`, "error");
          throw new Error(text || `HTTP ${res.status}`);
        }
        return res.json();
      } catch (err) {
        if (!silent && !(err && err.message && err.message.includes("токен"))) {
          // Network/runtime errors not already toasted above
          if (err && err.name === "TypeError") {
            showToast("Нет связи с сервером.", "error");
          }
        }
        throw err;
      } finally {
        if (button) {
          button.disabled = false;
          button.classList.remove("is-loading");
          if (originalLabel) button.innerHTML = originalLabel;
        }
      }
    }

    function showLogin(message = "") {
      $("login-screen").classList.add("active");
      $("login-error").textContent = message;
      setTimeout(() => $("login-token").focus(), 50);
    }

    function hideLogin() {
      $("login-screen").classList.remove("active");
      $("login-error").textContent = "";
    }

    function applyRole() {
      const isAdmin = state.role === "admin";
      document.body.dataset.role = state.role || "guest";
      document.querySelectorAll(".admin-only").forEach(el => el.classList.toggle("admin-hidden", !isAdmin));
      if (!isAdmin && ["accounts", "settings", "share", "services"].includes(state.tab)) setTab("dashboard");
    }

    async function loadSession() {
      try {
        const session = await api("/api/session");
        state.role = session.role || "viewer";
        localStorage.setItem("pulse_role", state.role);
        hideLogin();
        applyRole();
        return true;
      } catch {
        applyRole();
        return false;
      }
    }

    async function loginWithToken() {
      const token = $("login-token").value.trim();
      if (!token) {
        $("login-error").textContent = "Введите токен.";
        return;
      }
      state.token = token;
      localStorage.setItem("pulse_token", token);
      const ok = await loadSession();
      if (ok) {
        startLive();
        refreshData();
      }
    }

    function logout() {
      if (state.liveSource) state.liveSource.close();
      state.token = "";
      state.role = "";
      localStorage.removeItem("pulse_token");
      localStorage.removeItem("pulse_role");
      document.cookie = "pulse_token=; Max-Age=0; path=/";
      applyRole();
      showLogin("Вы вышли. Введите токен для доступа.");
    }

    function setLiveStatus(status) {
      // status: "online" | "connecting" | "offline"
      state.liveStatus = status;
      const el = document.getElementById("live-status");
      if (el) {
        el.dataset.status = status;
        el.title = status === "online" ? "Live-обновления подключены"
          : status === "connecting" ? "Восстанавливаем соединение…"
          : "Live-обновления оффлайн";
      }
      document.body.dataset.liveStatus = status;
    }

    function startLive() {
      stopLive();
      state.liveReconnectAttempt = 0;
      _liveConnect();
    }

    function stopLive() {
      if (state._liveReconnectTimer) {
        clearTimeout(state._liveReconnectTimer);
        state._liveReconnectTimer = null;
      }
      if (state.liveSource) {
        try { state.liveSource.close(); } catch {}
        state.liveSource = null;
      }
      setLiveStatus("offline");
    }

    function _liveScheduleReconnect() {
      const attempt = (state.liveReconnectAttempt || 0) + 1;
      state.liveReconnectAttempt = attempt;
      // Exponential backoff 1s → 60s with ±20% jitter
      const base = Math.min(60, Math.pow(2, Math.min(attempt - 1, 6)));
      const jitter = base * 0.2 * (Math.random() - 0.5) * 2;
      const delayMs = Math.max(1000, Math.round((base + jitter) * 1000));
      setLiveStatus("connecting");
      state._liveReconnectTimer = setTimeout(_liveConnect, delayMs);
    }

    function _liveConnect() {
      state._liveReconnectTimer = null;
      if (!state.token && !document.cookie.includes("pulse_token=")) {
        // Not authenticated yet; defer.
        setLiveStatus("offline");
        return;
      }
      try {
        const source = new EventSource("/api/live");
        state.liveSource = source;
        const onChange = () => {
          if (["dashboard", "analytics", "debts"].includes(state.tab)) refreshData({ silent: true, preserveScroll: true });
        };
        source.addEventListener("open", () => {
          state.liveReconnectAttempt = 0;
          setLiveStatus("online");
        });
        source.addEventListener("ping", onChange);
        source.addEventListener("ping-updated", onChange);
        source.addEventListener("giveaway-candidate", onChange);
        source.addEventListener("obsidian-sync", onChange);
        source.addEventListener("channel-profile", onChange);
        source.addEventListener("settings-updated", onChange);
        source.addEventListener("reminder", (event) => {
          onChange();
          if (browserNotificationsEnabled()) {
            const data = safeJson(event.data, {});
            new Notification("Pulse Desk: дедлайн", { body: `${data.chat || "чат"} · ${fmtDate(data.deadline_at)}`, icon: "/static/favicon.svg" });
          }
        });
        source.onerror = () => {
          try { source.close(); } catch {}
          state.liveSource = null;
          _liveScheduleReconnect();
        };
      } catch {
        _liveScheduleReconnect();
      }
    }

    // Reconnect when tab becomes visible if currently offline
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.liveStatus !== "online" && !state._liveReconnectTimer) {
        state.liveReconnectAttempt = 0;
        _liveConnect();
      }
    });
    window.addEventListener("online", () => {
      if (state.liveStatus !== "online" && !state._liveReconnectTimer) {
        state.liveReconnectAttempt = 0;
        _liveConnect();
      }
    });
    window.addEventListener("offline", () => setLiveStatus("offline"));

    function setTab(tab) {
      const titles = {
        dashboard: ["Дашборд", "Упоминания, фильтры и быстрые действия"],
        debts: ["Долги", "Победы, где приз еще ожидает выдачи"],
        market: ["Маркет", "Курсы и история рынка"],
        analytics: ["Аналитика", "Статистика по источникам, часам и авторам"],
        share: ["Доступ друзьям", "Бесплатная HTTPS-ссылка через Cloudflare Quick Tunnel"],
        accounts: ["Аккаунты", "Сессии Telegram и удаленный вход"],
        services: ["Сервисы", "Запуск и мониторинг внешних рантаймов (Discord-бот)"],
        settings: ["Настройки", "Отслеживание, runtime-режимы и диагностика"]
      };
      if (!titles[tab]) tab = "dashboard";
      state.tab = tab;
      document.body.dataset.tab = tab;
      document.querySelectorAll(".tabs").forEach(el => {
        const active = el.id === tab;
        el.classList.toggle("active", active);
        // Mark section as a tabpanel for screen readers
        if (!el.hasAttribute("role")) el.setAttribute("role", "tabpanel");
        el.setAttribute("aria-hidden", active ? "false" : "true");
        if (active) el.removeAttribute("tabindex"); else el.setAttribute("tabindex", "-1");
      });
      document.querySelectorAll("[data-tab]").forEach(btn => {
        const active = btn.dataset.tab === tab;
        btn.classList.toggle("active", active);
        if (btn.hasAttribute("role")) btn.setAttribute("aria-selected", active ? "true" : "false");
      });
      $("page-title").textContent = titles[tab][0];
      $("page-subtitle").textContent = titles[tab][1];
      refreshData();
    }

    function setSettingsTab(tab = "tracking") {
      document.querySelectorAll("[data-settings-section]").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.settingsSection === tab);
      });
      document.querySelectorAll("[data-settings-tab]").forEach(btn => {
        btn.classList.toggle("primary", btn.dataset.settingsTab === tab);
      });
      state.settingsTab = tab;
    }

    function params(append = false) {
      saveFilters();
      const p = new URLSearchParams({
        limit: state.limit,
        offset: state.limit > 0 && append ? state.offset : 0,
        chat_type: $("type-filter").value,
        sort: $("sort-order").value,
        sort_by: $("sort-by").value,
        grouped: state.grouped
      });
      if ($("status-filter").value) p.set("status", $("status-filter").value);
      if ($("favorite-filter").value) p.set("favorite", $("favorite-filter").value);
      if ($("mention-filter").value.trim()) p.set("mention", $("mention-filter").value.trim());
      if ($("search-input").value.trim()) p.set("search", $("search-input").value.trim());
      if ($("tag-filter") && $("tag-filter").value) p.set("tag", $("tag-filter").value);
      return p.toString();
    }

    function currentFilterQuery() {
      return {
        search: $("search-input").value,
        chat_type: $("type-filter").value,
        status: $("status-filter").value,
        favorite: $("favorite-filter").value,
        mention: $("mention-filter").value,
        sort_by: $("sort-by").value,
        sort: $("sort-order").value
      };
    }

    function applyFilterQuery(query = {}) {
      const mapping = {
        search: "search-input",
        chat_type: "type-filter",
        status: "status-filter",
        favorite: "favorite-filter",
        mention: "mention-filter",
        sort_by: "sort-by",
        sort: "sort-order"
      };
      Object.entries(mapping).forEach(([key, id]) => { if ($(id) && query[key] !== undefined) $(id).value = query[key]; });
      saveFilters();
      loadPings(false);
    }

    async function loadTagFilter() {
      const select = $("tag-filter");
      if (!select) return;
      try {
        const tags = await api("/api/tags");
        if (!Array.isArray(tags) || !tags.length) { select.style.display = "none"; return; }
        const current = select.value;
        select.innerHTML = '<option value="">Все теги</option>' + tags.map(t => `<option value="${esc(t)}"${t === current ? " selected" : ""}>${esc(t)}</option>`).join("");
        select.style.display = "";
        select.parentElement && (select.parentElement.style.display = "");
      } catch { select.style.display = "none"; }
    }

    async function loadSavedFilters() {
      if (!$("saved-filters-list")) return;
      try {
        const data = await api("/api/saved-filters");
        $("saved-filters-list").innerHTML = (data.filters || []).map((item, index) => `
          <button class="btn chip" data-filter-index="${index}"><i data-lucide="bookmark"></i>${esc(item.name)}</button>
        `).join("") || "<span class='muted'>Сохраненных фильтров пока нет.</span>";
        state.savedFilters = data.filters || [];
      } catch {}
    }

    function browserNotificationsEnabled() {
      return localStorage.getItem("pulse_browser_notifications") === "1" && "Notification" in window && Notification.permission === "granted";
    }

    function maybeNotifyRows(rows) {
      if (!browserNotificationsEnabled() || !rows.length) return;
      const lastSeen = Number(localStorage.getItem("pulse_last_notified_id") || "0");
      const interesting = rows
        .filter(row => Number(row.id) > lastSeen && (Number(row.priority_score) >= 60 || Number(row.is_win) || Number(row.is_giveaway)))
        .sort((a, b) => Number(b.priority_score || 0) - Number(a.priority_score || 0));
      const maxId = Math.max(lastSeen, ...rows.map(row => Number(row.id || 0)));
      localStorage.setItem("pulse_last_notified_id", String(maxId));
      if (!interesting.length) return;
      const row = interesting[0];
      new Notification("Pulse Desk: важное упоминание", {
        body: `${row.chat || "чат"}: ${(row.text || "").slice(0, 120)}`,
        icon: "/static/favicon.svg"
      });
    }

    function hashText(value) {
      let hash = 0;
      const text = String(value || "");
      for (let i = 0; i < text.length; i += 1) {
        hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
      }
      return String(hash);
    }

    function setHtmlIfChanged(elementOrId, html) {
      const element = typeof elementOrId === "string" ? $(elementOrId) : elementOrId;
      if (!element) return false;
      const hash = hashText(html);
      if (element.dataset.renderHash === hash) return false;
      element.innerHTML = html;
      element.dataset.renderHash = hash;
      return true;
    }

    async function keepScrollStable(enabled, work) {
      if (!enabled) return work();
      const previousY = window.scrollY;
      const previousBehavior = document.documentElement.style.scrollBehavior;
      document.documentElement.style.scrollBehavior = "auto";
      try {
        const result = await work();
        requestAnimationFrame(() => window.scrollTo({ top: previousY, left: window.scrollX, behavior: "auto" }));
        return result;
      } finally {
        setTimeout(() => { document.documentElement.style.scrollBehavior = previousBehavior; }, 0);
      }
    }

