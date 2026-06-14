/* Pulse Desk frontend — app-pings.js: pings list: loading, mark-read, render helpers, status/deadline meta.
   Classic script (no modules): top-level bindings are shared across the app-*.js files,
   which must load in the order set in index.html. Split from the former app.js. */

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

