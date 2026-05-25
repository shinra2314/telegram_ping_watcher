window.PulseCore = (() => {
  const state = {
    tab: "dashboard",
    offset: 0,
    limit: 0,
    grouped: false,
    charts: {},
    token: localStorage.getItem("pulse_token") || "",
    role: localStorage.getItem("pulse_role") || ""
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const fmtDate = (value) => value ? new Date(value).toLocaleString() : "нет даты";
  const loadingCards = (count = 6) => Array.from({ length: count }, () => `<div class="card skeleton-card"></div>`).join("");
  const emptyState = (icon, title, text) => `<div class="panel empty-state"><span class="metric-icon"><i data-lucide="${icon}"></i></span><strong>${esc(title)}</strong><span>${esc(text)}</span></div>`;
  const splitLines = (value) => String(value || "").split(/\r?\n|,/).map(x => x.trim()).filter(Boolean);

  function downloadJson(filename, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  return { state, $, esc, fmtDate, loadingCards, emptyState, splitLines, downloadJson };
})();
