(() => {
  const { $, esc, fmtDate, emptyState } = window.PulseCore;

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

  const giveawayStatuses = {
    pending: ["ожидаю", "warn"],
    claimed: ["забрал", "good"],
    missed: ["не успел", ""],
    scam: ["скам", "bad"],
    missed_unsubscribe: ["не успел отписаться", ""],
    missed_reply: ["не успел отписать", ""],
    closed: ["закрыто", "good"]
  };

  const workflowHints = {
    manual_review: ["ручная проверка", "bad"],
    claim_prize: ["забрать приз", "warn"],
    set_deadline: ["поставить дедлайн", "bad"],
    recommended: ["рекомендовано", "good"],
    watch: ["наблюдать", "info"],
    closed: ["закрыто", "good"]
  };

  const deadlineSources = {
    channel_description: "описание канала",
    channel_post_text: "текст поста",
    message_text: "текст сообщения",
    claim_window_text: "окно получения",
    manual: "ручной",
    channel_description_missing: "не найден"
  };

  const stageLabels = {
    claim: ["получить приз", "warn"],
    overdue: ["просрочено", "bad"],
    soon: ["скоро итоги", "warn"],
    waiting: ["ждет итогов", "info"],
    missing_deadline: ["нет дедлайна", "bad"],
    manual: ["ручная проверка", "bad"],
    done: ["закрыто", "good"]
  };

  function sourceLabel(value) {
    return deadlineSources[value] || value || "источник неизвестен";
  }

  function timeLeftLabel(row) {
    if (!row.deadline_at) return row.is_claim ? "срок получения не найден" : "дедлайн не найден";
    const seconds = Number(row.deadline_seconds);
    if (!Number.isFinite(seconds)) return fmtDate(row.deadline_at);
    const abs = Math.abs(seconds);
    const minutes = Math.max(1, Math.round(abs / 60));
    const hours = Math.round(abs / 3600);
    const days = Math.round(abs / 86400);
    const unit = days >= 2 ? `${days} дн.` : hours >= 2 ? `${hours} ч.` : `${minutes} мин.`;
    return seconds < 0 ? `просрочено на ${unit}` : `осталось ${unit}`;
  }

  function stageBadge(row) {
    const [label, cls] = stageLabels[row.workflow_stage] || stageLabels.waiting;
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  function statusBadge(row) {
    const status = row.giveaway_status || "pending";
    const [label, cls] = giveawayStatuses[status] || [status, ""];
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  function candidateBadge(row) {
    if (!row.candidate_status) return "";
    const cls = row.candidate_status === "manual_required" ? "bad" : row.candidate_status === "recommended" ? "good" : "info";
    return `<span class="badge ${cls}">score ${Number(row.candidate_score || 0)} · ${esc(row.candidate_status)}</span>`;
  }

  function hintBadge(row) {
    const [label, cls] = workflowHints[row.workflow_hint] || workflowHints.watch;
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  function boardActions(row) {
    if (document.body.dataset.role !== "admin") return "";
    const id = Number(row.id || 0);
    const chatId = Number(row.chat_id || 0);
    return `
      <div class="board-actions">
        <button class="btn" data-board-action="analyze" data-id="${id}"><i data-lucide="scan-search"></i>Анализ</button>
        ${chatId ? `<button class="btn" data-board-action="refresh-profile" data-id="${id}" data-chat-id="${chatId}"><i data-lucide="refresh-cw"></i>Дедлайн</button>` : ""}
        <button class="btn good" data-board-action="status" data-id="${id}" data-status="claimed"><i data-lucide="badge-check"></i>Забрал</button>
        <button class="btn" data-board-action="status" data-id="${id}" data-status="missed_reply"><i data-lucide="message-square-x"></i>Не отписал</button>
        <button class="btn" data-board-action="status" data-id="${id}" data-status="missed_unsubscribe"><i data-lucide="user-x"></i>Не отписался</button>
        <button class="btn" data-board-action="status" data-id="${id}" data-status="missed"><i data-lucide="clock-alert"></i>Не успел</button>
        <button class="btn bad" data-board-action="status" data-id="${id}" data-status="scam"><i data-lucide="shield-alert"></i>Скам</button>
      </div>
    `;
  }

  function renderItem(row) {
    const text = (row.text || "").replace(/\s+/g, " ").trim();
    const clipped = text.length > 220 ? text.slice(0, 220) + "..." : text;
    const deadlineTitle = Number(row.is_claim) ? "Получить до" : "Итоги до";
    const deadline = row.deadline_at ? fmtDate(row.deadline_at) : (Number(row.is_claim) ? "срок получения не найден" : "дедлайн не найден");
    const action = actionStatuses[row.action_status] || row.action_status || "new";
    const source = row.deadline_at ? sourceLabel(row.deadline_source) : (Number(row.is_claim) ? "срок не задан" : sourceLabel(row.deadline_source));
    const external = Array.isArray(row.external_requirements) && row.external_requirements.length
      ? `<span class="badge bad">manual</span>`
      : "";
    const deadlineClass = row.deadline_badge_class || (row.deadline_at ? "warn" : "bad");
    return `
      <div class="board-item stage-${esc(row.workflow_stage || "waiting")}" data-ping='${esc(JSON.stringify(row))}'>
        <div class="board-item-title">
          <strong>${esc(row.chat || "Неизвестный источник")}</strong>
          ${statusBadge(row)}
        </div>
        <div class="board-deadline">
          <span class="deadline-label">${esc(deadlineTitle)}</span>
          <strong>${esc(deadline)}</strong>
          <span class="deadline-left ${deadlineClass}">${esc(timeLeftLabel(row))}</span>
        </div>
        <div class="deadline-row">
          ${stageBadge(row)}
          <span class="badge ${deadlineClass}"><i data-lucide="calendar-clock"></i>${esc(source)}</span>
          <span class="badge info">${esc(action)}</span>
          ${candidateBadge(row)}
          ${external}
        </div>
        <div class="board-item-text">${esc(clipped || "Нет текста")}</div>
        ${boardActions(row)}
      </div>
    `;
  }

  function renderBucket(id, rows) {
    const el = $(id);
    if (!el) return;
    el.innerHTML = rows && rows.length
      ? rows.map(renderItem).join("")
      : emptyState("inbox", "Пусто", "В этой очереди сейчас нет розыгрышей.");
  }

  function renderStats(data) {
    const stats = data.stats || {};
    const counts = data.bucket_counts || {};
    $("giveaway-board-stats").innerHTML = [
      ["Всего", stats.total, "gift", "#29d3c2"],
      ["Забрать/проверить", counts.need_action || stats.claim_prize, "mouse-pointer-click", "#f6c453"],
      ["Ждет результата", counts.waiting_result, "hourglass", "#29d3c2"],
      ["Без дедлайна", counts.no_deadline || stats.no_deadline, "calendar-x", "#fb7185"],
      ["Не отписал", stats.missed_reply, "message-square-x", "#a1a1aa"],
      ["Закрыто", stats.done, "badge-check", "#4ade80"]
    ].map(([label, value, icon, color]) => `
      <div class="panel metric-card" style="--metric-color:${color}">
        <div class="metric-top"><div class="metric-label">${label}</div><span class="metric-icon"><i data-lucide="${icon}"></i></span></div>
        <div class="metric-value">${value ?? 0}</div>
      </div>
    `).join("");
  }

  function renderStrip(data) {
    const outbox = data.outbox || {};
    const candidateText = (data.candidate_statuses || []).map(row => `${row.status}: ${row.count}`).join(" · ") || "кандидатов нет";
    const actionText = (data.action_statuses || []).slice(0, 3).map(row => `${row.action}/${row.status}: ${row.count}`).join(" · ") || "действий нет";
    const stats = data.stats || {};
    $("giveaway-board-strip").innerHTML = `
      <span class="badge warn">просрочено ${Number(stats.overdue || 0).toLocaleString()}</span>
      <span class="badge info">на получение ${Number(stats.claim_prize || 0).toLocaleString()}</span>
      <span class="badge ${Number(outbox.pending || 0) > 5000 ? "warn" : "good"}">live ${Number(outbox.pending || 0).toLocaleString()}</span>
      <span class="badge">${esc(candidateText)}</span>
      <span class="badge">${esc(actionText)}</span>
    `;
  }

  function renderGiveawayBoard(data) {
    const buckets = data.buckets || {};
    renderStats(data);
    renderStrip(data);
    renderBucket("giveaway-need-action", buckets.need_action || []);
    renderBucket("giveaway-waiting-result", buckets.waiting_result || []);
    renderBucket("giveaway-no-deadline", buckets.no_deadline || []);
    renderBucket("giveaway-suspicious", buckets.suspicious || []);
    renderBucket("giveaway-done", buckets.done || []);
  }

  window.PulseGiveaways = { renderGiveawayBoard };
})();
