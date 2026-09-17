// Panel core: navigation, API, formatting, sheets, toasts, storage.
//
// Classic scripts in one global scope, no build step. Each screen lives in
// screens/<name>.js and calls App.register(); boot.js starts the app last.
// Screens render into #screen and declare their buttons as `data-act`
// attributes; one delegated listener routes every tap to the current screen.
'use strict';

const tg = (window.Telegram && window.Telegram.WebApp) || null;

const App = {
  screens: {},
  stack: [],          // [{name, params}] — the tab root first
  sections: {},       // what the caller's key opens, from /api/app/home
  role: '',
  seq: 0,             // render generation: a slow response never paints over a newer screen
  el: document.getElementById('screen'),
  bar: document.getElementById('bar'),
  sub: document.getElementById('sub'),

  register(name, def) { this.screens[name] = def; },

  current() { return this.stack[this.stack.length - 1] || { name: 'home', params: {} }; },

  // Tabs reset the stack; `go` pushes onto it, and Telegram's back button pops.
  tab(name, params) {
    this.stack = [{ name: name, params: params || {} }];
    return this.render();
  },
  go(name, params) {
    this.stack.push({ name: name, params: params || {} });
    return this.render();
  },
  back() {
    if (this.stack.length > 1) { this.stack.pop(); return this.render(); }
    return this.tab('home');
  },
  replace(params) {
    const cur = this.current();
    cur.params = Object.assign({}, cur.params, params);
    return this.render({ quiet: true });
  },

  async render(opts) {
    const quiet = opts && opts.quiet;
    const cur = this.current();
    const def = this.screens[cur.name];
    if (!def) return this.tab('home');
    const mine = ++this.seq;
    if (this.leave) { try { this.leave(); } catch (e) { /* ignore */ } this.leave = null; }
    this.syncChrome(def);
    if (!quiet) {
      // Skeleton only when the answer is slow, so fast screens do not flicker.
      this.skeletonTimer = setTimeout(() => { if (mine === this.seq) this.el.innerHTML = skeleton(); }, 140);
    }
    try {
      const view = await def.render(cur.params || {});
      if (mine !== this.seq) return;
      clearTimeout(this.skeletonTimer);
      if (typeof view === 'string') {
        // Only a real navigation animates; an in-place refresh must not blink.
        this.el.innerHTML = '<div class="screen' + (quiet ? ' still' : '') + '">' + view + '</div>';
      }
      if (def.mounted) this.leave = def.mounted(cur.params || {}) || null;
      if (!quiet) window.scrollTo(0, 0);
    } catch (err) {
      clearTimeout(this.skeletonTimer);
      if (mine !== this.seq) return;
      this.el.innerHTML = '<div class="screen">' + errorView(err) + '</div>';
    }
  },

  syncChrome(def) {
    const cur = this.current();
    const tabName = def.tab || cur.name;
    this.bar.innerHTML = this.tabs().map((t) =>
      '<button class="tab' + (t[0] === tabName ? ' on' : '') + '" data-tab="' + t[0] + '">'
      + icon(t[1], 22) + '<span>' + t[2] + '</span></button>').join('');
    this.sub.textContent = def.title || '';
    if (tg && tg.BackButton) {
      if (this.stack.length > 1) tg.BackButton.show(); else tg.BackButton.hide();
    }
  },

  tabs() {
    const s = this.sections;
    const tabs = [['home', 'home', 'Главная']];
    if (s.giveaways) tabs.push(['giveaways', 'gift', 'Розыгрыши']);
    if (s.feed) tabs.push(['feed', 'list', 'Лента']);
    if (s.market) tabs.push(['converter', 'exchange', 'Конвертер']);
    // At most five tabs: everything that is not a daily screen goes under «Ещё».
    if (s.debts || s.accounts || s.salary || s.prefs || s.analytics) tabs.push(['more', 'grid', 'Ещё']);
    return tabs;
  },
};

