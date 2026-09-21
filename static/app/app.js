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
  // Drop every screen's cached rows: the next render asks the server again.
  invalidate() {
    Object.keys(this.screens).forEach((name) => { const d = this.screens[name]; if (d.reset) d.reset(); });
  },
  // The open screens, so a reopen within half an hour lands where it left.
  // The login wizard is never restored: its state is a phone number.
  remember() {
    if (this.stack.some((e) => e.name === 'login')) return;
    Saved.put('stack', {
      at: Date.now(),
      stack: this.stack.map((e) => ({ name: e.name, params: plainParams(e.params) })),
    });
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
      this.remember();
      Pulse.paint();
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
      '<button class="tab' + (t[0] === tabName ? ' on' : '') + (Pulse.fresh[t[0]] ? ' fresh' : '') + '" data-tab="' + t[0] + '">'
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

// Actions worth offering again after the panel is reopened. Only ids and
// status codes ride in these bodies; a login step (phone, code, password)
// is never written to the device.
const RETRYABLE = [
  /^\/api\/app\/pings\/\d+\/status$/,
  /^\/api\/app\/debts\/claim$/,
  /^\/api\/app\/feed\/\d+\/meta$/,
  /^\/api\/app\/feed\/read$/,
  /^\/api\/app\/keys\/\d+$/,
  /^\/api\/app\/settings\/(field|notifications)$/,
  /^\/api\/app\/giveaways\/\d+\/engagement$/,
  /^\/api\/app\/prefs$/,
];
// Per Telegram user: Telegram Desktop runs several accounts in one WebView,
// and localStorage is shared across them — one person's saved screen or
// pending action must never surface in another's panel.
function whoami() {
  const user = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
  if (user && user.id) return String(user.id);
  try {
    const raw = new URLSearchParams(initDataFromHash()).get('user');
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && parsed.id) return String(parsed.id);
  } catch (e) { /* no signed user: nothing personal is stored under it anyway */ }
  return 'anon';
}
const RETRY_KEY = 'pd.retry.' + whoami();
const RETRY_TTL_MS = 30 * 60 * 1000;

// `label` names the action for «Повторить: …» if the session has run out.
async function api(path, body, label) {
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
    // The PC is off for the night (or the tunnel is down): show what this
    // screen last had, read-only. Actions have nothing to fall back on.
    const hit = body === undefined ? Offline.get(path) : null;
    if (hit) {
      Offline.enter(hit.at);
      return hit.data;
    }
    throw new ApiError(0, Offline.on
      ? 'Pulse Desk выключен — это действие подождёт, пока компьютер включится'
      : 'Нет связи с Pulse Desk. Компьютер включён?');
  }
  Offline.leave();
  if (!res.ok) {
    let detail = 'Ошибка ' + res.status;
    try {
      const data = await res.json();
      detail = typeof data.detail === 'string' ? data.detail : detail;
    } catch (e) { /* keep default */ }
    // Access withdrawn (not merely "this section is closed"): forget the snapshot.
    if (res.status === 403 && detail === 'Доступ к боту закрыт') Offline.clear();
    if (res.status === 401 && body !== undefined) {
      if (RETRYABLE.some((re) => re.test(path))) {
        try {
          localStorage.setItem(RETRY_KEY, JSON.stringify({ path: path, body: body, label: label || 'последнее действие', at: Date.now() }));
        } catch (e) { /* private mode: nothing to offer later */ }
      }
      Session.render();
    }
    throw new ApiError(res.status, detail);
  }
  const data = await res.json();
  if (body === undefined) Offline.keep(path, data);
  return data;
}

