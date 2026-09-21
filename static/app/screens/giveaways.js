// Giveaways and wins: the "needs action" queue, filtered like the bot's gw:f:
// callback, plus a card with the post, its link and the owner's quick statuses.
'use strict';

// Filters persist (Saved 'gw'); rows are cached so «назад» from a card and a
// switch of tabs do not refetch the queue. `rows` null = ask the server.
const GW = {
  sort: 'd', wins: false, account: -1,
  rows: null, meta: null, at: 0,
  STALE_MS: 60000,
  query() { return '/api/app/giveaways?sort=' + this.sort + '&wins=' + this.wins + '&account=' + this.account; },
  keep() { Saved.put('gw', { sort: this.sort, wins: this.wins, account: this.account }); },
  restore() {
    const s = Saved.get('gw', {});
    if (s.sort === 'd' || s.sort === 'p') this.sort = s.sort;
    this.wins = Boolean(s.wins);
    this.account = Number.isInteger(s.account) ? s.account : -1;
  },
  reset() { this.rows = null; },
  drop(id) {
    if (!this.rows) return;
    const before = this.rows.length;
    this.rows = this.rows.filter((r) => r.id !== Number(id));
    if (this.meta && this.rows.length < before) this.meta.total = Math.max(0, this.meta.total - 1);
  },
  async load() {
    const data = await api(this.query());
    // A saved account the key no longer covers would filter to nothing.
    if (this.account >= (data.accounts || []).length && this.account !== -1) {
      this.account = -1;
      this.keep();
      return this.load();
    }
    this.rows = data.items;
    this.meta = data;
    this.at = Date.now();
  },
  async more() {
    const last = this.rows[this.rows.length - 1];
    const data = await api(this.query() + '&after=' + last.id + '&loaded=' + this.rows.length);
    const have = new Set(this.rows.map((r) => r.id));
    this.rows = this.rows.concat(data.items.filter((r) => !have.has(r.id)));
    this.meta = Object.assign({}, this.meta, { has_more: data.has_more, total: data.total });
  },
};

const GW_STATUS = {
  pending: ['ждёт итогов', 'clock', 'warn'],
  claimed: ['Забрал', 'check', 'good'],
  missed: ['Пропустил', 'x', ''],
  scam: ['Скам', 'shield', 'bad'],
  closed: ['Закрыт', 'lock', ''],
};

