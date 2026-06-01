/* Pulse Desk — command palette (Ctrl/⌘+K) + keyboard shortcuts.
   Self-contained: drives the existing UI by clicking the buttons app.js
   already wires up, so it never reaches into app.js internals. */
(function () {
  "use strict";

  var cmdk = document.getElementById("cmdk");
  var input = document.getElementById("cmdk-input");
  var list = document.getElementById("cmdk-list");
  var shortcutsModal = document.getElementById("shortcuts-modal");
  if (!cmdk || !input || !list) return;

  var commands = [];
  var filtered = [];
  var activeIndex = 0;

  function visible(el) {
    return el && !el.classList.contains("admin-hidden") &&
      !el.classList.contains("hidden") && el.offsetParent !== null;
  }

  function clickEl(id) {
    var el = document.getElementById(id);
    if (el) el.click();
  }

  // Build the command list fresh each open so admin/viewer visibility is honored.
  function buildCommands() {
    var cmds = [];

    document.querySelectorAll(".sidebar .nav button[data-tab]").forEach(function (btn) {
      if (!visible(btn)) return;
      var label = (btn.textContent || "").trim();
      var icon = btn.querySelector("i") ? btn.querySelector("i").getAttribute("data-lucide") : "square";
      cmds.push({
        group: "Разделы",
        label: label,
        icon: icon || "square",
        hint: "Перейти",
        run: function () { btn.click(); }
      });
    });

    var actions = [
      { id: "scan-btn", label: "Скан истории", icon: "database" },
      { id: "report-btn", label: "Сформировать отчёт", icon: "file-text" },
      { id: "refresh-btn", label: "Обновить данные", icon: "refresh-cw" },
      { id: "theme-toggle", label: "Сменить тему", icon: "sun-moon" },
      { id: "browser-notify-btn", label: "Браузерные уведомления", icon: "bell" },
      { id: "export-json-btn", label: "Экспорт аналитики (JSON)", icon: "download" },
      { id: "logout-btn", label: "Выйти", icon: "log-out" }
    ];
    actions.forEach(function (a) {
      var el = document.getElementById(a.id);
      if (!el || el.classList.contains("admin-hidden")) return;
      cmds.push({
        group: "Действия",
        label: a.label,
        icon: a.icon,
        hint: "Выполнить",
        run: function () { el.click(); }
      });
    });

    cmds.push({
      group: "Действия",
      label: "Горячие клавиши",
      icon: "keyboard",
      hint: "?",
      run: openShortcuts
    });

    return cmds;
  }

  function render() {
    list.innerHTML = "";
    if (!filtered.length) {
      var empty = document.createElement("div");
      empty.className = "cmdk-empty";
      empty.textContent = "Ничего не найдено";
      list.appendChild(empty);
      return;
    }
    var lastGroup = null;
    filtered.forEach(function (cmd, i) {
      if (cmd.group !== lastGroup) {
        var gl = document.createElement("div");
        gl.className = "cmdk-group-label";
        gl.textContent = cmd.group;
        list.appendChild(gl);
        lastGroup = cmd.group;
      }
      var item = document.createElement("div");
      item.className = "cmdk-item" + (i === activeIndex ? " active" : "");
      item.setAttribute("role", "option");
      item.dataset.index = String(i);
      item.innerHTML =
        '<span class="metric-icon"><i data-lucide="' + cmd.icon + '"></i></span>' +
        '<span class="cmdk-label"></span>' +
        '<span class="cmdk-hint"></span>';
      item.querySelector(".cmdk-label").textContent = cmd.label;
      item.querySelector(".cmdk-hint").textContent = cmd.hint || "";
      item.addEventListener("mousemove", function () {
        if (activeIndex !== i) { activeIndex = i; markActive(); }
      });
      item.addEventListener("click", function () { execute(i); });
      list.appendChild(item);
    });
    if (window.lucide && typeof lucide.createIcons === "function") lucide.createIcons();
  }

  function markActive() {
    list.querySelectorAll(".cmdk-item").forEach(function (el) {
      var on = Number(el.dataset.index) === activeIndex;
      el.classList.toggle("active", on);
      if (on) el.scrollIntoView({ block: "nearest" });
    });
  }

  function filter(q) {
    q = (q || "").trim().toLowerCase();
    if (!q) { filtered = commands.slice(); }
    else {
      filtered = commands.filter(function (c) {
        return c.label.toLowerCase().indexOf(q) !== -1 ||
          c.group.toLowerCase().indexOf(q) !== -1;
      });
    }
    activeIndex = 0;
    render();
  }

  function open() {
    commands = buildCommands();
    input.value = "";
    filter("");
    cmdk.classList.add("active");
    cmdk.setAttribute("aria-hidden", "false");
    setTimeout(function () { input.focus(); }, 20);
  }

  function close() {
    cmdk.classList.remove("active");
    cmdk.setAttribute("aria-hidden", "true");
  }

  function isOpen() { return cmdk.classList.contains("active"); }

  function execute(i) {
    var cmd = filtered[i];
    close();
    if (cmd && typeof cmd.run === "function") setTimeout(cmd.run, 0);
  }

  function openShortcuts() {
    if (shortcutsModal) {
      shortcutsModal.classList.add("active");
      document.body.classList.add("modal-open");
    }
  }
  function closeShortcuts() {
    if (shortcutsModal) {
      shortcutsModal.classList.remove("active");
      if (!document.querySelector(".modal.active")) document.body.classList.remove("modal-open");
    }
  }

  // --- palette wiring ---
  var openBtn = document.getElementById("cmdk-open");
  if (openBtn) openBtn.addEventListener("click", open);

  input.addEventListener("input", function () { filter(input.value); });

  cmdk.addEventListener("mousedown", function (e) {
    if (e.target === cmdk) close();
  });

  input.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, filtered.length - 1);
      markActive();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      markActive();
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered.length) execute(activeIndex);
    }
  });

  var shortcutsClose = document.getElementById("shortcuts-close");
  if (shortcutsClose) shortcutsClose.addEventListener("click", closeShortcuts);
  if (shortcutsModal) {
    shortcutsModal.addEventListener("mousedown", function (e) {
      if (e.target === shortcutsModal) closeShortcuts();
    });
  }

  // --- global shortcuts ---
  var pendingG = false;
  var pendingGTimer = null;

  function inTypingContext(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
  }

  function navTo(tab) {
    var btn = document.querySelector('.sidebar .nav button[data-tab="' + tab + '"]') ||
      document.querySelector('[data-tab="' + tab + '"]');
    if (btn && visible(btn)) btn.click();
    else if (btn) btn.click();
  }

  document.addEventListener("keydown", function (e) {
    // Ctrl/Cmd+K toggles palette anywhere.
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      isOpen() ? close() : open();
      return;
    }

    if (e.key === "Escape") {
      if (isOpen()) { close(); return; }
      if (shortcutsModal && shortcutsModal.classList.contains("active")) { closeShortcuts(); return; }
      return; // let app.js handle its own modal Esc
    }

    // Don't fire single-key shortcuts while typing or with modifiers.
    if (isOpen() || inTypingContext(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;

    // "g" chord prefix
    if (pendingG) {
      pendingG = false;
      clearTimeout(pendingGTimer);
      var map = { d: "dashboard", b: "debts", m: "market", a: "analytics", s: "settings" };
      var dest = map[e.key.toLowerCase()];
      if (dest) { e.preventDefault(); navTo(dest); }
      return;
    }

    if (e.key === "g" || e.key === "G") {
      pendingG = true;
      pendingGTimer = setTimeout(function () { pendingG = false; }, 800);
      return;
    }

    if (e.key === "/") {
      e.preventDefault();
      navTo("dashboard");
      var search = document.getElementById("search-input");
      if (search) setTimeout(function () { search.focus(); }, 30);
    } else if (e.key === "r" || e.key === "R") {
      e.preventDefault();
      clickEl("refresh-btn");
    } else if (e.key === "t" || e.key === "T") {
      e.preventDefault();
      clickEl("theme-toggle");
    } else if (e.key === "?") {
      e.preventDefault();
      openShortcuts();
    }
  });
})();