// ── Offline snapshot ──────────────────────────────────────────────────
// The last answer of a few read-only screens, on this device only (never
// CloudStorage: the data has no business on Telegram's servers). Nothing
// with a post's text, an account, a key or a login is kept — the lists and
// figures are, so the panel is not a blank page from midnight to 10:00.
const Offline = {
  KEY: 'pd.snap.' + whoami(),
  KEEP: [
    /^\/api\/app\/home$/,
    /^\/api\/app\/giveaways(\?(?!.*after=).*)?$/,
    /^\/api\/app\/wins\?days=\d+$/,
    /^\/api\/app\/salary(\?.*)?$/,
    /^\/api\/app\/analytics(\?.*)?$/,
    /^\/api\/app\/market$/,
    /^\/api\/app\/market\/history\?.*$/,
  ],
  LIMIT: 16,
  on: false,
  read() { try { return JSON.parse(localStorage.getItem(this.KEY) || '{}') || {}; } catch (e) { return {}; } },
  keep(path, data) {
    if (!this.KEEP.some((re) => re.test(path))) return;
    const all = this.read();
    all[path] = { at: Date.now(), data: data };
    // Newest LIMIT entries: filter combinations must not grow it forever.
    const keys = Object.keys(all).sort((a, b) => all[b].at - all[a].at);
    keys.slice(this.LIMIT).forEach((k) => { delete all[k]; });
    try { localStorage.setItem(this.KEY, JSON.stringify(all)); } catch (e) { /* full or private: no snapshot */ }
  },
  get(path) { return this.read()[path] || null; },
  // A revoked key must not keep showing what it used to open.
  clear() { try { localStorage.removeItem(this.KEY); } catch (e) { /* ignore */ } },
  enter(at) {
    if (this.on) return;
    this.on = true;
    document.body.classList.add('offline');
    const when = new Date(at);
    const hm = when.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    const stamp = when.toDateString() === new Date().toDateString()
      ? hm : when.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' + hm;
    Session.show('offline', icon('power', 15) + '<span>Pulse Desk выключен · показано то, что было в ' + esc(stamp)
      + '</span><button class="link" data-act="app-retry">Проверить</button>');
  },
  leave() {
    if (!this.on) return;
    this.on = false;
    document.body.classList.remove('offline');
    Session.hide('offline');
    Session.render();
  },
};

// ── Session ───────────────────────────────────────────────────────────
// The server takes actions only from initData younger than an hour (reads
// stay open for a day). The page knows the same clock, so it says so before
// a tap fails instead of after.
const Session = {
  FRESH_SECONDS: 3600,
  WARN_SECONDS: 300,
  el: document.getElementById('notice'),

  age() {
    const at = tg && tg.initDataUnsafe && Number(tg.initDataUnsafe.auth_date);
    return at ? Date.now() / 1000 - at : 0;
  },
  stale() { return this.age() >= this.FRESH_SECONDS; },

  render() {
    if (!this.el || this.el.dataset.kind === 'retry' || this.el.dataset.kind === 'offline') return;
    const left = this.FRESH_SECONDS - this.age();
    if (left > this.WARN_SECONDS) { this.hide('session'); return; }
    this.show('session', icon('clock', 15) + '<span>' + (left > 0
      ? 'Через ' + Math.max(1, Math.round(left / 60)) + ' мин для действий нужно будет открыть панель заново'
      : 'Смотреть можно, а для действий откройте панель заново из бота') + '</span>'
      + '<button class="link" data-act="app-close">Закрыть</button>');
  },

  show(kind, html) {
    if (!this.el) return;
    this.el.dataset.kind = kind;
    this.el.innerHTML = html;
    this.el.hidden = false;
  },
  hide(kind) {
    if (!this.el || (kind && this.el.dataset.kind !== kind)) return;
    this.el.hidden = true;
    this.el.dataset.kind = '';
    this.el.innerHTML = '';
  },

  // After a reopen: the action a stale session refused, if it is recent.
  offerRetry() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(RETRY_KEY) || 'null'); } catch (e) { saved = null; }
    if (!saved) return;
    if (!saved.at || Date.now() - saved.at > RETRY_TTL_MS || !RETRYABLE.some((re) => re.test(saved.path))) {
      this.dropRetry();
      return;
    }
    this.show('retry', icon('refresh', 15) + '<span>Не сохранилось: ' + esc(saved.label) + '</span>'
      + '<button class="link" data-act="retry-run">Повторить</button>'
      + '<button class="icon-btn" data-act="retry-drop" aria-label="Не повторять">' + icon('x', 15) + '</button>');
  },
  dropRetry() {
    try { localStorage.removeItem(RETRY_KEY); } catch (e) { /* ignore */ }
    this.hide('retry');
  },
  async runRetry() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(RETRY_KEY) || 'null'); } catch (e) { saved = null; }
    this.dropRetry();
    if (!saved) return;
    await api(saved.path, saved.body, saved.label);
    toast('Готово: ' + saved.label);
    App.invalidate();
    return App.render({ quiet: true });
  },
};