App.register('giveaways', {
  tab: 'giveaways',
  title: 'Розыгрыши',
  reset: () => GW.reset(),

  async render(params) {
    if (params.force || !GW.rows || Date.now() - GW.at > GW.STALE_MS) await GW.load();
    const last = GW.meta;
    const rows = GW.rows;
    App.sub.textContent = last.total + ' в очереди';
    const accountName = GW.account >= 0 ? last.accounts[GW.account] : null;

    let html = seg([['all', 'Все'], ['wins', 'Победы']], GW.wins ? 'wins' : 'all', 'kind')
      + '<div class="chips">'
      + '<button class="chip" data-act="sort">' + icon('clock', 15) + (GW.sort === 'd' ? 'По обнаружению' : 'По дате поста') + '</button>'
      + (last.accounts.length > 1
        ? '<button class="chip' + (accountName ? ' on' : '') + '" data-act="account">' + icon('users', 15)
          + esc(accountName ? '@' + accountName : 'Все аккаунты') + '</button>'
        : '')
      + '</div>';

    if (!rows.length) {
      return html + emptyView(GW.wins ? 'trophy' : 'gift', GW.wins ? 'Открытых побед нет' : 'Очередь пуста',
        'Новые розыгрыши появятся здесь после скана.');
    }
    html += '<div class="list">' + rows.map((r) => item({
      act: 'open', data: { id: r.id },
      lead: icon(r.is_win ? 'trophy' : 'gift', 17), leadCls: r.is_win ? 'on' : '',
      title: esc(r.chat),
      desc: (r.deleted ? 'удалён · ' : '') + esc(priorityLabel(r.priority)),
      end: '<div class="gw-end"><div class="d num">' + esc(fmtTime(GW.sort === 'p' ? r.date : r.detected_at)) + '</div>'
        + (last.can_edit
          ? '<button class="icon-btn gw-x" data-act="dismiss" data-id="' + r.id + '" data-status="' + esc(r.status || '')
            + '" data-action="' + esc(r.action || '') + '" aria-label="Убрать из очереди">' + icon('x', 16) + '</button>'
          : '')
        + '</div>',
      // ✕ takes the chevron's place: the row still opens the card on tap.
      chev: !last.can_edit,
    })).join('') + '</div>';
    if (last.has_more) {
      html += '<div style="margin-top:12px"><button class="btn ghost" data-act="more">Показать ещё</button></div>';
    }
    return html;
  },

  actions: {
    kind: (el) => { GW.wins = el.dataset.v === 'wins'; GW.keep(); GW.reset(); return App.render({ quiet: true }); },
    sort: () => { GW.sort = GW.sort === 'd' ? 'p' : 'd'; GW.keep(); GW.reset(); return App.render({ quiet: true }); },
    more: async (el) => {
      el.disabled = true;
      try { await GW.more(); } finally { el.disabled = false; }
      return App.render({ quiet: true });
    },
    // Убрать из очереди = статус «Закрыт»; «Вернуть» — серверный откат по токену.
    dismiss: async (el) => {
      const id = el.dataset.id;
      const repaint = () => (App.current().name === 'giveaways' ? App.render({ quiet: true }) : null);
      GW.drop(id);
      repaint();
      let res;
      try {
        res = await api('/api/app/pings/' + id + '/status', { status: 'closed' }, 'убрать из очереди');
      } catch (err) {
        GW.reset();
        repaint();
        throw err;
      }
      undoToast('Убрано из очереди', res.undo, () => { GW.reset(); return repaint(); });
    },
    open: (el) => App.go('giveaway', { id: el.dataset.id }),
    account: () => {
      const names = (GW.meta && GW.meta.accounts) || [];
      const rows = [item({ act: 'pick-account', data: { i: -1 }, chev: false, cls: GW.account < 0 ? 'selected' : '',
        lead: icon('users', 16), title: 'Все аккаунты' })]
        .concat(names.map((name, i) => item({
          act: 'pick-account', data: { i: i }, chev: false, cls: GW.account === i ? 'selected' : '',
          lead: icon('phone', 16), title: '@' + esc(name),
        })));
      Sheet.open('Аккаунт', '<div class="list">' + rows.join('') + '</div>');
    },
    'pick-account': (el) => {
      GW.account = Number(el.dataset.i);
      GW.keep();
      GW.reset();
      Sheet.close();
      return App.render({ quiet: true });
    },
  },
});

function priorityLabel(label) {
  return { critical: 'критично', high: 'высокий приоритет', normal: 'обычный', low: 'низкий' }[label] || (label || 'обычный');
}

// A win nobody has marked is not "waiting for results" — it is a prize owed.
function cardBadge(g) {
  if (g.is_win && (!g.status || g.status === 'pending')) return ['не забрано', 'warn'];
  const s = GW_STATUS[g.status];
  return s ? [s[0], s[2]] : (g.status ? [g.status, ''] : null);
}

// The owner's card as last drawn: the analysis and channel blocks repaint
// from it after «Разобрать» / «Профиль» without refetching the post.
let GW_CARD = null;