/* Pulse Desk — drag-and-drop reordering of dashboard bento panels.
   Order is keyed by element id and persisted in localStorage. The drag
   handle is the panel header, so buttons/text inside stay usable. */
(function () {
  "use strict";

  var STORE = "pulse_ops_order";
  var grid = document.querySelector(".dashboard-ops-grid");
  if (!grid) return;

  function panels() {
    return Array.prototype.slice.call(grid.children).filter(function (el) {
      return el.classList.contains("panel") && el.id;
    });
  }

  function applyOrder() {
    var saved;
    try { saved = JSON.parse(localStorage.getItem(STORE) || "[]"); } catch (e) { saved = []; }
    if (!Array.isArray(saved) || !saved.length) return;
    saved.forEach(function (id) {
      var el = document.getElementById(id);
      if (el && el.parentNode === grid) grid.appendChild(el);
    });
  }

  function saveOrder() {
    localStorage.setItem(STORE, JSON.stringify(panels().map(function (p) { return p.id; })));
  }

  var dragEl = null;

  panels().forEach(function (panel) {
    var head = panel.querySelector(".ops-panel-head") || panel;
    head.style.cursor = "grab";

    head.addEventListener("mousedown", function () { panel.setAttribute("draggable", "true"); });
    panel.addEventListener("mouseup", function () { panel.removeAttribute("draggable"); });

    panel.addEventListener("dragstart", function (e) {
      dragEl = panel;
      panel.classList.add("dragging");
      grid.classList.add("is-dragging");
      try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", panel.id); } catch (err) {}
    });

    panel.addEventListener("dragend", function () {
      panel.classList.remove("dragging");
      panel.removeAttribute("draggable");
      grid.classList.remove("is-dragging");
      panels().forEach(function (p) { p.classList.remove("drag-over"); });
      dragEl = null;
      saveOrder();
    });

    panel.addEventListener("dragover", function (e) {
      if (!dragEl || dragEl === panel) return;
      e.preventDefault();
      panel.classList.add("drag-over");
    });

    panel.addEventListener("dragleave", function () { panel.classList.remove("drag-over"); });

    panel.addEventListener("drop", function (e) {
      e.preventDefault();
      panel.classList.remove("drag-over");
      if (!dragEl || dragEl === panel) return;
      var list = panels();
      var from = list.indexOf(dragEl), to = list.indexOf(panel);
      if (from < to) grid.insertBefore(dragEl, panel.nextSibling);
      else grid.insertBefore(dragEl, panel);
      saveOrder();
    });
  });

  applyOrder();
})();
