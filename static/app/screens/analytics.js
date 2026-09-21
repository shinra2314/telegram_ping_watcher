// Statistics, counted by the server over the key's own accounts only. `stats`
// opens the summary and the day chart; `analytics` adds hours, chats, authors,
// the per-account split and detection delay.
'use strict';

const WEEKDAY = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
// The period of the hours / chats / authors / speed breakdowns (remembered).
const ANA = { days: 30 };

App.register('analytics', {
  tab: 'more',
  title: 'Статистика',

  async render() {
    const r = await api('/api/app/analytics?days=' + ANA.days);
    const s = r.summary || {};
    App.sub.textContent = r.scope.length ? r.scope.map((a) => '@' + a).join(', ') : 'все аккаунты';

    let html = '<div class="scope-line">' + icon(r.scope.length ? 'at' : 'users', 15)
      + '<span>' + (r.scope.length
        ? 'Только упоминания ' + esc(r.scope.map((a) => '@' + a).join(', '))
        : 'По всем отслеживаемым аккаунтам') + '</span></div>';

    if (!s.total) {
      return html + emptyView('chart', 'Данных пока нет', 'Статистика появится после первых упоминаний.');
    }
    if (r.full) html += seg([['7', '7 дней'], ['30', '30 дней'], ['90', '90 дней']], String(ANA.days), 'days') + '<div style="height:12px"></div>';

    html += '<div class="kv">'
      + statCell('За сутки', fmtInt(s.last_24h))
      + statCell('За неделю', fmtInt(s.last_7d))
      + statCell('Победы', fmtInt(s.wins), s.win_rate ? String(s.win_rate).replace('.', ',') + '% упоминаний' : '')
      + statCell('Розыгрыши', fmtInt(s.giveaways), 'всего ' + fmtInt(s.total))
      + '</div>';

    html += daysChart(r.daily || []);
    if (!r.full) return html;

    const hours = r.hours || [];
    if (hours.some((h) => h > 0)) {
      const max = Math.max.apply(null, hours) || 1;
      const peak = hours.indexOf(max);
      html += '<div class="section-label">Часы за ' + (r.window_days || 30) + ' дней'
        + '<span class="more" style="cursor:default">пик ' + String(peak).padStart(2, '0') + ':00</span></div>'
        + '<div class="panel pad"><div class="hours">' + hours.map((h, i) =>
          '<i title="' + String(i).padStart(2, '0') + ':00 — ' + h + '" style="opacity:' + (h ? (0.14 + 0.86 * h / max).toFixed(2) : '0.06') + '"></i>').join('')
        + '</div><div class="hours-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div></div>';
    }

    if (r.accounts && r.accounts.length > 1) {
      const top = Math.max.apply(null, r.accounts.map((a) => a.mentions)) || 1;
      html += '<div class="section-label">Аккаунты</div><div class="panel pad bars">'
        + r.accounts.map((a) => barLine('@' + a.name, fmtInt(a.mentions) + winsNote(a.wins), a.mentions / top)).join('')
        + '</div>';
    }

    if (r.chats && r.chats.length) {
      html += '<div class="section-label">Где упоминают · ' + (r.window_days || 30) + ' дн</div><div class="list">'
        + r.chats.map((c, i) => item({
          // With the search grant a chat opens the feed narrowed to it.
          act: App.sections.search ? 'chat' : '', data: { chat: c.chat }, chev: false,
          lead: '<span class="num" style="font-size:13px;font-weight:700">' + (i + 1) + '</span>', leadCls: c.wins ? 'on' : '',
          title: esc(c.chat),
          // A chat with no giveaways of its own (a comment thread, a results
          // channel) would otherwise lead with «0 розыгрышей».
          desc: [
            c.giveaways ? fmtInt(c.giveaways) + ' ' + plural(c.giveaways, 'розыгрыш', 'розыгрыша', 'розыгрышей') : '',
            c.wins ? fmtInt(c.wins) + ' ' + plural(c.wins, 'победа', 'победы', 'побед') : '',
          ].filter(Boolean).join(' · ') || 'упоминания',
          end: '<div class="t num">' + fmtInt(c.count) + '</div>',
        })).join('') + '</div>';
    }

    if (r.senders && r.senders.length) {
      const top = Math.max.apply(null, r.senders.map((a) => a.count)) || 1;
      html += '<div class="section-label">Кто пишет · ' + (r.window_days || 30) + ' дн</div><div class="panel pad bars">'
        + r.senders.map((a) => barLine(a.sender, fmtInt(a.count) + winsNote(a.wins), a.count / top)).join('')
        + '</div>';
    }

    const lat = r.latency || {};
    const posts = lat.posts || {};
    const wins = lat.wins || {};
    if (posts.count || wins.count) {
      html += '<div class="section-label">Скорость · ' + (r.window_days || 30) + ' дн</div><div class="kv">'
        + statCell('Пост → замечен', posts.count ? minutes(posts.median) : '—', posts.count ? '9 из 10 — до ' + minutes(posts.p90) : '')
        + statCell('Итоги → победа', wins.count ? minutes(wins.median) : '—', wins.count ? '9 из 10 — до ' + minutes(wins.p90) : '')
        + '</div>';
    }
    return html;
  },

  actions: {
    days: (el) => { ANA.days = Number(el.dataset.v); Saved.put('ana_days', ANA.days); return App.render({ quiet: true }); },
    chat: (el) => {
      // "Name (@handle)": the handle is what full-text search finds reliably.
      const name = el.dataset.chat || '';
      const handle = /\(@([\w\d_]+)\)/.exec(name);
      FEED.q = handle ? handle[1] : name.replace(/[()"]/g, ' ').trim().slice(0, 60);
      FEED.reset();
      return App.tab('feed');
    },
  },
});

function statCell(k, v, note) {
  return '<div class="cell"><div class="k">' + esc(k) + '</div><div class="v">' + v
    + (note ? '<small class="of">' + esc(note) + '</small>' : '') + '</div></div>';
}

function winsNote(wins) {
  return wins ? ' · <span class="accent">' + fmtInt(wins) + ' ' + plural(wins, 'победа', 'победы', 'побед') + '</span>' : '';
}

function barLine(label, value, share) {
  return '<div class="barline"><div class="top"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0">'
    + esc(label) + '</span><span class="num dim" style="flex:none;margin-left:10px">' + value + '</span></div>'
    + '<div class="track"><i style="width:' + Math.max(2, Math.round(100 * share)) + '%"></i></div></div>';
}

function minutes(value) {
  const v = Number(value) || 0;
  if (v < 1) return 'до 1 мин';
  if (v < 90) return Math.round(v) + ' мин';
  return (v / 60).toFixed(1).replace('.', ',') + ' ч';
}

// Two weeks as columns: the whole column is every mention, its citron base the
// wins among them. Today is the last column.
function daysChart(days) {
  if (!days.length) return '';
  const max = Math.max.apply(null, days.map((d) => d.total)) || 1;
  const total = days.reduce((sum, d) => sum + d.total, 0);
  const cols = days.map((d, i) => {
    const h = Math.round(100 * d.total / max);
    const w = d.total ? Math.round(h * d.wins / d.total) : 0;
    return '<div class="col' + (i === days.length - 1 ? ' today' : '') + '" title="' + esc(d.day) + ': ' + d.total + '">'
      + (d.total ? '<i style="height:' + Math.max(2, h - w) + '%"></i>' : '<i></i>')
      + (w ? '<i class="win" style="height:' + w + '%"></i>' : '')
      + '</div>';
  }).join('');
  const axis = days.map((d, i) => {
    const date = new Date(d.day + 'T12:00:00');
    const label = i === days.length - 1 ? 'сег' : (i % 2 === 1 ? '' : WEEKDAY[date.getDay()]);
    return '<span>' + label + '</span>';
  }).join('');
  return '<div class="section-label">' + days.length + ' дней<span class="more" style="cursor:default">'
    + fmtInt(total) + ' ' + plural(total, 'упоминание', 'упоминания', 'упоминаний') + '</span></div>'
    + '<div class="panel pad"><div class="cols">' + cols + '</div><div class="cols-axis">' + axis + '</div>'
    + '<div class="legend"><span><i></i>упоминания</span><span><i class="win"></i>победы</span></div></div>';
}