// ── Saved view state ──────────────────────────────────────────────────
// Filters and the open screen, per Telegram user, across devices (Store is
// CloudStorage: 4096 characters a value, so it stays one small object).
// Search text is not kept — an old query silently narrowing the feed next
// week reads as missing data.
const Saved = {
  data: {},
  KEY: 'pd.state.' + whoami(),
  // CloudStorage answers by callback; a client that never calls back must not
  // hold the whole panel on a blank screen.
  async load() {
    const timeout = new Promise((resolve) => setTimeout(() => resolve({}), 900));
    const value = await Promise.race([Store.json(this.KEY, {}), timeout]);
    this.data = value && typeof value === 'object' ? value : {};
  },
  get(key, fallback) { return this.data[key] !== undefined ? this.data[key] : fallback; },
  put(key, value) {
    this.data[key] = value;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => Store.set(this.KEY, JSON.stringify(this.data)), 400);
  },
};

// Outside Telegram (a browser tab during development) the WebApp script still
// loads but has no initData; the signed string then rides in the URL hash.
function initDataFromHash() {
  const m = /tgWebAppData=([^&]*)/.exec(location.hash);
  return m ? decodeURIComponent(m[1]) : '';
}

// Params worth keeping across a reopen: strings and numbers, no `force`.
function plainParams(params) {
  const out = {};
  Object.keys(params || {}).forEach((k) => {
    const v = params[k];
    if (k !== 'force' && (typeof v === 'string' || typeof v === 'number')) out[k] = v;
  });
  return out;
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

// «Вернуть» for a status change: the server answered with an undo token
// (bot/undo — the same 30-second snapshot the bot's «↩️ Отменить» uses).
function undoToast(message, token, after) {
  if (!token) { toast(message); return; }
  toast(message, false, {
    label: 'Вернуть',
    run: async () => {
      await api('/api/app/undo', { token: token });
      toast('Вернули как было');
      if (after) return after();
      return null;
    },
  });
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
  'retry-run': () => Session.runRetry(),
  'pulse-refresh': () => Pulse.refresh(),
  'retry-drop': () => Session.dropRetry(),
};

// A refused action: a stale session gets a way out, anything else a toast.
function failed(err) {
  if (err && err.status === 401) {
    Sheet.open('Сессия устарела', '<p class="hint" style="margin:0 0 14px">Смотреть можно и дальше, но для действий '
      + 'Telegram должен выдать панели свежую подпись — закройте её и откройте из бота. '
      + 'Если действие не сохранилось, панель предложит повторить его.</p>'
      + '<button class="btn primary" data-act="app-close">' + icon('x', 18) + 'Закрыть панель</button>');
    haptic('warning');
    return;
  }
  toast((err && err.message) || 'Ошибка', true);
}

function dispatch(kind, event) {
  const target = event.target.closest('[data-act]');
  if (!target) return;
  const act = target.dataset.act;
  const def = App.screens[App.current().name] || {};
  const table = kind === 'click' ? def.actions : (kind === 'change' ? def.changes : def.inputs);
  const run = (table && table[act]) || (kind === 'click' && GLOBAL_ACTIONS[act]);
  if (!run) return;
  if (kind === 'click') haptic();
  Promise.resolve(run(target, event)).catch(failed);
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
    if (run) { event.preventDefault(); Promise.resolve(run(event.target, event)).catch(failed); }
  }
});
// A focused field pulls the keyboard up; a fixed bar would then float over it.
// A slider takes focus too, but brings no keyboard.
const TYPING = 'input:not([type=range])';
document.addEventListener('focusin', (e) => { if (e.target.matches(TYPING)) App.bar.style.display = 'none'; });
document.addEventListener('focusout', () => { setTimeout(() => {
  if (!document.activeElement || !document.activeElement.matches(TYPING)) App.bar.style.display = '';
}, 80); });

