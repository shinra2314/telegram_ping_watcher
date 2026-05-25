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

    async function api(url, options = {}) {
      const headers = Object.assign({}, options.headers || {});
      if (state.token) headers["X-Pulse-Token"] = state.token;
      const res = await fetch(apiUrl(url), Object.assign({}, options, { headers }));
      if (res.status === 401 || res.status === 403) {
        const message = res.status === 403 ? "Для этого действия нужен admin-токен." : "Нужен токен доступа.";
        showLogin(message);
        throw new Error(message);
      }
      if (!res.ok) throw new Error(await res.text());
      return res.json();
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

    function startLive() {
      if (state.liveSource) state.liveSource.close();
      try {
        const source = new EventSource("/api/live");
        state.liveSource = source;
        const onChange = () => {
          if (["dashboard", "analytics"].includes(state.tab)) refreshData({ silent: true, preserveScroll: true });
        };
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
          source.close();
          state.liveSource = null;
        };
      } catch {}
    }

    function setTab(tab) {
      const titles = {
        dashboard: ["Дашборд", "Упоминания, фильтры и быстрые действия"],
        giveaways: ["Розыгрыши", "Очереди действий, дедлайны и безопасный разбор"],
        tasks: ["Задачи", "Дедлайны, напоминания и открытые розыгрыши"],
        market: ["Маркет", "Курсы и история рынка"],
        analytics: ["Аналитика", "Статистика по источникам, часам и авторам"],
        share: ["Доступ друзьям", "Бесплатная HTTPS-ссылка через Cloudflare Quick Tunnel"],
        accounts: ["Аккаунты", "Сессии Telegram и удаленный вход"],
        settings: ["Настройки", "Отслеживание, runtime-режимы и диагностика"]
      };
      if (!titles[tab]) tab = "dashboard";
      state.tab = tab;
      document.body.dataset.tab = tab;
      document.querySelectorAll(".tabs").forEach(el => el.classList.toggle("active", el.id === tab));
      document.querySelectorAll("[data-tab]").forEach(btn => btn.classList.toggle("active", btn.dataset.tab === tab));
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
      pending: { label: "ожидаю выдачи", className: "pending", icon: "hourglass", color: "#f6c453" },
      claimed: { label: "забрал приз", className: "claimed", icon: "badge-check", color: "#4ade80" },
      missed: { label: "не успел", className: "missed", icon: "clock-alert", color: "#a1a1aa" },
      scam: { label: "скам", className: "scam", icon: "shield-alert", color: "#fb7185" },
      missed_unsubscribe: { label: "не успел по отписке", className: "missed", icon: "user-x", color: "#a1a1aa" },
      missed_reply: { label: "не успел отписать", className: "missed", icon: "message-square-x", color: "#a1a1aa" },
      closed: { label: "закрыто", className: "claimed", icon: "archive", color: "#94a3b8" }
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

    function renderGiveawayStatusControl(pingId, currentStatus) {
      const current = giveawayStatusMeta(currentStatus).className;
      if (state.role !== "admin") {
        const meta = giveawayStatusMeta(currentStatus);
        return `<span class="badge ${meta.className}"><i data-lucide="${meta.icon}"></i>${meta.label}</span>`;
      }
      return `
        <div class="giveaway-status-control" aria-label="Статус розыгрыша">
          ${Object.entries(giveawayStatuses).map(([value, meta]) => `
            <button class="btn ${meta.className} ${current === meta.className ? "active" : ""}" data-action="giveaway-status" data-id="${pingId}" data-status="${value}" title="${meta.label}">
              <i data-lucide="${meta.icon}"></i>${meta.label}
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
      const text = ping.text || "Нет текста";
      const chat = ping.chat || "Неизвестный чат";
      const avatar = chat.replace(/^@/, "").trim().slice(0, 2) || "?";
      const giveawayStatus = ping.giveaway_status || "pending";
      const giveawayMeta = giveawayStatusMeta(giveawayStatus);
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
      const clippedText = text.length > 260 ? text.slice(0, 260) + "..." : text;
      const mentionBadges = mentions.slice(0, 4).map(m => `<span class="badge">${esc(m)}</span>`).join("");
      const extraMentions = mentions.length > 4 ? `<span class="badge">+${mentions.length - 4}</span>` : "";
      const deadline = ping.deadline_at ? fmtDate(ping.deadline_at) : "";
      const deadlineInfo = isGiveaway
        ? `<div class="deadline-row">
            <span class="badge ${ping.deadline_at ? "warn" : "bad"}"><i data-lucide="calendar-clock"></i>${deadline || "дедлайн не найден"}</span>
            <span class="badge">${esc(deadlineSourceLabel(ping.deadline_source))}</span>
            ${ping.reminder_at ? `<span class="badge info"><i data-lucide="bell"></i>${fmtDate(ping.reminder_at)}</span>` : ""}
          </div>`
        : "";
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
        <div class="actions">
          <button class="btn ping-action" data-action="favorite" data-id="${ping.id}" title="Избранное" aria-label="Избранное"><i data-lucide="star"></i></button>
          <button class="btn ping-action" data-action="read" data-id="${ping.id}" title="Прочитано" aria-label="Прочитано"><i data-lucide="check"></i></button>
        </div>` : "";
      return `
        <article class="${cardClasses}" style="--metric-color:${priorityColor}" data-ping='${esc(JSON.stringify(ping))}'>
          <div class="ping-head">
            <div class="ping-identity">
              <span class="ping-avatar">${esc(avatar)}</span>
              <div>
                <div class="ping-title-row">
                  <div class="ping-title">${esc(chat)}</div>
                  <span class="badge ${status.className} ping-status"><i data-lucide="${status.icon}"></i>${esc(status.label)}</span>
                </div>
                <div class="ping-meta"><span>${esc(ping.sender || "неизвестно")}</span><span>${fmtDate(ping.detected_at || ping.date)}</span></div>
              </div>
            </div>
            ${actionButtons}
          </div>
          <div class="ping-reason"><i data-lucide="${isWin ? "trophy" : isGiveaway ? giveawayMeta.icon : priority >= 60 ? "flame" : "message-circle"}"></i>${esc(reason)}</div>
          ${isGiveaway ? renderGiveawayStatusControl(ping.id, giveawayStatus) : ""}
          ${deadlineInfo}
          <div class="ping-text">${esc(clippedText)}</div>
          <div class="badges">
            <span class="badge info">${esc(chatTypeLabel(ping.chat_type))}</span>
            ${isGiveaway ? `<span class="badge ${giveawayMeta.className}"><i data-lucide="${giveawayMeta.icon}"></i>${giveawayMeta.label}</span>` : `<span class="badge ${priority >= 60 ? "warn" : "good"}">приоритет ${priority}</span>`}
            ${isFavorite ? `<span class="badge warn"><i data-lucide="star"></i>избранное</span>` : ""}
            ${isGiveaway ? `<span class="badge warn">розыгрыш</span>` : ""}
            ${ping.note ? `<span class="badge">заметка</span>` : ""}
            ${ping.action_status ? `<span class="badge info">${esc(actionStatuses[ping.action_status] || ping.action_status)}</span>` : ""}
            ${ping.auto_joined ? `<span class="badge good">вступил</span>` : ""}
            ${mentionBadges}${extraMentions}
          </div>
        </article>`;
    }

    function safeJson(value, fallback) {
      try { return typeof value === "string" ? JSON.parse(value) : value || fallback; } catch { return fallback; }
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

    async function loadAnalytics() {
      const data = await api("/api/analytics");
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
      const metricsHtml = metrics.map(([label, value, icon, color]) => `
        <div class="panel metric-card" style="--metric-color:${color}">
          <div class="metric-top">
            <div class="metric-label">${label}</div>
            <span class="metric-icon"><i data-lucide="${icon}"></i></span>
          </div>
          <div class="metric-value">${value ?? 0}</div>
        </div>
      `).join("");
      setHtmlIfChanged("stats-row", metricsHtml);
      setHtmlIfChanged("analytics-stats-row", metricsHtml);
      renderDashboardInsight(data);
      if (state.tab !== "analytics") return;
      const detailed = await api("/api/analytics/detailed");
      drawChart("dailyChart", "bar", data.daily.slice().reverse().map(x => x.day), data.daily.slice().reverse().map(x => x.count), "#2dd4bf");
      const hours = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
      drawChart("hourlyChart", "bar", hours, hours.map(h => data.hourly[h] || 0), "#f4b44d");
      drawChart("typeChart", "doughnut", ["Личка", "Группы", "Каналы"], [data.by_type.private || 0, data.by_type.group || 0, data.by_type.channel || 0], ["#2dd4bf", "#45d483", "#f4b44d"]);
      const channelAccounts = detailed.channels_by_account || data.channels_by_account || [];
      const channelAccountTotal = Number(detailed.channel_memberships_total || data.channel_memberships_total || 0);
      setHtmlIfChanged("channels-by-account-list", channelAccounts.length ? `
        <div class="source-item">
          <div class="row"><strong>Всего каналов по аккаунтам</strong><span class="badge good">${channelAccountTotal}</span></div>
          <div class="deadline-row"><span class="badge">уникальных в базе: ${Number(data.channel_chats_total || 0)}</span><span class="badge">учтено аккаунтов: ${channelAccounts.length}</span></div>
        </div>
        ${channelAccounts.map(a => `
        <div class="row panel">
          <strong>${esc(a.display || a.session_name || "аккаунт")}</strong>
          <span class="badge ${a.status === "online" ? "good" : "warn"}">${esc(a.status || "unknown")}</span>
          <span class="badge">${Number(a.channels || 0)} каналов</span>
          <span class="muted">${a.last_channel_scan_at ? fmtDate(a.last_channel_scan_at) : "скан еще не считал каналы"}</span>
        </div>
      `).join("")}
      ` : "<div class='muted'>Каналы по аккаунтам появятся после ближайшего скана истории.</div>");
      setHtmlIfChanged("senders-list", detailed.senders.map(s => `<div class="row panel"><strong>${esc(s.sender || "неизвестно")}</strong><span class="badge">${s.count} всего</span><span class="badge warn">${s.wins} побед</span></div>`).join("") || "<div class='muted'>Данных пока нет.</div>");
      setHtmlIfChanged("valuable-chats-list", (detailed.chats || []).map(c => `<div class="row panel"><strong>${esc(c.chat || "неизвестно")}</strong><span class="badge">${c.count} всего</span><span class="badge warn">${c.wins} побед</span><span class="badge">prio ${Number(c.avg_priority || 0).toFixed(1)}</span></div>`).join("") || "<div class='muted'>Данных пока нет.</div>");
      setHtmlIfChanged("sources-list", (detailed.sources || []).map(s => `
        <div class="source-item">
          <div class="row"><strong>${esc(s.chat || "неизвестно")}</strong><span class="badge good">score ${Number(s.score || 0).toFixed(1)}</span></div>
          <div class="deadline-row"><span class="badge">${s.total_pings || 0} всего</span><span class="badge warn">${s.giveaways || 0} розыгрышей</span><span class="badge good">${s.wins || 0} побед</span><span class="badge bad">${s.noise || 0} шум</span></div>
        </div>
      `).join("") || "<div class='muted'>Источники еще не рассчитаны.</div>");
      setHtmlIfChanged("priority-list", (detailed.priorities || []).map(p => `<div class="row panel"><strong>${esc(p.priority_label || "normal")}</strong><span class="badge">${p.count}</span></div>`).join("") || "<div class='muted'>Данных пока нет.</div>");
      setHtmlIfChanged("mentions-list", (detailed.top_mentions || []).map(m => `<div class="row panel"><strong>@${esc(m.username || "")}</strong><span class="badge">${m.count} упоминаний</span></div>`).join("") || "<div class='muted'>Упоминаний пока нет.</div>");
      setHtmlIfChanged("status-flow-list", (detailed.status_flow || []).map(s => `<div class="row panel"><strong>${esc(s.status || "unknown")}</strong><span class="badge info">${esc(s.action_status || "new")}</span><span class="badge">${s.count}</span></div>`).join("") || "<div class='muted'>Статусов пока нет.</div>");
    }

    async function loadMarket() {
      const latest = await api("/api/market");
      if (!latest.length) {
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
      $("market-cards").innerHTML = coins.map(([id, label]) => marketCard(m, id, label, baseline)).join("");
      $("market-table").innerHTML = `<thead><tr><th>Актив</th><th>USD</th><th>UAH</th><th>24h</th><th>Период</th><th>Обновлено</th></tr></thead><tbody>${coins.map(([id, label]) => marketRow(m, id, label, baseline)).join("")}</tbody>`;
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

    function marketCard(m, id, label, baseline) {
      const d = m[id] || {};
      const c = d.usd_24h_change || 0;
      const period = marketChange(d, baseline?.[id]);
      return `<div class="panel metric-card" style="--metric-color:${c >= 0 ? "#45d483" : "#f97373"}"><div class="metric-top"><div class="metric-label">${label} / USD</div><span class="badge ${c >= 0 ? "good" : "bad"}">${c >= 0 ? "+" : ""}${c.toFixed(2)}%</span></div><div class="metric-value">$${Number(d.usd || 0).toLocaleString()}</div><div class="deadline-row"><span class="badge ${period >= 0 ? "good" : "bad"}">период ${period >= 0 ? "+" : ""}${period.toFixed(2)}%</span><span class="badge">${Number(d.uah || 0).toLocaleString()} UAH</span></div></div>`;
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
        data: { labels, datasets: [{ data, borderColor: Array.isArray(color) ? undefined : color, backgroundColor: type === "line" ? hexToRgba(primary, .15) : color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: .35, fill: type === "line" }] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          resizeDelay: 120,
          plugins: { legend: { display: false }, tooltip: { backgroundColor: "#101720", borderColor: "rgba(207,219,232,.18)", borderWidth: 1, titleColor: "#ffffff", bodyColor: "#dce6ec", displayColors: false } },
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
      await loadDiagnostics();
      await loadLogs();
      await loadEvents();
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
        $("type-filter").value = "giveaway";
        setTab("dashboard");
        loadPings(false);
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
      const card = e.target.closest(".card[data-ping]");
      if (card) openModal(JSON.parse(card.dataset.ping));
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
      $("modal").classList.remove("active");
      document.body.classList.remove("modal-open");
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
      document.body.classList.add("modal-open");
      $("modal").classList.add("active");
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
    $("refresh-giveaways-btn").addEventListener("click", loadGiveawayBoard);
    $("refresh-diagnostics-btn").addEventListener("click", loadDiagnostics);
    $("refresh-backups-btn").addEventListener("click", loadBackups);
    $("create-backup-btn").addEventListener("click", async () => {
      await api("/api/backups/create", { method: "POST" });
      await loadBackups();
    });
    $("copy-logs-btn").addEventListener("click", () => navigator.clipboard.writeText($("logs-box").textContent));
    $("export-json-btn").addEventListener("click", async () => {
      const data = await api(`/api/export-json?${params(false)}`);
      downloadJson(`pulse_pings_${new Date().toISOString().slice(0, 10)}.json`, data);
    });

    async function initApp() {
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
