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
      if (!isAdmin && ["accounts", "settings", "share"].includes(state.tab)) setTab("dashboard");
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

    async function loadPings(append = false, options = {}) {
      const silent = Boolean(options.silent);
      const container = $("pings-list");
      if (!append) {
        state.offset = 0;
        if (!silent && !container.children.length) setHtmlIfChanged(container, loadingCards(6));
      }
      try {
        const rows = await api(`/api/pings?${params(append)}`);
        if (!append) maybeNotifyRows(rows);
        if (!append && !rows.length) {
          setHtmlIfChanged(container, emptyState("search-x", "Ничего не найдено", "Попробуйте снять часть фильтров или изменить поисковый запрос."));
          $("load-more-btn").classList.add("hidden");
          lucide.createIcons();
          return;
        }
        const html = rows.map(renderPing).join("");
        const changed = append
          ? setHtmlIfChanged(container, container.innerHTML + html)
          : setHtmlIfChanged(container, html);
        $("load-more-btn").classList.toggle("hidden", state.limit <= 0 || rows.length < state.limit);
        if (changed) lucide.createIcons();
      } catch (err) {
        if (!silent || !container.children.length) {
          setHtmlIfChanged(container, emptyState("triangle-alert", "Не удалось загрузить упоминания", err.message));
        }
        lucide.createIcons();
      }
    }

    async function markFilteredPingsRead() {
      const button = $("read-all-btn");
      if (!button) return;
      button.disabled = true;
      const previousHtml = button.innerHTML;
      button.innerHTML = `<i data-lucide="loader-circle"></i>Читаю...`;
      lucide.createIcons();
      try {
        const query = new URLSearchParams({ chat_type: $("type-filter").value || "all" });
        if ($("status-filter").value) query.set("status", $("status-filter").value);
        if ($("favorite-filter").value) query.set("favorite", $("favorite-filter").value);
        if ($("mention-filter").value.trim()) query.set("mention", $("mention-filter").value.trim());
        if ($("search-input").value.trim()) query.set("search", $("search-input").value.trim());
        query.set("only_new", "true");
        const result = await api(`/api/pings/mark-read?${query.toString()}`, { method: "POST" });
        button.innerHTML = `<i data-lucide="check-check"></i>${Number(result.changed || 0)} прочитано`;
        await refreshData({ silent: true, preserveScroll: true });
        setTimeout(() => {
          button.innerHTML = previousHtml;
          lucide.createIcons();
        }, 1600);
      } catch (err) {
        button.innerHTML = `<i data-lucide="triangle-alert"></i>Ошибка`;
        setTimeout(() => {
          button.innerHTML = previousHtml;
          lucide.createIcons();
        }, 2200);
      } finally {
        button.disabled = false;
        lucide.createIcons();
      }
    }

    const giveawayStatuses = {
      pending: { label: "ожидаю выдачи", shortLabel: "ожидаю", className: "pending", icon: "hourglass", color: "#f6c453" },
      claimed: { label: "забрал приз", shortLabel: "забрал", className: "claimed", icon: "badge-check", color: "#4ade80" },
      missed: { label: "не успел", shortLabel: "не успел", className: "missed", icon: "clock-alert", color: "#a1a1aa" },
      scam: { label: "скам", shortLabel: "скам", className: "scam", icon: "shield-alert", color: "#fb7185" },
      missed_unsubscribe: { label: "не успел по отписке", shortLabel: "отписка", className: "missed", icon: "user-x", color: "#a1a1aa" },
      missed_reply: { label: "не успел отписать", shortLabel: "ответ", className: "missed", icon: "message-square-x", color: "#a1a1aa" },
      closed: { label: "закрыто", shortLabel: "закрыто", className: "claimed", icon: "archive", color: "#94a3b8" }
    };

    function giveawayStatusMeta(value) {
      return giveawayStatuses[value] || giveawayStatuses.pending;
    }

    function statusMeta(value) {
      return ({
        new: { label: "новое", className: "good", icon: "sparkles" },
        read: { label: "прочитано", className: "info", icon: "check" },
        important: { label: "важное", className: "warn", icon: "flame" },
        ignored: { label: "скрыто", className: "", icon: "eye-off" },
        resolved: { label: "решено", className: "good", icon: "check-circle" }
      })[value] || { label: value || "new", className: "", icon: "circle" };
    }

    function chatTypeLabel(value) {
      return ({ private: "личка", group: "группа", channel: "канал" })[value] || value || "unknown";
    }

    function deadlineSourceLabel(value) {
      return ({
        channel_description: "описание канала",
        channel_description_missing: "не найдено в описании",
        channel_post_text: "текст поста канала",
        message_text: "текст сообщения",
        manual: "ручной"
      })[value] || "нет источника";
    }

    function toDatetimeLocal(value) {
      if (!value) return "";
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return "";
      const pad = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function fromDatetimeLocal(value) {
      if (!value) return "";
      return value.length === 16 ? `${value}:00` : value;
    }

    const actionStatuses = {
      new: "новое",
      to_check: "проверить",
      waiting_result: "ждет результата",
      claim_prize: "забрать приз",
      claimed: "приз забран",
      scam: "скам",
      missed: "пропущено",
      closed: "закрыто"
    };

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, Number(value || 0)));
    }

    function compactDate(value) {
      if (!value) return "нет даты";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "нет даты";
      return date.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    }

    function relativeTime(value) {
      if (!value) return "нет даты";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "нет даты";
      const diffSeconds = Math.round((Date.now() - date.getTime()) / 1000);
      const future = diffSeconds < 0;
      const seconds = Math.abs(diffSeconds);
      if (seconds < 60) return future ? "скоро" : "только что";
      const units = [
        [31536000, "г"],
        [2592000, "мес"],
        [86400, "д"],
        [3600, "ч"],
        [60, "мин"]
      ];
      const unit = units.find(([size]) => seconds >= size) || units[units.length - 1];
      const amount = Math.max(1, Math.floor(seconds / unit[0]));
      return future ? `через ${amount} ${unit[1]}` : `${amount} ${unit[1]} назад`;
    }

    function safeExternalLink(value) {
      const link = String(value || "").trim();
      if (!link || link.toLowerCase().startsWith("нет ")) return "";
      try {
        const parsed = new URL(link, window.location.origin);
        return ["http:", "https:", "tg:"].includes(parsed.protocol) ? parsed.href : "";
      } catch {
        return "";
      }
    }

    function initials(value) {
      const cleaned = String(value || "").replace(/^@/, "").trim();
      const parts = cleaned.split(/[\s._-]+/).filter(Boolean);
      const raw = parts.length > 1 ? `${parts[0][0] || ""}${parts[1][0] || ""}` : cleaned.slice(0, 2);
      return raw.toUpperCase() || "?";
    }

    function priorityMeta(priority, status) {
      const score = clamp(priority, 0, 100);
      if (score >= 90) return { label: "Критично", className: "bad", icon: "siren", hint: "Проверь первым" };
      if (score >= 60) return { label: "Важно", className: "warn", icon: "flame", hint: "Есть сильный сигнал" };
      if (status === "new") return { label: "Новое", className: "good", icon: "sparkles", hint: "Еще не разобрано" };
      return { label: "Обычное", className: "info", icon: "message-circle", hint: "Можно разобрать позже" };
    }

    function actionStatusMeta(value, fallbackLabel = "новое") {
      const labels = {
        new: { label: "Нужно разобрать", hint: "Открой карточку и реши, что делать", icon: "inbox", className: "info" },
        to_check: { label: "Проверить", hint: "Высокий сигнал или ручная проверка", icon: "search-check", className: "warn" },
        waiting_result: { label: "Ждет итогов", hint: "Следи за результатом розыгрыша", icon: "hourglass", className: "pending" },
        claim_prize: { label: "Забрать приз", hint: "Похоже на победу или окно выдачи", icon: "gift", className: "warn" },
        claimed: { label: "Приз забран", hint: "Закрыто успешно", icon: "badge-check", className: "claimed" },
        scam: { label: "Скам", hint: "Не тратить время", icon: "shield-alert", className: "scam" },
        missed: { label: "Пропущено", hint: "Срок или действие уже упущены", icon: "clock-alert", className: "missed" },
        closed: { label: "Закрыто", hint: "Работа по карточке завершена", icon: "archive", className: "claimed" }
      };
      return labels[value] || { label: actionStatuses[value] || fallbackLabel, hint: "Статус действия", icon: "list-checks", className: "info" };
    }

    function deadlineMeta(ping) {
      if (!ping.deadline_at) {
        return { label: "Дедлайн не найден", hint: deadlineSourceLabel(ping.deadline_source), icon: "calendar-x", className: "bad" };
      }
      const deadline = new Date(ping.deadline_at);
      if (Number.isNaN(deadline.getTime())) {
        return { label: compactDate(ping.deadline_at), hint: "Дата выглядит нестандартно", icon: "calendar-alert", className: "warn" };
      }
      const hoursLeft = (deadline.getTime() - Date.now()) / 3600000;
      if (hoursLeft < 0) return { label: "Просрочено", hint: compactDate(ping.deadline_at), icon: "calendar-x", className: "bad" };
      if (hoursLeft <= 6) return { label: "Скоро", hint: `${compactDate(ping.deadline_at)} · ${relativeTime(ping.deadline_at)}`, icon: "alarm-clock", className: "bad" };
      if (hoursLeft <= 24) return { label: "Сегодня", hint: `${compactDate(ping.deadline_at)} · ${relativeTime(ping.deadline_at)}`, icon: "calendar-clock", className: "warn" };
      return { label: compactDate(ping.deadline_at), hint: relativeTime(ping.deadline_at), icon: "calendar-check", className: "good" };
    }

    function renderMentionChips(mentions, limit = 5) {
      const safeMentions = Array.isArray(mentions) ? mentions : [];
      if (!safeMentions.length) return `<span class="muted">Нет username-меток</span>`;
      const chips = safeMentions.slice(0, limit).map(name => `<span class="badge mention">${esc(name)}</span>`).join("");
      const extra = safeMentions.length > limit ? `<span class="badge">+${safeMentions.length - limit}</span>` : "";
      return `${chips}${extra}`;
    }

    function renderGiveawayStatusControl(pingId, currentStatus) {
      const current = currentStatus || "pending";
      if (state.role !== "admin") {
        const meta = giveawayStatusMeta(currentStatus);
        return `<span class="badge ${meta.className}"><i data-lucide="${meta.icon}"></i>${meta.label}</span>`;
      }
      return `
        <div class="giveaway-status-control" aria-label="Статус розыгрыша">
          ${Object.entries(giveawayStatuses).map(([value, meta]) => `
            <button class="btn ${meta.className} ${current === value ? "active" : ""}" data-action="giveaway-status" data-id="${pingId}" data-status="${value}" title="${meta.label}" aria-label="${meta.label}">
              <i data-lucide="${meta.icon}"></i>${meta.shortLabel || meta.label}
            </button>
          `).join("")}
        </div>
      `;
    }

    function renderPing(ping) {
      const mentions = safeJson(ping.mentions, []);
      const isWin = Number(ping.is_win);
      const isGiveaway = Number(ping.is_giveaway) || isWin;
      const isFavorite = Number(ping.is_favorite);
      const status = statusMeta(ping.status);
      const priority = Number(ping.priority_score || 0);
      const priorityInfo = priorityMeta(priority, ping.status);
      const text = ping.text || "Нет текста";
      const chat = ping.chat || "Неизвестный чат";
      const avatar = initials(chat);
      const giveawayStatus = ping.giveaway_status || "pending";
      const giveawayMeta = giveawayStatusMeta(giveawayStatus);
      const actionInfo = actionStatusMeta(
        ping.action_status || (isWin ? "claim_prize" : isGiveaway ? "waiting_result" : priority >= 60 ? "to_check" : "new"),
        actionStatuses[ping.action_status] || "новое"
      );
      const deadline = deadlineMeta(ping);
      const openLink = safeExternalLink(ping.link);
      const reason = isWin
        ? "Похоже на победу или выдачу приза"
        : isGiveaway
          ? `Розыгрыш: ${giveawayMeta.label}`
          : ping.status === "new"
            ? "Новое входящее упоминание"
            : priority >= 60
              ? "Высокий приоритет по ключевым словам"
              : "Обычное упоминание в ленте";
      const priorityColor = isGiveaway ? giveawayMeta.color : isWin ? "#4ade80" : priority >= 60 ? "#fb7185" : "#29d3c2";
      const clippedText = text.length > 360 ? text.slice(0, 360).trimEnd() + "..." : text;
      const groupCount = Number(ping.group_count || 0);
      const detectedAt = ping.detected_at || ping.date;
      const messageDate = ping.date && ping.date !== detectedAt ? compactDate(ping.date) : "";
      const cardClasses = [
        "card",
        "ping",
        ping.status === "new" ? "is-new" : "",
        ping.status === "important" || priority >= 60 ? "is-important" : "",
        ping.status === "resolved" ? "is-resolved" : "",
        isGiveaway ? "is-giveaway giveaway-" + giveawayMeta.className : "",
        isWin ? "is-win" : "",
        isFavorite ? "is-favorite" : "",
        isGiveaway && isFavorite ? "is-favorite-giveaway" : ""
      ].filter(Boolean).join(" ");
      const actionButtons = state.role === "admin" ? `
          <button class="btn ping-action ${isFavorite ? "active" : ""}" data-action="favorite" data-id="${ping.id}" title="${isFavorite ? "Убрать из избранного" : "В избранное"}" aria-label="${isFavorite ? "Убрать из избранного" : "В избранное"}"><i data-lucide="star"></i></button>
          <button class="btn ping-action ${ping.status === "read" ? "active" : ""}" data-action="read" data-id="${ping.id}" title="Отметить прочитанным" aria-label="Отметить прочитанным"><i data-lucide="check"></i></button>
        ` : "";
      const openButton = openLink
        ? `<a class="btn ping-action ping-open" href="${esc(openLink)}" target="_blank" rel="noopener" title="Открыть в Telegram" aria-label="Открыть в Telegram"><i data-lucide="external-link"></i></a>`
        : "";
      return `
        <article class="${cardClasses}" style="--metric-color:${priorityColor};--priority-width:${clamp(priority, 0, 100)}%" data-ping='${esc(JSON.stringify(ping))}'>
          <div class="ping-topline">
            <div class="ping-identity">
              <span class="ping-avatar">${esc(avatar)}</span>
              <div>
                <div class="ping-title-row">
                  <div class="ping-title">${esc(chat)}</div>
                  <span class="badge ${isWin ? "good" : isGiveaway ? giveawayMeta.className : status.className} ping-status"><i data-lucide="${isWin ? "trophy" : isGiveaway ? giveawayMeta.icon : status.icon}"></i>${esc(isWin ? "победа" : isGiveaway ? "giveaway" : status.label)}</span>
                </div>
                <div class="ping-meta">
                  <span><i data-lucide="user-round"></i>${esc(ping.sender || "неизвестно")}</span>
                  <span><i data-lucide="radio"></i>${esc(compactDate(detectedAt))}</span>
                  <span>${esc(relativeTime(detectedAt))}</span>
                </div>
              </div>
            </div>
            <div class="ping-actions">${actionButtons}${openButton}</div>
          </div>

          <div class="ping-signal-row">
            <div class="ping-priority ${priorityInfo.className}">
              <span class="metric-icon"><i data-lucide="${priorityInfo.icon}"></i></span>
              <div>
                <span>Приоритет</span>
                <strong>${priority}/100 · ${priorityInfo.label}</strong>
                <div class="priority-meter"><span></span></div>
              </div>
            </div>
            <div class="ping-next-step ${actionInfo.className}">
              <span class="metric-icon"><i data-lucide="${actionInfo.icon}"></i></span>
              <div>
                <span>Следующее действие</span>
                <strong>${esc(actionInfo.label)}</strong>
                <small>${esc(actionInfo.hint)}</small>
              </div>
            </div>
          </div>

          <div class="ping-reason"><i data-lucide="${isWin ? "trophy" : isGiveaway ? giveawayMeta.icon : priority >= 60 ? "flame" : "message-circle"}"></i>${esc(reason)}</div>

          <div class="ping-message">${esc(clippedText)}</div>

          <div class="ping-detail-grid">
            <div class="ping-detail">
              <span>Источник</span>
              <strong>${esc(chatTypeLabel(ping.chat_type))}</strong>
              <small>${messageDate ? `Пост: ${esc(messageDate)}` : "Дата сообщения совпадает"}</small>
            </div>
            <div class="ping-detail">
              <span>Упоминания</span>
              <div class="ping-mentions">${renderMentionChips(mentions)}</div>
            </div>
            ${isGiveaway ? `
              <div class="ping-detail ping-deadline ${deadline.className}">
                <span>${ping.reminder_at ? "Дедлайн и напоминание" : "Дедлайн"}</span>
                <strong><i data-lucide="${deadline.icon}"></i>${esc(deadline.label)}</strong>
                <small>${esc(deadline.hint)}${ping.reminder_at ? ` · напомнить ${esc(compactDate(ping.reminder_at))}` : ""}</small>
              </div>
            ` : ""}
          </div>

          ${isGiveaway ? `<div class="ping-giveaway-control">${renderGiveawayStatusControl(ping.id, giveawayStatus)}</div>` : ""}

          <div class="badges ping-tags">
            <span class="badge info">${esc(chatTypeLabel(ping.chat_type))}</span>
            ${isGiveaway ? `<span class="badge ${giveawayMeta.className}"><i data-lucide="${giveawayMeta.icon}"></i>${giveawayMeta.label}</span>` : `<span class="badge ${priority >= 60 ? "warn" : "good"}">приоритет ${priority}</span>`}
            ${isFavorite ? `<span class="badge warn"><i data-lucide="star"></i>избранное</span>` : ""}
            ${isGiveaway ? `<span class="badge warn">розыгрыш</span>` : ""}
            ${groupCount > 1 ? `<span class="badge info"><i data-lucide="layers"></i>${groupCount} в чате</span>` : ""}
            ${ping.note ? `<span class="badge">заметка</span>` : ""}
            ${ping.action_status ? `<span class="badge info">${esc(actionStatuses[ping.action_status] || ping.action_status)}</span>` : ""}
            ${ping.auto_joined ? `<span class="badge good">вступил</span>` : ""}
          </div>
        </article>`;
    }

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

    function debtProfileRows(data, profileKey) {
      if (!profileKey || profileKey === "all") return data.rows || [];
      const profile = (data.profiles || []).find(item => item.key === profileKey);
      return profile ? profile.rows || [] : data.rows || [];
    }

    function renderDebtStats(data) {
      const stats = data.stats || {};
      setHtmlIfChanged("debts-stats", [
        ["Ждут выдачи", stats.total, "receipt-text", "#f6c453"],
        ["Новые", stats.new, "sparkles", "#45d483"],
        ["Критично", stats.critical, "siren", "#fb7185"],
        ["Профилей с долгом", stats.profiles_with_debt, "at-sign", "#29d3c2"]
      ].map(([label, value, icon, color]) => `
        <div class="panel metric-card" style="--metric-color:${color}">
          <div class="metric-top"><div class="metric-label">${label}</div><span class="metric-icon"><i data-lucide="${icon}"></i></span></div>
          <div class="metric-value">${value ?? 0}</div>
        </div>
      `).join(""));
    }

    function renderDebtProfiles(data) {
      const profiles = data.profiles || [];
      const active = state.debtProfile || "all";
      const allCount = Number(data.stats?.total || 0);
      const allNew = Number(data.stats?.new || 0);
      const buttons = [`
        <button class="${active === "all" ? "active" : ""}" data-debt-profile="all">
          <span class="profile-name"><i data-lucide="layers"></i>Все долги</span>
          <span class="profile-count">${allCount}</span>
          ${allNew ? `<small>${allNew} новых</small>` : "<small>общая очередь</small>"}
        </button>
      `].concat(profiles.map(profile => {
        const selected = active === profile.key;
        const count = Number(profile.count || 0);
        const critical = Number(profile.critical_count || 0);
        return `
          <button class="${selected ? "active" : ""} ${count ? "has-debt" : ""}" data-debt-profile="${esc(profile.key)}">
            <span class="profile-name"><i data-lucide="${count ? "trophy" : "at-sign"}"></i>@${esc(profile.username)}</span>
            <span class="profile-count">${count}</span>
            <small>${critical ? `${critical} критично` : `prio ${Number(profile.max_priority || 0)}`}</small>
          </button>
        `;
      })).join("");
      setHtmlIfChanged("debt-profile-tabs", buttons);
    }

    function debtItem(row) {
      const mentions = safeJson(row.mentions, []);
      const priority = Number(row.priority_score || 0);
      const priorityInfo = priorityMeta(priority, row.status);
      const deadline = deadlineMeta(row);
      const status = giveawayStatusMeta(row.giveaway_status || "pending");
      const text = (row.text || "").replace(/\s+/g, " ").trim();
      const clipped = text.length > 260 ? text.slice(0, 260) + "..." : text;
      const openLink = safeExternalLink(row.link);
      const source = row.chat || "Неизвестный источник";
      const primaryMention = mentions[0] ? `@${String(mentions[0]).replace(/^@/, "")}` : "username не найден";
      const detected = row.detected_at ? fmtDate(row.detected_at) : "";
      const deadlineLabel = row.deadline_at ? deadline.label : "Срок выдачи не найден";
      const actions = state.role === "admin" ? `
        <div class="board-actions debt-actions">
          <button class="btn good" data-debt-status="claimed" data-id="${row.id}"><i data-lucide="badge-check"></i>Забрал</button>
          <button class="btn" data-debt-status="missed_reply" data-id="${row.id}"><i data-lucide="message-square-x"></i>Не отписал</button>
          <button class="btn" data-debt-status="missed" data-id="${row.id}"><i data-lucide="clock-alert"></i>Не успел</button>
          <button class="btn bad" data-debt-status="scam" data-id="${row.id}"><i data-lucide="shield-alert"></i>Скам</button>
        </div>
      ` : "";
      return `
        <div class="board-item debt-item" data-ping='${esc(JSON.stringify(row))}'>
          <div class="debt-card-head">
            <span class="debt-source-mark">${esc(initials(source))}</span>
            <div class="debt-title-block">
              <span>Победа · ${esc(primaryMention)}</span>
              <strong>${esc(source)}</strong>
              ${detected ? `<small>${esc(detected)}</small>` : ""}
            </div>
            <span class="debt-status-pill ${status.className}"><i data-lucide="${status.icon}"></i>${esc(status.shortLabel || status.label)}</span>
          </div>
          <div class="debt-signal-grid">
            <div class="debt-signal priority ${priorityInfo.className}" style="--priority-width:${clamp(priority, 0, 100)}%">
              <span class="metric-icon"><i data-lucide="${priorityInfo.icon}"></i></span>
              <div>
                <span>Приоритет</span>
                <strong>${priority}/100 · ${esc(priorityInfo.label)}</strong>
                <div class="priority-meter"><span></span></div>
              </div>
            </div>
            <div class="debt-signal deadline ${deadline.className}">
              <span class="metric-icon"><i data-lucide="${deadline.icon}"></i></span>
              <div>
                <span>Выдача</span>
                <strong>${esc(deadlineLabel)}</strong>
                <small>${esc(deadline.hint || "проверь вручную")}</small>
              </div>
            </div>
          </div>
          <div class="debt-meta-row">
            <div class="ping-mentions">${renderMentionChips(mentions, 6)}</div>
            ${openLink ? `<a class="badge debt-telegram-link" href="${openLink}" target="_blank" rel="noopener"><i data-lucide="external-link"></i>Telegram</a>` : ""}
          </div>
          <div class="debt-preview">
            <span>Текст результата</span>
            <p>${esc(clipped || "Нет текста")}</p>
          </div>
          <div class="debt-footer">
            <div class="debt-context-badges">
              <span class="badge info"><i data-lucide="trophy"></i>победа</span>
              <span class="badge ${row.status === "new" ? "good" : "info"}">${esc(statusMeta(row.status).label)}</span>
            </div>
            ${actions}
          </div>
        </div>
      `;
    }

    function renderDebts(data) {
      state.debtBoard = data;
      const validKeys = new Set(["all"].concat((data.profiles || []).map(item => item.key)));
      if (!validKeys.has(state.debtProfile)) state.debtProfile = "all";
      const activeProfile = (data.profiles || []).find(item => item.key === state.debtProfile);
      const rows = debtProfileRows(data, state.debtProfile);
      renderDebtStats(data);
      renderDebtProfiles(data);
      $("debts-active-kicker").textContent = activeProfile ? `@${activeProfile.username}` : "Все профили";
      $("debts-active-title").textContent = activeProfile ? "Победы этого username" : "Ожидают выдачи";
      $("debts-active-count").textContent = rows.length;
      setHtmlIfChanged("debts-list", rows.length
        ? rows.map(debtItem).join("")
        : emptyState("badge-check", "Долгов нет", "Все найденные призы закрыты или еще не появились в статусе “ожидаю выдачи”."));
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
      const dryRun = runtime.dry_run_giveaways !== false;
      if ($("settings-summary-line")) {
        $("settings-summary-line").textContent = `${usernames.length || 0} usernames · источник ${source} · dry-run ${dryRun ? "включен" : "выключен"}`;
      }
      if ($("settings-summary")) {
        $("settings-summary").innerHTML = `
          <div class="summary-tile"><span>Usernames</span><strong>${usernames.length || 0}</strong></div>
          <div class="summary-tile"><span>Скан</span><strong>${scanInterval ? scanInterval + " сек." : "..."}</strong></div>
          <div class="summary-tile"><span>Параллель</span><strong>${scanConcurrency || "..."}</strong></div>
          <div class="summary-tile"><span>Лимит истории</span><strong>${scanLimit > 0 ? scanLimit : "без лимита"}</strong></div>
          <div class="summary-tile"><span>Правки</span><strong>${editSweep > 0 ? editSweep : "off"}</strong></div>
          <div class="summary-tile"><span>Розыгрыши</span><strong>${dryRun ? "dry-run" : "live"}</strong></div>
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
      $("runtime-dry-run").checked = values.dry_run_giveaways !== false;
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
        dry_run_giveaways: $("runtime-dry-run").checked,
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
      membersBox.innerHTML = members.length ? `<div class="backup-list">${members.map(m => `
        <div class="backup-item">
          <div class="row"><strong>${esc(m.name || "—")}</strong><span class="badge">${m.tg_username ? "@" + esc(m.tg_username) : "—"}</span><span class="badge ${m.blocked ? "bad" : "good"}">${m.blocked ? "заблокирован" : "активен"}</span></div>
          <div class="deadline-row" style="gap:.4rem;flex-wrap:wrap">
            <span class="muted">ключ: ${esc(m.key_label || "—")} · ${m.last_seen_at ? fmtDate(m.last_seen_at) : "—"}</span>
            <button class="btn ${m.blocked ? "" : "bad"}" data-block-member="${m.tg_id}" data-blocked="${m.blocked ? 1 : 0}"><i data-lucide="${m.blocked ? "user-check" : "user-x"}"></i>${m.blocked ? "Разблокировать" : "Заблокировать"}</button>
          </div>
        </div>`).join("")}</div>` : "<div class='muted'>Пользователей пока нет.</div>";
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
          if (state.tab === "debts") await loadDebts();
          if (state.tab === "market" && !silent) await loadMarket();
          if (state.tab === "analytics") await loadAnalytics();
          if (state.tab === "share" && !silent) await loadShareGuide();
          if (state.tab === "accounts" && !silent) await loadAccounts();
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
    $("browser-notify-btn").addEventListener("click", async () => {
      if (!("Notification" in window)) return;
      const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (permission === "granted") {
        localStorage.setItem("pulse_browser_notifications", "1");
        $("browser-notify-btn").classList.add("primary");
      }
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
    $("debt-profile-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-debt-profile]");
      if (!btn) return;
      state.debtProfile = btn.dataset.debtProfile || "all";
      if (state.debtBoard) renderDebts(state.debtBoard);
    });
    $("debts-list").addEventListener("click", async (e) => {
      const statusBtn = e.target.closest("button[data-debt-status]");
      if (statusBtn) {
        e.stopPropagation();
        const status = statusBtn.dataset.debtStatus;
        const mappedAction = { claimed: "claimed", scam: "scam", missed: "missed", missed_reply: "missed" }[status] || "missed";
        await api(`/api/pings/${statusBtn.dataset.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ giveaway_status: status, action_status: mappedAction })
        });
        await loadDebts();
        await loadDashboardSummary();
        return;
      }
      if (e.target.closest("a")) return;
      const item = e.target.closest(".debt-item[data-ping]");
      if (item) openModal(JSON.parse(item.dataset.ping));
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
    });
    $("copy-logs-btn").addEventListener("click", () => navigator.clipboard.writeText($("logs-box").textContent));
    $("export-json-btn").addEventListener("click", async () => {
      const data = await api(`/api/export-json?${params(false)}`);
      downloadJson(`pulse_pings_${new Date().toISOString().slice(0, 10)}.json`, data);
    });

    document.getElementById('btn-tg-digest')?.addEventListener('click', async () => {
        const btn = document.getElementById('btn-tg-digest');
        btn.disabled = true;
        btn.textContent = 'Отправляю…';
        try {
            const hours = document.getElementById('digest-hours')?.value || '24';
            const res = await api(`/api/export/telegram-digest?hours=${hours}`, { method: 'POST' });
            btn.textContent = `✅ Отправлено (${res.pings_count} пингов)`;
        } catch (e) {
            btn.textContent = '❌ Ошибка';
            console.error(e);
        }
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = '📤 Дайджест в Telegram';
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
      if (browserNotificationsEnabled()) $("browser-notify-btn").classList.add("primary");
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
        const btn = document.getElementById('btn-push-subscribe');
        if (btn) btn.textContent = '🔕 Отписаться';
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
        const btn = document.getElementById('btn-push-subscribe');
        if (btn) btn.textContent = '🔔 Уведомления';
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
            if (btn && sub) btn.textContent = '🔕 Отписаться';
        } catch {}
    })();
