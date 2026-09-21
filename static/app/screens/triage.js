// «Разбор»: everything waiting on the owner, one card at a time. The server
// orders the queue (hot wins, then giveaways worth entering, then account
// problems); every button goes through the ordinary endpoints, so the queue
// has no status logic of its own. «Потом» only moves a card to the back of
// this session's pile — nothing is written.
'use strict';

const Triage = {
  items: null,
  later: [],
  last: null,          // { item, token } — what «Вернуть» puts back
  total: 0,
  tally: { done: 0, claimed: 0, usd: 0 },
  busy: false,
  reset() { this.items = null; },
  // One decision at a time: a double tap must not settle the next card too.
  async once(fn) {
    if (this.busy) return;
    this.busy = true;
    try { await fn(); } finally { this.busy = false; }
  },
  async load() {
    const data = await api('/api/app/triage');
    this.items = data.items || [];
    this.later = [];
    this.last = null;
    this.total = this.items.length;
    this.tally = { done: 0, claimed: 0, usd: 0 };
  },
  // The card on screen leaves the queue; `token` makes it undoable.
  settle(item, token, claimed) {
    this.items.shift();
    this.tally.done += 1;
    if (claimed) {
      this.tally.claimed += 1;
      this.tally.usd += Number(item.value) || 0;
    }
    this.last = { item: item, token: token, claimed: claimed };
  },
  restore() {
    const last = this.last;
    if (!last) return;
    this.items.unshift(last.item);
    this.tally.done = Math.max(0, this.tally.done - 1);
    if (last.claimed) {
      this.tally.claimed = Math.max(0, this.tally.claimed - 1);
      this.tally.usd = Math.max(0, this.tally.usd - (Number(last.item.value) || 0));
    }
    this.last = null;
  },
};

const TRIAGE_KIND = {
  win: ['trophy', 'on', 'Победа'],
  giveaway: ['gift', '', 'Розыгрыш'],
  account: ['alert', 'warn', 'Аккаунт'],
};

App.register('triage', {
  tab: 'home',
  title: 'Разбор',
  reset: () => Triage.reset(),

  async render(params) {
    if (params.force || !Triage.items) await Triage.load();
    const left = Triage.items.length;
    App.sub.textContent = left ? 'осталось ' + left : 'готово';
    if (!left) return triageSummary();
    const done = Triage.total - left - Triage.later.length;
    const share = Triage.total ? Math.round(100 * Math.max(0, done) / Triage.total) : 0;
    return '<div class="tri-progress"><div class="progress"><i style="width:' + share + '%"></i></div>'
      + '<span class="num">' + Math.max(0, done) + ' / ' + Triage.total + '</span></div>'
      + triageCard(Triage.items[0]) + '<div style="height:120px"></div>';
  },

  // The decision buttons sit in a dock above the tab bar: a long post must
  // not push «Забрал» below the fold.
  mounted() {
    const old = document.querySelector('.action-dock');
    if (old) old.remove();
    if (!Triage.items || !Triage.items.length) return null;
    const dock = document.createElement('div');
    dock.className = 'action-dock tri';
    dock.innerHTML = triageActions(Triage.items[0]);
    document.body.appendChild(dock);
    return () => dock.remove();
  },

  actions: {
    more: (el) => {
      const text = document.querySelector('.tri-card .text-block');
      if (text) text.classList.remove('clamp');
      el.remove();
    },
    claim: (el) => Triage.once(() => settleStatus(el.dataset.v)),
    join: () => Triage.once(() => settleStatus('pending')),
    skip: () => Triage.once(async () => {
      const it = Triage.items[0];
      const res = await api('/api/app/giveaways/' + it.id + '/skip', {}, 'не участвую');
      Triage.settle(it, res.undo, false);
      afterSettle('Не участвуем');
    }),
    reconnect: () => Triage.once(async () => {
      const it = Triage.items[0];
      const res = await api('/api/app/accounts/' + encodeURIComponent(it.session_name) + '/reconnect', {});
      Triage.settle(it, null, false);
      toast(res.message || 'Подключаю…');
      await App.render({ quiet: true });
    }),
    accounts: () => App.go('accounts'),
    later: () => {
      Triage.later.push(Triage.items.shift());
      haptic('select');
      return App.render({ quiet: true });
    },
    'go-later': () => {
      Triage.items = Triage.later;
      Triage.later = [];
      return App.render({ quiet: true });
    },
    reload: () => { Triage.reset(); return App.render(); },
    home: () => App.tab('home'),
  },
});

const TRIAGE_WORDS = { claimed: 'Забрал', missed: 'Пропустил', scam: 'Скам', pending: 'Участвую — ждём итогов' };

async function settleStatus(code) {
  const it = Triage.items[0];
  if (code === 'scam' && !(await confirmBox('Отметить как скам?'))) return;
  const word = TRIAGE_WORDS[code] || code;
  const res = await api('/api/app/pings/' + it.id + '/status', { status: code }, 'отметка «' + word + '»');
  Triage.settle(it, res.undo, code === 'claimed');
  GW.drop(it.id);
  afterSettle(word);
}

