// Start the panel: Telegram chrome, the back button, the first screen.
'use strict';

(function boot() {
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

  // /app?s=accounts opens a screen directly (the bot can deep-link into it).
  // Home runs first either way: it is what learns which sections the key opens.
  const wanted = new URLSearchParams(location.search).get('s');
  App.tab('home').then(() => {
    if (!wanted || wanted === 'home' || !App.screens[wanted]) return;
    const tabs = App.tabs().map((t) => t[0]);
    if (tabs.indexOf(wanted) !== -1) App.tab(wanted);
    else App.go(wanted);
  });
})();
