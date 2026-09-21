// «Мои победы»: every win of the key's accounts over 30 or 90 days and how
// it ended — the queue drops a win once it is claimed, this list keeps it.
'use strict';

const Wins = { days: 30, rows: null, meta: null, next: null };

const WIN_OUTCOME = {
  claimed: ['забрано', 'good'],
  open: ['не забрано', 'warn'],
  missed: ['пропущено', ''],
  scam: ['скам', 'bad'],
  closed: ['закрыто', ''],
};

App.register('wins', {
  tab: 'home',
  title: 'Мои победы',
  reset: () => { Wins.rows = null; },

  async render(params) {
    if (params.force || !Wins.rows) {
      const data = await api('/api/app/wins?days=' + Wins.days);
      Wins.rows = data.items;
      Wins.meta = data;
      Wins.next = data.next;
    }
    const m = Wins.meta;
    const sum = m.summary || { wins: 0, claimed: 0 };
    const value = Wins.rows.reduce((total, r) => total + (r.value || 0), 0);
    App.sub.textContent = m.scope.length ? m.scope.map((a) => '@' + a).join(', ') : 'все аккаунты';
    let html = seg([['30', '30 дней'], ['90', '90 дней']], String(Wins.days), 'days')
      + '<div class="kv" style="margin-top:12px">'
      + '<div class="cell"><div class="k">Побед</div><div class="v">' + fmtInt(sum.wins) + '</div></div>'
      + '<div class="cell"><div class="k">Забрано</div><div class="v">' + fmtInt(sum.claimed)
      + (sum.wins ? '<small class="of">' + Math.round(100 * sum.claimed / sum.wins) + '%</small>' : '') + '</div></div>'
      + '</div>';
    if (value) {
      html += '<div class="hint" style="margin:8px 4px 0">Призы в показанных строках — около ' + esc(fmtUsd(value))
        + ' по тексту постов (скины и боты без оценки).</div>';
    }
    if (!Wins.rows.length) {
      return html + emptyView('trophy', 'Побед пока нет', 'За ' + Wins.days + ' дней победы ваших аккаунтов не найдены.');
    }
    html += '<div class="list" style="margin-top:12px">' + Wins.rows.map((r) => {
      const out = WIN_OUTCOME[r.outcome] || [r.outcome, ''];
      return item({
        act: App.sections.giveaways ? 'open' : '', data: { id: r.id },
        lead: icon('trophy', 16), leadCls: r.outcome === 'open' ? 'on' : '',
        title: esc(r.chat),
        desc: '<span class="badge ' + out[1] + '">' + out[0] + '</span>'
          + (r.accounts.length ? ' <span class="dim">' + esc(r.accounts.map((a) => '@' + a).join(', ')) + '</span>' : ''),
        end: '<div class="t num">' + (r.value ? esc(fmtUsd(r.value)) : '') + '</div>'
          + '<div class="d num">' + esc(fmtTime(r.detected_at)) + '</div>',
        chev: false,
      });
    }).join('') + '</div>';
    if (Wins.next) html += '<div style="margin-top:12px"><button class="btn ghost" data-act="more">Показать ещё</button></div>';
    return html;
  },

  actions: {
    days: (el) => { Wins.days = Number(el.dataset.v); Wins.rows = null; return App.render({ quiet: true }); },
    more: async (el) => {
      el.disabled = true;
      try {
        const data = await api('/api/app/wins?days=' + Wins.days + '&after=' + Wins.next);
        const have = new Set(Wins.rows.map((r) => r.id));
        Wins.rows = Wins.rows.concat(data.items.filter((r) => !have.has(r.id)));
        Wins.next = data.next;
      } finally { el.disabled = false; }
      return App.render({ quiet: true });
    },
    open: (el) => App.go('giveaway', { id: el.dataset.id }),
  },
});