// ── Pulse: "is there anything new?" while the panel is on screen ──────
// Every 30 s, and only while visible, the page asks /api/app/pulse (cached
// per person on the server). Newer rows light a dot on their tab and a pill
// on the open list; the list itself is never repainted underneath the
// reader — that would steal the scroll and any focused field. Home has
// nothing to type into, so it refreshes itself.
const Pulse = {
  EVERY_MS: 30000,
  marks: { feed: null, giveaways: null },   // what the list last showed
  fresh: {},
  last: null,
  inactive: false,

  value(tab, p) {
    if (tab === 'feed') return p.latest;
    if (tab === 'giveaways') return Math.max(p.latest_giveaway || 0, p.latest_win || 0) || undefined;
    return undefined;
  },

  visible() { return document.visibilityState === 'visible' && !this.inactive; },

  start() {
    setInterval(() => this.tick(), this.EVERY_MS);
    document.addEventListener('visibilitychange', () => { if (this.visible()) this.tick(); });
    if (tg && tg.onEvent) {
      try {
        tg.onEvent('activated', () => { this.inactive = false; this.tick(); });
        tg.onEvent('deactivated', () => { this.inactive = true; });
      } catch (e) { /* older clients: visibilitychange is enough */ }
    }
  },

  async tick() {
    if (!this.visible() || this.busy) return;
    this.busy = true;
    let p;
    try { p = await api('/api/app/pulse'); } catch (e) { return; } finally { this.busy = false; }
    const before = this.last;
    this.last = p;
    Object.keys(this.marks).forEach((tab) => {
      const now = this.value(tab, p);
      if (now === undefined) return;
      if (this.marks[tab] === null) this.marks[tab] = now;
      else if (now > this.marks[tab]) this.fresh[tab] = true;
    });
    this.paint();
    const cur = App.current().name;
    const changed = before && (p.latest !== before.latest || p.queue !== before.queue
      || p.level !== before.level || p.latest_win !== before.latest_win);
    const typing = document.activeElement && document.activeElement.matches('input, textarea');
    if (changed && cur === 'home' && !typing && !Sheet.isOpen()) App.render({ quiet: true });
  },

  // A list that has just loaded has seen everything up to now.
  saw(tab) {
    this.marks[tab] = this.last ? this.value(tab, this.last) : null;
    delete this.fresh[tab];
    this.paint();
    // The server may know of rows newer than the last pulse: ask again soon.
    clearTimeout(this.soon);
    this.soon = setTimeout(() => { this.marks[tab] = null; this.tick(); }, 1500);
  },

  paint() {
    App.bar.querySelectorAll('.tab').forEach((el) => {
      el.classList.toggle('fresh', Boolean(this.fresh[el.dataset.tab]));
    });
    const old = document.querySelector('.pulse-pill');
    if (old) old.remove();
    const cur = App.current().name;
    if (!this.fresh[cur]) return;
    const screen = App.el.querySelector('.screen');
    if (!screen) return;
    const pill = document.createElement('button');
    pill.className = 'pulse-pill';
    pill.dataset.act = 'pulse-refresh';
    pill.innerHTML = icon('refresh', 15) + 'Есть новое — обновить';
    screen.insertBefore(pill, screen.firstChild);
  },

  refresh() {
    const cur = App.current().name;
    const def = App.screens[cur];
    if (def && def.reset) def.reset();
    delete this.fresh[cur];
    window.scrollTo(0, 0);
    return App.render({ quiet: true });
  },
};
