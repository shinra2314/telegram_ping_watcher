// Start the panel: Telegram chrome, the back button, saved state, the first screen.
'use strict';

// Deep links from the bot: `/app?s=giveaway&id=123`. Only these screens take
// parameters, and only in these shapes — the query string is not trusted.
const DEEP_PARAMS = {
  giveaway: { id: /^\d{1,10}$/ },
  ping: { id: /^\d{1,10}$/ },
  salary: { month: /^\d{4}-\d{2}$/, account: /^[^<>"'&]{1,64}$/ },
};
// A reopen within this long lands on the screen that was open.
const RESUME_MS = 30 * 60 * 1000;
// Which section of /api/app/home a screen lives in. A saved screen or a
// link to one the key does not open is dropped, not shown as an error.
const SCREEN_SECTION = {
  giveaways: 'giveaways', giveaway: 'giveaways', wins: 'giveaways',
  feed: 'feed', ping: 'feed', converter: 'market', analytics: 'analytics', prefs: 'prefs',
  salary: 'salary', debts: 'debts',
  accounts: 'accounts', login: 'accounts', triage: 'accounts', cleanup: 'accounts',
  system: 'accounts', keys: 'accounts', key: 'accounts', member: 'accounts', settings: 'accounts',
};

function opens(name) {
  const section = SCREEN_SECTION[name];
  return !section || Boolean(App.sections[section]);
}

function deepLink() {
  const query = new URLSearchParams(location.search);
  const name = query.get('s');
  if (!name || name === 'home' || !App.screens[name] || !opens(name)) return null;
  const rules = DEEP_PARAMS[name] || {};
  const params = {};
  Object.keys(rules).forEach((key) => {
    const value = query.get(key);
    if (value && rules[key].test(value)) params[key] = value;
  });
  if (rules.id && !params.id) return null;
  return { name: name, params: params };
}

// A card opens above its own tab, so «назад» lands in the list it belongs to.
function openAt(name, params) {
  const tabs = App.tabs().map((t) => t[0]);
  if (tabs.indexOf(name) !== -1) return App.tab(name, params);
  const def = App.screens[name];
  if (def.tab && def.tab !== name && tabs.indexOf(def.tab) !== -1) {
    App.stack = [{ name: def.tab, params: {} }, { name: name, params: params }];
    return App.render();
  }
  return App.go(name, params);
}

function resume(saved) {
  if (!saved || !saved.at || Date.now() - saved.at > RESUME_MS || !Array.isArray(saved.stack)) return null;
  const stack = saved.stack.filter((e) => e && App.screens[e.name] && e.name !== 'login');
  if (!stack.length || (stack.length === 1 && stack[0].name === 'home')) return null;
  if (!stack.every((e) => opens(e.name))) return null;
  // The key may have lost a section since: then the old tab is not offered.
  const tabs = App.tabs().map((t) => t[0]);
  if (tabs.indexOf(stack[0].name) === -1) return null;
  App.stack = stack.map((e) => ({ name: e.name, params: Object.assign({}, e.params) }));
  return App.render();
}

// Telegram's own «Настройки» item in the panel's ⋯ menu: the owner's
// settings, a guest's notifications. Only on clients that have it.
function nativeButtons() {
  if (!tg || !tg.SettingsButton || !tg.isVersionAtLeast || !tg.isVersionAtLeast('7.0')) return;
  const target = App.sections.accounts ? 'settings' : (App.sections.prefs ? 'prefs' : null);
  if (!target) return;
  try {
    tg.SettingsButton.onClick(() => { if (App.current().name !== target) App.go(target); });
    tg.SettingsButton.show();
  } catch (e) { /* older clients */ }
}

(async function boot() {
  document.getElementById('mark').innerHTML = icon('pulse', 16);
  const refresh = document.getElementById('refresh');
  refresh.innerHTML = icon('refresh', 18);
  refresh.addEventListener('click', async () => {
    haptic();
    refresh.classList.add('spin');
    const cur = App.current();
    cur.params = Object.assign({}, cur.params, { force: 1 });
    try { await App.render({ quiet: true }); } finally {
      delete cur.params.force;
      refresh.classList.remove('spin');
    }
  });

  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor('#0f1113');
      tg.setBackgroundColor('#0f1113');
      if (tg.setBottomBarColor) tg.setBottomBarColor('#0f1113');
      if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
    } catch (e) { /* older clients */ }
    if (tg.BackButton) {
      tg.BackButton.onClick(() => {
        if (Sheet.isOpen()) { Sheet.close(); return; }
        App.back();
      });
    }
  }

  await Saved.load();
  GW.restore();
  FEED.restore();
  if ([7, 30, 90].indexOf(Saved.get('ana_days', 30)) !== -1) ANA.days = Saved.get('ana_days', 30);
  if ('achn'.indexOf(Saved.get('debts_seg', 'a')) !== -1) Debts.seg = Saved.get('debts_seg', 'a');

  // Network-first shell cache for the hours the PC is off (static/app/sw.js).
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/app-sw.js').catch(() => { /* WebView without workers */ });
  }

  Session.render();
  setInterval(() => Session.render(), 30000);
  Session.offerRetry();
  Pulse.start();

  // Read before the first render: rendering home already overwrites it.
  const previous = Saved.get('stack', null);
  // Home runs first either way: it is what learns which sections the key opens.
  await App.tab('home');
  nativeButtons();
  const link = deepLink();
  if (link) return openAt(link.name, link.params);
  return resume(previous);
})();
