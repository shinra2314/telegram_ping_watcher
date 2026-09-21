// Home: status hero (owner) or the guest's own card, tiles for every open
// section, the guest's last wins, a rates strip.
'use strict';

App.register('home', {
  tab: 'home',
  title: '',

  async render() {
    const data = await api('/api/app/home');
    App.sections = data.sections || {};
    App.role = data.role;
    App.syncChrome(this);
    // A guest's name heads their own card below; the bar then says what this is.
    App.sub.textContent = data.me ? 'моя панель' : (data.name || '');
    const s = App.sections;
    const c = data.counters || {};
    let html = '';

    if (data.summary) html += hero(data.summary);
    if (data.me) html += profileCard(data.name, data.me) + mineBlock(data.mine, data.me);

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
    if (s.feed) {
      tiles.push(tile({ act: 'open-feed', icon: 'list', label: 'Лента', value: '',
        small: s.search ? 'упоминания и поиск' : 'упоминания' }));
    }
    if (s.analytics) {
      const own = data.me && data.me.accounts && data.me.accounts.length;
      tiles.push(tile({ act: 'open-analytics', icon: 'chart', label: 'Статистика', value: '',
        small: own ? 'по вашим аккаунтам' : 'по всем аккаунтам' }));
    }
    if (s.salary) tiles.push(tile({ act: 'open-salary', icon: 'cash', label: 'Зарплата', value: '', small: 'по книге' }));
    if (s.prefs && data.me) {
      tiles.push(tile({ act: 'open-prefs', icon: data.me.muted ? 'bell-off' : 'bell', label: 'Уведомления', value: '',
        small: data.me.muted ? 'выключены' : (data.me.notify.length
          ? data.me.notify.length + ' ' + plural(data.me.notify.length, 'тип', 'типа', 'типов') : 'закрыты владельцем') }));
    }
    if (s.market) tiles.push(tile({ act: 'open-converter', icon: 'exchange', label: 'Конвертер', value: '', small: 'валюты и крипта' }));
    // An odd tile would leave a hole in the two-column grid; widen the last one.
    if (tiles.length % 2 === 1) tiles[tiles.length - 1] = tiles[tiles.length - 1].replace('class="tile', 'class="tile wide');

    if (tiles.length) {
      html += '<div class="section-label">Разделы</div><div class="tiles">' + tiles.join('') + '</div>';
    } else {
      html += emptyView('lock', 'Разделы закрыты', 'Ваш ключ пока не открывает ни одного раздела панели.');
    }

    const wins = (data.mine && data.mine.recent_wins) || [];
    if (wins.length) {
      html += '<div class="section-label">Последние победы<span class="more" data-act="open-wins">Все ›</span></div>'
        + '<div class="list">' + wins.map((w) => item({
          act: 'open-win', data: { id: w.id },
          lead: icon('trophy', 16), leadCls: 'on',
          title: esc(w.chat),
          desc: esc(winStatus(w.status)),
          end: '<div class="d num">' + esc(fmtTime(w.detected_at)) + '</div>',
        })).join('') + '</div>';
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
    'open-feed': () => App.tab('feed'),
    'open-analytics': () => App.go('analytics'),
    'open-prefs': () => App.go('prefs'),
    'open-win': (el) => App.go('giveaway', { id: el.dataset.id }),
    'open-wins': () => { GW.wins = true; GW.keep(); GW.reset(); return App.tab('giveaways'); },
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

// The guest's card: who they are to the bot and what their key opened. The
// owner never sees it — their home starts with the system hero instead.
function profileCard(name, me) {
  const premium = me.role === 'premium';
  const accounts = me.accounts && me.accounts.length
    ? me.accounts.map((a) => '@' + a).join(', ') : 'все аккаунты';
  const facts = [
    profileFact('at', 'Аккаунты', accounts),
    profileFact('clock', 'Уведомления', me.delay_minutes ? 'через ' + me.delay_text : 'мгновенно'),
  ];
  if (me.access && me.access.scheduled) {
    facts.push(profileFact('lock', 'Доступ', me.access.until ? 'до ' + fmtTime(me.access.until) : 'по расписанию'));
  }
  const letter = (String(name || '?').trim()[0] || '?').toUpperCase();
  return '<div class="panel keyed profile">'
    + '<div class="profile-top"><div class="avatar">' + esc(letter) + '</div>'
    + '<div class="who"><div class="h">' + esc(name || 'Гость') + '</div>'
    + '<span class="badge ' + (premium ? 'on' : '') + '">' + (premium ? icon('flame', 12) + 'Премиум' : icon('user', 12) + 'Просмотр')
    + '</span></div></div>'
    + '<div class="facts">' + facts.join('') + '</div>'
    + (me.muted
      ? '<div class="muted-line" data-act="open-prefs">' + icon('bell-off', 16)
        + '<span>Уведомления выключены</span><span class="go">Включить ›</span></div>'
      : '')
    + '</div>';
}

function profileFact(iconName, label, value) {
  return '<div class="fact"><span class="k">' + icon(iconName, 14) + esc(label) + '</span>'
    + '<span class="v">' + esc(value) + '</span></div>';
}

function mineBlock(mine, me) {
  if (!mine) return '';
  const w = mine.wins || {};
  const e = mine.engagement || {};
  const scoped = me.accounts && me.accounts.length;
  // «Участвую / пропустил» are buttons on giveaway notifications: a key that
  // never gets giveaways would only ever show two zeros there.
  const engaged = App.sections.giveaways || e.joined || e.skipped;
  return '<div class="section-label">Моё за ' + (mine.days || 30) + ' дней</div><div class="kv">'
    + '<div class="cell"' + (engaged ? '' : ' style="grid-column:span 2"') + '><div class="k">'
    + (scoped ? 'Победы аккаунтов' : 'Победы всех аккаунтов') + '</div>'
    + '<div class="v">' + fmtInt(w.wins) + '<small class="of">забрано ' + fmtInt(w.claimed) + '</small></div></div>'
    + (engaged
      ? '<div class="cell"><div class="k">Участвую</div>'
        + '<div class="v">' + fmtInt(e.joined) + '<small class="of">пропустил ' + fmtInt(e.skipped) + '</small></div></div>'
      : '')
    + '</div>';
}

function winStatus(code) {
  return { claimed: 'забрано', missed: 'пропущено', scam: 'скам', closed: 'закрыто' }[code] || 'не забрано';
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
