(() => {
  const { $, esc, fmtDate } = window.PulseCore;

  function badge(ok, goodText, badText) {
    return `<span class="badge ${ok ? "good" : "warn"}">${esc(ok ? goodText : badText)}</span>`;
  }

  function renderList(rows) {
    return `<div class="diagnostic-list">${rows.map(row => `<div>${row}</div>`).join("")}</div>`;
  }

  function renderDiagnostics(data) {
    const db = data.db || {};
    const live = data.live || {};
    const outbox = live.outbox || {};
    const scan = data.scan || {};
    const scanHealth = scan.health || {};
    const runtime = data.runtime || {};
    const accounts = data.accounts || {};
    const security = data.security || {};
    const giveaways = data.giveaways || {};
    const recommendations = data.recommendations || [];
    const problemEvents = data.recent_problem_events || [];

    $("diagnostics-panel").innerHTML = `
      <div class="diagnostics-strip">
        ${badge(data.status === "ok", "API ok", "API issue")}
        ${badge(!security.admin_token_looks_weak, "ADMIN_TOKEN strong", "ADMIN_TOKEN weak")}
        ${badge(!security.viewer_token_looks_weak, "VIEWER_TOKEN strong", "VIEWER_TOKEN weak")}
        ${badge(outbox.pressure !== "high", "live queue ok", "live queue large")}
        ${badge(!(runtime.missing_background_tasks || []).length, "jobs ok", "jobs missing")}
        ${badge(!(scanHealth.running || []).length, "scan idle", "scan running")}
        ${badge(accounts.online > 0, `${accounts.online || 0} online`, "no accounts online")}
      </div>
      <div class="diagnostic-grid">
        <div class="panel diagnostic-card">
          <h2>База</h2>
          ${renderList([
            `schema: ${esc(String(data.schema_version || "unknown"))}`,
            `size: ${Number(db.size_mb || 0).toFixed(2)} MB`,
            `backups: ${Number(db.backup_count || 0)}`,
            `pings: ${Number(db.stats?.total || 0).toLocaleString()}`,
            `chats: ${Number(db.stats?.unique_chats || 0).toLocaleString()}`,
            `favorites: ${Number(db.stats?.favorites || 0).toLocaleString()}`
          ])}
        </div>
        <div class="panel diagnostic-card">
          <h2>Live</h2>
          ${renderList([
            `outbox: ${Number(outbox.total || 0).toLocaleString()}`,
            `pending: ${Number(outbox.pending || 0).toLocaleString()}`,
            `recent 1h: ${Number(outbox.recent_1h || 0).toLocaleString()}`,
            `pressure: ${esc(outbox.pressure || "ok")}`,
            `oldest: ${fmtDate(outbox.oldest_created_at)}`,
            `latest: ${fmtDate(outbox.latest_created_at)}`,
            ...(outbox.by_type || []).slice(0, 4).map(row => `${esc(row.event_type)}: ${Number(row.count || 0).toLocaleString()}`)
          ])}
        </div>
        <div class="panel diagnostic-card">
          <h2>Скан</h2>
          ${renderList([
            `current: ${scan.current?.running ? "running" : "idle"}`,
            `latest: ${esc(scan.latest?.status || "none")}`,
            `found: ${Number(scan.latest?.found || 0).toLocaleString()}`,
            `running rows: ${(scanHealth.running || []).length}`,
            `interrupted: ${(scanHealth.recent_interrupted || []).length}`,
            `tasks: ${(scan.background_tasks || []).map(esc).join(", ") || "none"}`
          ])}
        </div>
        <div class="panel diagnostic-card">
          <h2>Runtime</h2>
          ${renderList([
            `uptime: ${Number(runtime.uptime_seconds || 0).toLocaleString()} sec`,
            `background: ${(runtime.background_tasks || []).map(esc).join(", ") || "none"}`,
            `missing: ${(runtime.missing_background_tasks || []).map(esc).join(", ") || "none"}`,
            `accounts ok: ${runtime.accounts_ok ? "yes" : "no"}`
          ])}
        </div>
        <div class="panel diagnostic-card">
          <h2>Розыгрыши</h2>
          ${renderList([
            `mode: ${esc(giveaways.review_mode || "manual")}`,
            `strict: ${esc(giveaways.strict_rule || "")}`,
            `account: ${esc(giveaways.action_account || "")}`,
            `dry run: ${giveaways.dry_run ? "yes" : "no"}`,
            `delay: ${Number(giveaways.min_action_delay_seconds || 0)} sec`
          ])}
        </div>
        <div class="panel diagnostic-card">
          <h2>Подсказки</h2>
          ${renderList(recommendations.map(esc))}
        </div>
        <div class="panel diagnostic-card">
          <h2>Последние проблемы</h2>
          ${renderList(problemEvents.length ? problemEvents.map(row => `${esc(row.level)} · ${esc(row.source)} · ${esc(row.message)} · ${fmtDate(row.created_at)}`) : ["ошибок и предупреждений нет"])}
        </div>
      </div>
    `;
  }

  window.PulseDiagnostics = { renderDiagnostics };
})();
