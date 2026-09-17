// Home: status hero (owner), tiles for every open section, a rates strip.
'use strict';

App.register('home', {
  tab: 'home',
  title: '',

  async render() {
    const data = await api('/api/app/home');
    App.sections = data.sections || {};
    App.role = data.role;
    App.syncChrome(this);
    App.sub.textContent = data.name || '';
    const s = App.sections;
    const c = data.counters || {};
    let html = '';

    if (data.summary) html += hero(data.summary);

    const tiles = [];
    if (s.giveaways) {
      // The queue is often wins only; a badge repeating the same figure is noise.
      const allWins = c.wins && c.wins === c.giveaways;
      tiles.push(tile({
        act: 'open-giveaways', icon: allWins ? 'trophy' : 'gift', label: 'Розыгрыши',
        value: fmtInt(c.giveaways),
        small: allWins ? plural(c.wins, 'победа', 'победы', 'побед') : 'в очереди',
        hot: c.wins > 0,
        badge: c.wins && !allWins ? '<span class="badge on">' + icon('trophy', 12) + ' ' + c.wins + '</span>' : '',
      }));
    }
    if (s.debts) {
      tiles.push(tile({
        act: 'open-debts', icon: 'wallet', label: 'Долги',
        value: fmtInt(c.debts), small: c.debts_value ? fmtUsd(c.debts_value) : '',
      }));
    }
    if (s.accounts) {
      const allOnline = c.accounts_total && c.accounts_online >= c.accounts_total;
      tiles.push(tile({
        act: 'open-accounts', icon: 'users', label: 'Аккаунты',
        value: (c.accounts_online || 0) + '<small>/ ' + (c.accounts_total || 0) + '</small>',
        badge: '<span class="dot ' + (allOnline ? 'on' : 'warn') + '"></span>',
      }));
    }
    if (s.salary) tiles.push(tile({ act: 'open-salary', icon: 'cash', label: 'Зарплата', value: '', small: 'по книге' }));
    if (s.market) tiles.push(tile({ act: 'open-converter', icon: 'exchange', label: 'Конвертер', value: '', small: 'валюты и крипта' }));
    // An odd tile would leave a hole in the two-column grid; widen the last one.
    if (tiles.length % 2 === 1) tiles[tiles.length - 1] = tiles[tiles.length - 1].replace('class="tile', 'class="tile wide');

    if (tiles.length) {
      html += '<div class="section-label">Разделы</div><div class="tiles">' + tiles.join('') + '</div>';
    } else {
      html += emptyView('lock', 'Разделы закрыты', 'Ваш ключ пока не открывает ни одного раздела панели.');
    }

    if (data.ticker && data.ticker.length) {
      html += '<div class="section-label">Курсы<span class="more" data-act="open-converter">Конвертер ›</span></div>'
        + '<div class="ticker">' + data.ticker.map((t) =>
          '<div class="tick" data-act="open-converter"><div class="c">' + esc(t.code) + '</div><div class="p">'
          + esc(t.usd >= 1 ? fmtUsd(t.usd) : '$' + t.usd.toFixed(4))
          + '</div><div class="u">' + (t.uah ? '₴' + esc(t.uah >= 100 ? fmtInt(t.uah) : t.uah.toFixed(2)) : '') + '</div></div>').join('') + '</div>';
    }
    return html;
  },

  actions: {
    'open-giveaways': () => App.tab('giveaways'),
    'open-converter': () => App.tab('converter'),
    'open-debts': () => App.go('debts'),
    'open-accounts': () => App.go('accounts'),
    'open-salary': () => App.go('salary'),
    'open-attention': (el) => {
      const key = el.dataset.key;
      if (key === 'accounts' && App.sections.accounts) return App.go('accounts');
      if (key === 'giveaway-action' && App.sections.giveaways) return App.tab('giveaways');
      return null;
    },
  },
});

function hero(summary) {
  const level = summary.level === 'bad' ? 'bad' : (summary.level === 'warn' ? 'warn' : 'good');
  const counts = summary.counts || {};
  const scan = summary.scan || {};
  let html = '<div class="panel hero ' + level + '">'
    + '<div class="lamp">' + icon(level === 'good' ? 'check' : (level === 'bad' ? 'alert' : 'pulse'), 22) + '</div>'
    + '<div style="flex:1;min-width:0"><div class="h">' + esc(summary.headline || '') + '</div>'
    + '<div class="s">' + fmtInt(counts.new_pings) + ' ' + plural(counts.new_pings || 0, 'новое', 'новых', 'новых')
    + ' · ' + fmtInt(counts.total_pings) + ' всего</div>'
    + (scan.running
      ? '<div class="progress"><i style="width:' + (scan.percent || 0) + '%"></i></div>'
        + '<div class="s" style="margin-top:6px">Скан ' + (scan.percent || 0) + '% · ' + esc(scan.current_account || '') + '</div>'
      : '')
    + '</div></div>';
  const items = (summary.attention || []).filter((a) => a.key !== 'calm').slice(0, 4);
  if (items.length) {
    html += '<div class="section-label">Требует внимания</div><div class="list">'
      + items.map((a) => item({
        act: 'open-attention', data: { key: a.key }, chev: false,
        lead: icon(a.tone === 'bad' ? 'flame' : 'alert', 17), leadCls: a.tone === 'bad' ? 'bad' : 'warn',
        title: esc(a.title), desc: esc(a.text), end: '<span class="num">' + esc(a.value) + '</span>',
      })).join('') + '</div>';
  }
  return html;
}

function tile(t) {
  return '<div class="tile' + (t.hot ? ' hot' : '') + '" data-act="' + t.act + '">'
    + (t.badge ? '<span class="corner">' + t.badge + '</span>' : '')
    + '<div class="ico">' + icon(t.icon, 24) + '</div>'
    + '<div>' + (t.value
      ? '<div class="val">' + t.value + (t.small ? '<small>' + esc(t.small) + '</small>' : '') + '</div>'
      : (t.small ? '<div class="note">' + esc(t.small) + '</div>' : ''))
    + '<div class="lbl">' + esc(t.label) + '</div></div></div>';
}