App.register('giveaway', {
  tab: 'giveaways',
  title: 'Карточка',

  async render(params) {
    const g = await api('/api/app/giveaways/' + encodeURIComponent(params.id));
    GW_CARD = g;
    App.sub.textContent = g.chat;
    const badge = cardBadge(g);
    let html = '<div class="panel pad' + (g.is_win ? ' keyed' : '') + '" style="display:flex;gap:12px;align-items:center">'
      + '<div class="lead" style="width:44px;height:44px;border-radius:14px;display:grid;place-items:center;'
      + 'background:' + (g.is_win ? 'var(--accent-soft);color:var(--accent)' : 'var(--card-2);color:var(--dim)') + '">'
      + icon(g.is_win ? 'trophy' : 'gift', 22) + '</div>'
      + '<div style="flex:1;min-width:0"><div style="font-weight:650;font-size:16px">' + (g.is_win ? 'Победа' : 'Розыгрыш') + '</div>'
      + '<div class="dim" style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + esc(g.chat) + ' · ' + esc(fmtTime(g.detected_at)) + '</div></div>'
      + (badge ? '<span class="badge ' + badge[1] + '">' + esc(badge[0]) + '</span>' : '')
      + '</div>';
    if (g.mentions || g.estimated_value) {
      html += '<div class="kv" style="margin-top:10px">'
        + (g.mentions ? '<div class="cell"><div class="k">Упоминание</div><div class="v sm">' + esc(g.mentions) + '</div></div>' : '')
        + (g.estimated_value ? '<div class="cell"><div class="k">Оценка</div><div class="v sm">' + esc(fmtUsd(g.estimated_value)) + '</div></div>' : '')
        + '</div>';
    }
    html += '<div class="section-label">Пост</div><div class="text-block">' + esc(g.text || '—') + '</div>';
    if (g.link) {
      html += '<div style="margin-top:10px"><button class="btn" data-act="link" data-url="' + esc(g.link) + '">'
        + icon('external', 18) + 'Открыть в Telegram</button></div>';
    }
    if (g.can_edit && g.quick_statuses.length) {
      html += '<div class="section-label">Отметить</div><div class="tiles">'
        + g.quick_statuses.map((code) => {
          const s = GW_STATUS[code] || [code, 'check', ''];
          return '<button class="btn' + (code === 'claimed' ? ' primary' : '') + (code === 'scam' ? ' danger' : '')
            + '" data-act="status" data-v="' + code + '" data-id="' + g.id + '">' + icon(s[1], 18) + esc(s[0]) + '</button>';
        }).join('') + '</div>';
    }
    if (g.can_edit) html += '<div id="gw-extra">' + extraBlocks(g) + '</div>';
    return html;
  },

  actions: {
    status: async (el) => {
      const code = el.dataset.v;
      if (code === 'scam' && !(await confirmBox('Отметить как скам?'))) return;
      const word = (GW_STATUS[code] || [code])[0];
      const res = await api('/api/app/pings/' + el.dataset.id + '/status', { status: code }, 'отметка «' + word + '»');
      GW.drop(el.dataset.id);
      undoToast('Сохранено: ' + word, res.undo, () => {
        GW.reset();
        return App.current().name === 'giveaways' ? App.render({ quiet: true }) : null;
      });
      return App.back();
    },
    // Разбор and the channel profile repaint their own block only: the post
    // above stays where the owner was reading it.
    analyze: (el) => extraAction(el, 'analyze', 'Разбираю…', (res) => { GW_CARD.candidate = res.candidate; return 'Разобрано'; }),
    profile: (el) => extraAction(el, 'profile', 'Читаю канал…', (res) => { GW_CARD.channel = res.channel; return 'Профиль обновлён'; }),
    skip: async (el) => {
      const id = el.dataset.id;
      const res = await api('/api/app/giveaways/' + id + '/skip', {}, 'не участвуем');
      GW.drop(id);
      undoToast('Не участвуем', res.undo, () => { GW.reset(); return null; });
      return App.back();
    },
  },
});