// ── API ───────────────────────────────────────────────────────────────
// Every call carries initData; the server verifies it each time.
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, body) {
  const init = { headers: { 'X-Telegram-Init-Data': (tg && tg.initData) || initDataFromHash() } };
  if (body !== undefined) {
    init.method = 'POST';
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new ApiError(0, 'Нет связи с Pulse Desk. Компьютер включён?');
  }
  if (!res.ok) {
    let detail = 'Ошибка ' + res.status;
    try {
      const data = await res.json();
      detail = typeof data.detail === 'string' ? data.detail : detail;
    } catch (e) { /* keep default */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

// Outside Telegram (a browser tab during development) the WebApp script still
// loads but has no initData; the signed string then rides in the URL hash.
function initDataFromHash() {
  const m = /tgWebAppData=([^&]*)/.exec(location.hash);
  return m ? decodeURIComponent(m[1]) : '';
}

// ── Formatting ────────────────────────────────────────────────────────
function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function group(text) {
  const parts = text.split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return parts.join('.');
}

// Mirrors converter._digits: cents for fiat, more for small coin amounts.
function digitsFor(value, isFiat) {
  const m = Math.abs(value);
  if (isFiat) return m >= 1 ? 2 : (m >= 0.01 ? 4 : 6);
  if (m >= 1000) return 2;
  if (m >= 1) return 4;
  return m >= 0.001 ? 6 : 8;
}

function fmtAmount(value, isFiat) {
  if (value == null || !isFinite(value)) return '—';
  let text = value.toFixed(digitsFor(value, isFiat));
  // Fiat keeps its cents; a coin drops trailing zeros (`1 BTC`, not `1.0000`).
  if (!isFiat && text.indexOf('.') !== -1) text = text.replace(/0+$/, '').replace(/\.$/, '');
  return group(text);
}

function fmtInt(value) {
  return group(String(Math.round(Number(value) || 0)));
}

function fmtUsd(value) {
  if (value == null || !isFinite(value)) return '—';
  const v = Number(value);
  return '$' + group(v >= 100 ? String(Math.round(v)) : v.toFixed(2));
}

function parseTime(iso) {
  if (!iso) return null;
  const d = new Date(String(iso).replace(' ', 'T'));
  return isNaN(d.getTime()) ? null : d;
}

function fmtTime(iso) {
  const d = parseTime(iso);
  if (!d) return '';
  const now = new Date();
  const mins = Math.round((now - d) / 60000);
  if (mins >= 0 && mins < 1) return 'только что';
  if (mins >= 0 && mins < 60) return mins + ' мин';
  const sameDay = d.toDateString() === now.toDateString();
  const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return hm;
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' + hm;
}

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

// ── Feedback ──────────────────────────────────────────────────────────
function haptic(kind) {
  if (!tg || !tg.HapticFeedback) return;
  try {
    if (kind === 'ok' || kind === 'error' || kind === 'warning') {
      tg.HapticFeedback.notificationOccurred(kind === 'ok' ? 'success' : kind);
    } else if (kind === 'select') {
      tg.HapticFeedback.selectionChanged();
    } else {
      tg.HapticFeedback.impactOccurred('light');
    }
  } catch (e) { /* older clients */ }
}

let toastTimer = null;
// `action` ({ label, run }) adds a button — «Вернуть» after a removal — and
// keeps the toast up longer so there is time to reach it.
function toast(message, bad, action) {
  const old = document.querySelector('.toast');
  if (old) old.remove();
  const el = document.createElement('div');
  el.className = 'toast' + (bad ? ' bad' : '') + (action ? ' with-act' : '');
  el.textContent = message;
  if (action) {
    const btn = document.createElement('button');
    btn.className = 'toast-act';
    btn.textContent = action.label;
    btn.addEventListener('click', () => {
      el.remove();
      Promise.resolve(action.run()).catch((err) => toast(err.message || 'Ошибка', true));
    });
    el.appendChild(btn);
  }
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), action ? 5000 : 2600);
  haptic(bad ? 'error' : 'ok');
}

function confirmBox(message) {
  return new Promise((resolve) => {
    if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
      tg.showConfirm(message, (ok) => resolve(Boolean(ok)));
    } else {
      resolve(window.confirm(message));
    }
  });
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Скопировано');
  } catch (e) {
    toast('Не удалось скопировать', true);
  }
}

function openLink(url) {
  if (!url) return;
  if (tg && /^https?:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
  else if (tg && tg.openLink) tg.openLink(url);
  else window.open(url, '_blank');
}

// ── Sheets ────────────────────────────────────────────────────────────
const Sheet = {
  open(title, html, onMount) {
    this.close();
    const scrim = document.createElement('div');
    scrim.className = 'scrim';
    scrim.dataset.act = 'sheet-close';
    const sheet = document.createElement('div');
    sheet.className = 'sheet';
    sheet.innerHTML = '<div class="grab"></div>'
      + (title ? '<div class="sheet-title">' + esc(title) + '</div>' : '')
      + '<div class="scroll">' + html + '</div>';
    document.body.appendChild(scrim);
    document.body.appendChild(sheet);
    if (onMount) onMount(sheet);
    haptic();
    return sheet;
  },
  close() {
    document.querySelectorAll('.scrim,.sheet').forEach((n) => n.remove());
  },
  isOpen() { return Boolean(document.querySelector('.sheet')); },
};

// ── Storage ───────────────────────────────────────────────────────────
// Telegram CloudStorage follows the user across devices; localStorage is the
// fallback for old clients and for a plain browser tab.
const Store = {
  cloud() { return tg && tg.CloudStorage && tg.isVersionAtLeast && tg.isVersionAtLeast('6.9'); },
  get(key) {
    return new Promise((resolve) => {
      if (this.cloud()) {
        tg.CloudStorage.getItem(key, (err, value) => resolve(err ? this.local(key) : (value || this.local(key))));
      } else {
        resolve(this.local(key));
      }
    });
  },
  local(key) { try { return localStorage.getItem(key) || ''; } catch (e) { return ''; } },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
    if (this.cloud()) tg.CloudStorage.setItem(key, value, () => {});
  },
  async json(key, fallback) {
    try { return JSON.parse(await this.get(key)) || fallback; } catch (e) { return fallback; }
  },
};

