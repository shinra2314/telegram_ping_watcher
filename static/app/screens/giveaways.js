// Giveaways and wins: the "needs action" queue, filtered like the bot's gw:f:
// callback, plus a card with the post, its link and the owner's quick statuses.
'use strict';

const GW = { sort: 'd', wins: false, account: -1, pages: 1 };

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

  async render() {
    const query = '/api/app/giveaways?sort=' + GW.sort + '&wins=' + GW.wins + '&account=' + GW.account;
    // "Show more" keeps earlier pages on screen, so a re-render refetches them all.
    const pages = await Promise.all(Array.from({ length: GW.pages }, (_, i) => api(query + '&page=' + (i + 1))));
    const last = pages[pages.length - 1];
    const rows = [].concat.apply([], pages.map((p) => p.items));
    App.sub.textContent = last.total + ' ' + plural(last.total, 'в очереди', 'в очереди', 'в очереди');
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
    kind: (el) => { GW.wins = el.dataset.v === 'wins'; GW.pages = 1; return App.render({ quiet: true }); },
    sort: () => { GW.sort = GW.sort === 'd' ? 'p' : 'd'; GW.pages = 1; return App.render({ quiet: true }); },
    more: () => { GW.pages += 1; return App.render({ quiet: true }); },
    // Убрать из очереди = статус «Закрыт»; «Вернуть» кладёт прежние значения обратно.
    dismiss: async (el) => {
      const path = '/api/app/pings/' + el.dataset.id + '/status';
      const prev = { status: el.dataset.status || '', action: el.dataset.action || 'new' };
      const row = el.closest('.item');
      if (row) row.remove();
      const refresh = () => (App.current().name === 'giveaways' ? App.render({ quiet: true }) : null);
      try {
        await api(path, { status: 'closed' });
      } finally {
        refresh();
      }
      toast('Убрано из очереди', false, {
        label: 'Вернуть',
        run: async () => { await api(path, prev); toast('Вернули в очередь'); return refresh(); },
      });
    },
    open: (el) => App.go('giveaway', { id: el.dataset.id }),
    account: async () => {
      const data = await api('/api/app/giveaways?sort=' + GW.sort + '&page=1');
      const rows = [item({ act: 'pick-account', data: { i: -1 }, chev: false, cls: GW.account < 0 ? 'selected' : '',
        lead: icon('users', 16), title: 'Все аккаунты' })]
        .concat(data.accounts.map((name, i) => item({
          act: 'pick-account', data: { i: i }, chev: false, cls: GW.account === i ? 'selected' : '',
          lead: icon('phone', 16), title: '@' + esc(name),
        })));
      Sheet.open('Аккаунт', '<div class="list">' + rows.join('') + '</div>');
    },
    'pick-account': (el) => {
      GW.account = Number(el.dataset.i);
      GW.pages = 1;
      Sheet.close();
      return App.render({ quiet: true });
    },
  },
});

function priorityLabel(label) {
  return { critical: 'критично', high: 'высокий приоритет', normal: 'обычный', low: 'низкий' }[label] || (label || 'обычный');
}

App.register('giveaway', {
  tab: 'giveaways',
  title: 'Карточка',

  async render(params) {
    const g = await api('/api/app/giveaways/' + encodeURIComponent(params.id));
    App.sub.textContent = g.chat;
    let html = '<div class="panel pad' + (g.is_win ? ' keyed' : '') + '" style="display:flex;gap:12px;align-items:center">'
      + '<div class="lead" style="width:44px;height:44px;border-radius:14px;display:grid;place-items:center;'
      + 'background:' + (g.is_win ? 'var(--accent-soft);color:var(--accent)' : 'var(--card-2);color:var(--dim)') + '">'
      + icon(g.is_win ? 'trophy' : 'gift', 22) + '</div>'
      + '<div style="flex:1;min-width:0"><div style="font-weight:650;font-size:16px">' + (g.is_win ? 'Победа' : 'Розыгрыш') + '</div>'
      + '<div class="dim" style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + esc(g.chat) + ' · ' + esc(fmtTime(g.detected_at)) + '</div></div>'
      + (g.status ? '<span class="badge ' + ((GW_STATUS[g.status] || [])[2] || '') + '">'
        + esc((GW_STATUS[g.status] || [g.status])[0]) + '</span>' : '')
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
    return html;
  },

  actions: {
    status: async (el) => {
      const code = el.dataset.v;
      if (code === 'scam' && !(await confirmBox('Отметить как скам?'))) return;
      await api('/api/app/pings/' + el.dataset.id + '/status', { status: code });
      toast('Сохранено: ' + (GW_STATUS[code] || [code])[0]);
      return App.back();
    },
  },
});
