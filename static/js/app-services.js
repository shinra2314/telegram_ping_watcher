/* Pulse Desk frontend — app-services.js: Launcher tab (external service control).
   Classic script sharing the global scope with the other app-*.js files; relies on
   api(), state, $, esc, showToast defined in app-core.js. Keep load order: after
   app-settings.js, before app-main.js (app-main's refreshData calls loadServices). */

    let svcSelected = null;

    function svcFmtUptime(sec) {
      sec = Number(sec) || 0;
      if (sec <= 0) return "—";
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
      if (h) return `${h}ч ${m}м`;
      if (m) return `${m}м ${s}с`;
      return `${s}с`;
    }

    function svcBadge(status, running) {
      if (running) return ["good", "работает"];
      if (status === "failed") return ["bad", "ошибка (стоп)"];
      if (status === "crashed") return ["bad", "упал"];
      if (status === "starting") return ["warn", "запуск"];
      if (status === "stopping") return ["warn", "остановка"];
      return ["info", "остановлен"];
    }

    function renderServices(data) {
      const list = $("services-list");
      const services = (data && data.services) || [];
      const statsHost = $("services-stats");
      if (statsHost) {
        statsHost.innerHTML = `
          <div class="stat"><span class="stat-label">Сервисов</span><strong>${data.total || 0}</strong></div>
          <div class="stat"><span class="stat-label">Работают</span><strong>${data.running || 0}</strong></div>
          <div class="stat"><span class="stat-label">Хост</span><strong>Pulse Desk</strong></div>`;
      }
      if (!services.length) {
        list.innerHTML = `<div class="panel"><div class="muted">Нет управляемых сервисов. Добавьте их в <code>config/services.json</code> (шаблон — <code>config/services.example.json</code>) и обновите.</div></div>`;
        return;
      }
      list.innerHTML = services.map(svc => {
        const [cls, label] = svcBadge(svc.status, svc.running);
        const health = svc.has_health_probe
          ? (svc.healthy === true ? "OK" : svc.healthy === false ? "нет" : "—")
          : "n/a";
        const metrics = svc.metrics
          ? `<div><span>RAM</span><strong>${svc.metrics.rss_mb ?? "—"} МБ</strong></div>`
          : "";
        return `
        <div class="panel service-card" data-svc="${esc(svc.name)}">
          <div class="ops-panel-head">
            <div>
              <div class="kicker">${esc(svc.cmd || "")}</div>
              <h2>${esc(svc.label)}</h2>
            </div>
            <span class="badge ${cls}">${label}</span>
          </div>
          <div class="ops-micro-grid">
            <div><span>PID</span><strong>${svc.pid ?? "—"}</strong></div>
            <div><span>Аптайм</span><strong>${svcFmtUptime(svc.uptime_seconds)}</strong></div>
            <div><span>Рестарты</span><strong>${svc.restarts ?? 0}</strong></div>
            <div><span>Health</span><strong>${health}</strong></div>
            ${metrics}
          </div>
          ${svc.last_error ? `<div class="muted" style="color:var(--bad,#c0392b);margin-top:6px">⚠ ${esc(svc.last_error)}</div>` : ""}
          <div class="actions" style="margin-top:10px;flex-wrap:wrap">
            <button class="btn good" data-act="start" ${svc.running ? "disabled" : ""}><i data-lucide="play"></i>Старт</button>
            <button class="btn" data-act="restart"><i data-lucide="rotate-cw"></i>Рестарт</button>
            <button class="btn bad" data-act="stop" ${svc.running ? "" : "disabled"}><i data-lucide="square"></i>Стоп</button>
            <button class="btn" data-act="logs"><i data-lucide="terminal"></i>Логи</button>
            ${svc.panel_url ? `<button class="btn" data-act="panel"><i data-lucide="layout"></i>Панель</button>` : ""}
          </div>
        </div>`;
      }).join("");
      if (window.lucide) lucide.createIcons();
    }

    async function loadServices() {
      const list = $("services-list");
      try {
        const data = await api("/api/services", { silent: true });
        renderServices(data);
      } catch (err) {
        list.innerHTML = `<div class="panel"><div class="muted">Не удалось загрузить сервисы: ${esc(err.message || "ошибка")}</div></div>`;
      }
    }

    async function serviceAction(name, action, btn) {
      try {
        await api(`/api/services/${encodeURIComponent(name)}/${action}`, { method: "POST", button: btn });
        const verb = { start: "запущен", stop: "остановлен", restart: "перезапущен" }[action] || action;
        showToast(`Сервис ${name}: ${verb}`, "info");
      } catch (err) {
        // api() already toasts the error body
      } finally {
        await loadServices();
        if (svcSelected === name) await loadServiceLogs(name);
      }
    }

    async function loadServiceLogs(name) {
      svcSelected = name;
      $("services-detail").style.display = "block";
      $("services-log-title").textContent = `Логи: ${name}`;
      const box = $("services-log-box");
      box.textContent = "Загрузка...";
      try {
        const data = await api(`/api/services/${encodeURIComponent(name)}/logs?limit=300`, { silent: true });
        const lines = data.logs || [];
        box.textContent = lines.length ? lines.join("\n") : "Логов пока нет.";
        box.scrollTop = box.scrollHeight;
      } catch (err) {
        box.textContent = `Не удалось получить логи: ${err.message || "ошибка"}`;
      }
    }

    function openServicePanel(name) {
      api("/api/services", { silent: true }).then(data => {
        const svc = (data.services || []).find(s => s.name === name);
        if (!svc || !svc.panel_url) { showToast("У сервиса нет встроенной панели", "error"); return; }
        $("services-detail").style.display = "block";
        const wrap = $("services-panel-wrap");
        wrap.style.display = "block";
        $("services-panel-title").textContent = `Панель: ${svc.label}`;
        $("services-panel-open").href = svc.panel_url;
        $("services-panel-iframe").src = svc.panel_url;
        wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }

    // --- one-time event wiring (elements exist at parse time) ---
    $("services-list").addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-act]");
      if (!btn) return;
      const card = btn.closest(".service-card");
      if (!card) return;
      const name = card.dataset.svc;
      const act = btn.dataset.act;
      if (act === "logs") return void loadServiceLogs(name);
      if (act === "panel") return void openServicePanel(name);
      serviceAction(name, act, btn);
    });
    $("services-refresh").addEventListener("click", loadServices);
    $("services-log-refresh").addEventListener("click", () => { if (svcSelected) loadServiceLogs(svcSelected); });
    $("services-start-all").addEventListener("click", async (e) => {
      await api("/api/services/start-all", { method: "POST", button: e.currentTarget });
      showToast("Команда «старт всех» отправлена", "info");
      await loadServices();
    });
    $("services-stop-all").addEventListener("click", async (e) => {
      await api("/api/services/stop-all", { method: "POST", button: e.currentTarget });
      showToast("Команда «стоп всех» отправлена", "info");
      await loadServices();
    });