async function extraAction(el, kind, busyText, apply) {
  const id = App.current().params.id;
  el.disabled = true;
  const label = el.innerHTML;
  el.innerHTML = icon('refresh', 18) + busyText;
  try {
    const res = await api('/api/app/giveaways/' + id + '/' + kind, {});
    if (!GW_CARD || String(GW_CARD.id) !== String(id)) return;
    const done = apply(res);
    const box = document.getElementById('gw-extra');
    if (box) box.innerHTML = extraBlocks(GW_CARD);
    toast(done);
  } finally {
    if (el.isConnected) { el.disabled = false; el.innerHTML = label; }
  }
}

// Owner-only: what the analysis found, the channel, and the buttons that
// spend Telegram requests (same functions as the bot's gw:an / gw:pr / gw:sk).
function extraBlocks(g) {
  const c = g.candidate;
  const ch = g.channel;
  let html = '<div class="section-label">Разбор</div>';
  if (c) {
    // External requirements usually repeat a reason word for word.
    const warn = [].concat(c.blocked ? [c.blocked] : [], c.external || [])
      .filter((w) => !c.reasons.some((r) => r.indexOf(w) !== -1));
    html += '<div class="panel pad">'
      + '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">'
      + (c.score != null ? '<span class="badge on">score ' + esc(c.score) + '</span>' : '')
      + (c.status ? '<span class="badge">' + esc(c.status) + '</span>' : '')
      + (c.join_buttons ? '<span class="badge">кнопок участия: ' + c.join_buttons + '</span>' : '')
      + (c.analyzed_at ? '<span class="dim num" style="margin-left:auto;font-size:12px">' + esc(fmtTime(c.analyzed_at)) + '</span>' : '')
      + '</div>'
      + (c.reasons.length ? '<div class="dim" style="font-size:13px;margin-top:8px">' + c.reasons.map((r) => '• ' + esc(r)).join('<br>') + '</div>' : '')
      + (c.required_channels.length ? '<div style="font-size:13px;margin-top:8px"><span class="dim">Подписаться:</span> ' + esc(c.required_channels.join(', ')) + '</div>' : '')
      + (warn.length ? '<div style="font-size:13px;margin-top:8px;color:var(--amber)">' + warn.map((r) => '⚠ ' + esc(r)).join('<br>') + '</div>' : '')
      + '</div>';
  } else {
    html += '<div class="hint" style="margin:0 4px 4px">Ещё не разбирали: условия, кнопки участия и оценка появятся после разбора.</div>';
  }
  if (ch) {
    html += '<div class="section-label">Канал</div><div class="panel pad" style="font-size:14px">'
      + '<div style="display:flex;gap:8px;align-items:center">'
      + (ch.username ? '<b>@' + esc(ch.username) + '</b>' : '<span class="dim">без username</span>')
      + '</div>'
      + (ch.wins || ch.giveaways || ch.noise
        ? '<div class="dim num" style="font-size:12.5px;margin-top:6px">побед ' + ch.wins + ' · розыгрышей ' + ch.giveaways
          + (ch.noise ? ' · <span style="color:var(--red)">скам и шум ' + ch.noise + '</span>' : '') + '</div>'
        : '')
      + (ch.description ? '<div class="dim" style="font-size:13px;margin-top:6px;white-space:pre-wrap">' + esc(ch.description) + '</div>' : '')
      + (ch.error ? '<div style="font-size:12.5px;margin-top:6px;color:var(--amber)">' + esc(ch.error) + '</div>' : '')
      + '</div>';
  }
  html += '<div class="btn-row" style="margin-top:10px">'
    + '<button class="btn" data-act="analyze">' + icon('search', 18) + 'Разобрать</button>'
    + '<button class="btn" data-act="profile">' + icon('scan', 18) + 'Профиль</button></div>';
  if (!g.is_win) {
    html += '<div style="margin-top:10px"><button class="btn ghost" data-act="skip" data-id="' + esc(g.id) + '">'
      + icon('x', 18) + 'Не участвуем</button></div>';
  }
  return html;
}