// ── Shared view pieces ────────────────────────────────────────────────
function skeleton() {
  return '<div class="stack" style="margin-top:6px">'
    + '<div class="skel" style="height:78px"></div>'
    + '<div class="tiles"><div class="skel" style="height:104px"></div><div class="skel" style="height:104px"></div></div>'
    + '<div class="skel" style="height:220px"></div></div>';
}

function emptyView(iconName, title, text) {
  return '<div class="empty">' + icon(iconName, 34) + '<div class="h">' + esc(title) + '</div>'
    + (text ? '<div>' + esc(text) + '</div>' : '') + '</div>';
}

function errorView(err) {
  const reopen = err && err.status === 401;
  return '<div class="err-box"><div class="h">' + (reopen ? 'Сессия панели устарела' : 'Не получилось')
    + '</div><div class="dim">' + esc(err && err.message) + '</div></div>'
    + '<div class="stack" style="margin-top:12px">'
    + (reopen
      ? '<button class="btn" data-act="app-close">' + icon('x', 18) + 'Закрыть и открыть заново</button>'
      : '<button class="btn" data-act="app-retry">' + icon('refresh', 18) + 'Повторить</button>')
    + '</div>';
}

function item(opts) {
  const attrs = opts.act ? ' data-act="' + opts.act + '"' : '';
  const data = opts.data ? Object.keys(opts.data).map((k) => ' data-' + k + '="' + esc(opts.data[k]) + '"').join('') : '';
  return '<div class="item' + (opts.act ? '' : ' static') + (opts.cls ? ' ' + opts.cls : '') + '"' + attrs + data + '>'
    + (opts.lead !== undefined ? '<div class="lead ' + (opts.leadCls || '') + '">' + opts.lead + '</div>' : '')
    + '<div class="body"><div class="t">' + opts.title + '</div>'
    + (opts.desc ? '<div class="d' + (opts.wrap ? ' wrap' : '') + '">' + opts.desc + '</div>' : '') + '</div>'
    + (opts.end ? '<div class="end">' + opts.end + '</div>' : '')
    + (opts.act && opts.chev !== false ? '<span class="chev">' + icon('chevron', 16) + '</span>' : '')
    + '</div>';
}

function seg(options, value, act) {
  return '<div class="seg">' + options.map((o) =>
    '<button class="' + (o[0] === value ? 'on' : '') + '" data-act="' + act + '" data-v="' + esc(o[0]) + '">'
    + esc(o[1]) + '</button>').join('') + '</div>';
}

// ── Events ────────────────────────────────────────────────────────────
const GLOBAL_ACTIONS = {
  'sheet-close': () => Sheet.close(),
  'app-close': () => { if (tg) tg.close(); },
  'app-retry': () => App.render(),
  nav: (el) => App.go(el.dataset.to, Object.assign({}, el.dataset)),
  link: (el) => openLink(el.dataset.url),
  copy: (el) => copyText(el.dataset.text || ''),
};

function dispatch(kind, event) {
  const target = event.target.closest('[data-act]');
  if (!target) return;
  const act = target.dataset.act;
  const def = App.screens[App.current().name] || {};
  const table = kind === 'click' ? def.actions : (kind === 'change' ? def.changes : def.inputs);
  const run = (table && table[act]) || (kind === 'click' && GLOBAL_ACTIONS[act]);
  if (!run) return;
  if (kind === 'click') haptic();
  Promise.resolve(run(target, event)).catch((err) => toast(err.message || 'Ошибка', true));
}

document.addEventListener('click', (event) => {
  const tabEl = event.target.closest('[data-tab]');
  if (tabEl) { haptic('select'); App.tab(tabEl.dataset.tab); return; }
  dispatch('click', event);
});
document.addEventListener('input', (event) => dispatch('input', event));
// A slider fires `input` on every step and `change` once when released — the
// label follows the first, the save waits for the second.
document.addEventListener('change', (event) => dispatch('change', event));
document.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && event.target.matches('[data-enter]')) {
    const def = App.screens[App.current().name] || {};
    const run = def.actions && def.actions[event.target.dataset.enter];
    if (run) { event.preventDefault(); Promise.resolve(run(event.target, event)).catch((err) => toast(err.message, true)); }
  }
});
// A focused field pulls the keyboard up; a fixed bar would then float over it.
// A slider takes focus too, but brings no keyboard.
const TYPING = 'input:not([type=range])';
document.addEventListener('focusin', (e) => { if (e.target.matches(TYPING)) App.bar.style.display = 'none'; });
document.addEventListener('focusout', () => { setTimeout(() => {
  if (!document.activeElement || !document.activeElement.matches(TYPING)) App.bar.style.display = '';
}, 80); });