function afterSettle(word) {
  haptic('ok');
  App.render({ quiet: true });
  const token = Triage.last && Triage.last.token;
  undoToast(word, token, () => {
    Triage.restore();
    GW.reset();
    return App.current().name === 'triage' ? App.render({ quiet: true }) : null;
  });
}

function triageCard(it) {
  const kind = TRIAGE_KIND[it.kind] || ['list', '', it.kind];
  if (it.kind === 'account') {
    return '<div class="panel pad tri-card">'
      + '<div class="tri-kind"><span class="badge warn">' + icon(kind[0], 12) + kind[2] + '</span></div>'
      + '<div class="tri-title">' + esc(it.session_name) + (it.username ? ' <span class="dim">@' + esc(it.username) + '</span>' : '') + '</div>'
      + '<div class="tri-meta">' + esc(it.problem) + '</div>'
      + (it.error ? '<div class="text-block" style="margin-top:12px">' + esc(it.error) + '</div>' : '')
      + '</div>';
  }
  const facts = [];
  if (it.accounts && it.accounts.length) facts.push(icon('at', 13) + esc(it.accounts.map((a) => '@' + a).join(', ')));
  if (it.kind === 'win') {
    facts.push(icon('cash', 13) + (it.value ? esc(fmtUsd(it.value)) : 'без оценки'));
  } else if (it.score != null) {
    facts.push(icon('chart', 13) + 'score ' + esc(it.score));
  }
  const hot = it.kind === 'win' && it.score >= 90;
  const long = (it.text || '').length > 380;
  return '<div class="panel pad tri-card' + (it.kind === 'win' ? ' keyed' : '') + '">'
    + '<div class="tri-kind"><span class="badge ' + kind[1] + '">' + icon(kind[0], 12) + kind[2] + '</span>'
    + (hot ? '<span class="badge bad">' + icon('flame', 12) + 'горячая</span>' : '')
    + '<span class="dim num" style="margin-left:auto;font-size:12.5px">' + esc(fmtTime(it.detected_at)) + '</span></div>'
    + '<div class="tri-title">' + esc(it.chat) + '</div>'
    + (facts.length ? '<div class="tri-facts">' + facts.map((f) => '<span>' + f + '</span>').join('') + '</div>' : '')
    + '<div class="text-block' + (long ? ' clamp' : '') + '">' + esc(it.text || '—') + '</div>'
    + (long ? '<button class="link" data-act="more">Показать целиком</button>' : '')
    + (it.link ? '<div style="margin-top:12px"><button class="btn" data-act="link" data-url="' + esc(it.link) + '">'
      + icon('external', 18) + 'Открыть пост</button></div>' : '')
    + '</div>';
}

function triageActions(it) {
  const later = '<button class="btn ghost" data-act="later">' + icon('clock', 17) + 'Потом</button>';
  if (it.kind === 'win') {
    return '<button class="btn primary" data-act="claim" data-v="claimed">' + icon('check', 18) + 'Забрал</button>'
      + '<div class="btn-row">'
      + '<button class="btn" data-act="claim" data-v="missed">' + icon('x', 17) + 'Пропустил</button>'
      + '<button class="btn danger" data-act="claim" data-v="scam">' + icon('shield', 17) + 'Скам</button>'
      + later + '</div>';
  }
  if (it.kind === 'giveaway') {
    return '<button class="btn primary" data-act="join">' + icon('check', 18) + 'Участвую</button>'
      + '<div class="btn-row"><button class="btn" data-act="skip">' + icon('x', 17) + 'Не участвую</button>' + later + '</div>';
  }
  return '<button class="btn primary" data-act="reconnect">' + icon('plug', 18) + 'Переподключить</button>'
    + '<div class="btn-row"><button class="btn" data-act="accounts">' + icon('users', 17) + 'К аккаунтам</button>' + later + '</div>';
}

function triageSummary() {
  const t = Triage.tally;
  const rows = [
    ['Разобрано', fmtInt(t.done)],
    ['Забрано', fmtInt(t.claimed) + (t.usd ? ' · ' + fmtUsd(t.usd) : '')],
  ];
  if (Triage.later.length) rows.push(['Отложено', fmtInt(Triage.later.length)]);
  return '<div style="text-align:center;margin-top:18px">'
    + '<div class="big-ok">' + icon('check', 36) + '</div>'
    + '<h2 class="title" style="margin-bottom:4px">' + (Triage.total ? 'Очередь разобрана' : 'Разбирать нечего') + '</h2>'
    + '<p class="dim" style="margin:0 0 16px">' + (Triage.total ? 'Всё, что ждало решения, решено или отложено.' : 'Победы забраны, аккаунты в порядке.') + '</p></div>'
    + (Triage.total ? '<div class="kv">' + rows.map((r) => '<div class="cell"><div class="k">' + r[0] + '</div><div class="v sm">' + r[1] + '</div></div>').join('') + '</div>' : '')
    + '<div class="stack" style="margin-top:16px">'
    + (Triage.later.length ? '<button class="btn primary" data-act="go-later">' + icon('clock', 18) + 'Пройти отложенные · ' + Triage.later.length + '</button>' : '')
    + '<button class="btn" data-act="reload">' + icon('refresh', 18) + 'Собрать очередь заново</button>'
    + '<button class="btn ghost" data-act="home">' + icon('home', 18) + 'На главную</button></div>';
}
