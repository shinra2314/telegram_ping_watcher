// Salary from the workbook, read-only. The owner sees the month overview and
// can open any account; a key holder lands straight on their own account.
'use strict';

const SAL_STATUS = { paid: ['выплачено', 'good'], pending: ['к выплате', 'warn'], none: ['—', ''] };

App.register('salary', {
  tab: 'more',
  title: 'Зарплата',

  async render(params) {
    const q = [];
    if (params.month) q.push('month=' + encodeURIComponent(params.month));
    if (params.account) q.push('account=' + encodeURIComponent(params.account));
    const data = await api('/api/app/salary' + (q.length ? '?' + q.join('&') : ''));
    let html = monthNav(data);
    if (data.overview) html += overview(data.overview);
    else if (data.account) html += accountView(data.account);
    return html;
  },

  actions: {
    month: (el) => App.replace({ month: el.dataset.v }),
    member: (el) => App.go('salary', { account: el.dataset.name, month: App.current().params.month }),
  },
});

function monthNav(data) {
  const keys = data.months.map((m) => m.key);
  const i = keys.indexOf(data.month);
  const prev = i > 0 ? keys[i - 1] : null;
  const next = i >= 0 && i < keys.length - 1 ? keys[i + 1] : null;
  const btn = (key, name) => '<button class="icon-btn" data-act="month" data-v="' + esc(key || '') + '"'
    + (key ? '' : ' disabled style="opacity:.35"') + '>' + icon(name, 18) + '</button>';
  return '<div class="month-nav">' + btn(prev, 'left') + '<div class="m">' + esc(data.month_label) + '</div>' + btn(next, 'right') + '</div>';
}

function money(v) { return fmtUsd(v || 0); }

function overview(o) {
  let html = '<div class="kv">'
    + '<div class="cell"><div class="k">К выплате</div><div class="v">' + money(o.payout) + '</div></div>'
    + '<div class="cell"><div class="k">Прибыль</div><div class="v ' + (o.profit >= 0 ? 'up' : 'down') + '">' + money(o.profit) + '</div></div>'
    + '<div class="cell"><div class="k">Выплачено</div><div class="v sm">' + money(o.paid) + '</div></div>'
    + '<div class="cell"><div class="k">Ждут</div><div class="v sm">' + money(o.pending) + '</div></div>'
    + '<div class="cell"><div class="k">Выиграно</div><div class="v sm">' + money(o.won) + '</div></div>'
    + '<div class="cell"><div class="k">Побед</div><div class="v sm">' + fmtInt(o.wins) + '</div></div>'
    // «Прибыль» уже за вычетом расходов месяца — без этих двух ячеек она просто
    // выглядела бы меньше, чем должна.
    + (o.expenses ? '<div class="cell"><div class="k">Расходы</div><div class="v sm">' + money(o.expenses) + '</div></div>'
      + '<div class="cell"><div class="k">Удержано с долей</div><div class="v sm">' + money(o.withheld) + '</div></div>' : '')
    + '</div>';
  if (o.issues && o.issues.length) {
    html += '<div class="err-box" style="margin-top:12px;border-color:rgba(255,190,70,.3);background:rgba(255,190,70,.06)">'
      + '<div class="h" style="color:var(--amber)">Книга теряет деньги</div>'
      // Issues are written for the bot's Markdown; the backticks mean nothing here.
      + o.issues.slice(0, 4).map((t) => '<div class="dim" style="font-size:13px">• ' + esc(String(t).replace(/[`*_]/g, '')) + '</div>').join('') + '</div>';
  }
  if (!o.rows.length) return html + emptyView('cash', 'За месяц пусто', 'В книге нет строк за этот месяц.');
  const top = Math.max.apply(null, o.rows.map((r) => r.total || 0)) || 1;
  html += '<div class="section-label">Аккаунты</div><div class="list">'
    + o.rows.map((r, i) => {
      const st = SAL_STATUS[r.status] || SAL_STATUS.none;
      return item({
        act: 'member', data: { name: r.account },
        lead: '<span class="num" style="font-size:13px;font-weight:700">' + (i + 1) + '</span>', leadCls: i === 0 ? 'on' : '',
        title: esc(r.account),
        desc: '<span class="badge ' + st[1] + '">' + st[0] + '</span> <span class="faint">крипта ' + money(r.crypto)
          + ' · скины ' + money(r.skins) + ' · йобо ' + money(r.yobo) + '</span>',
        end: '<div class="t num">' + money(r.total) + '</div><div class="progress" style="width:64px;margin-top:6px"><i style="width:'
          + Math.round(100 * (r.total || 0) / top) + '%"></i></div>',
      });
    }).join('') + '</div>';
  return html;
}

function accountView(a) {
  const row = a.row || {};
  const st = SAL_STATUS[row.status] || SAL_STATUS.none;
  const delta = a.delta || 0;
  let html = '<div class="panel pad keyed"><div class="dim" style="font-size:13px">' + esc(a.account)
    + (a.rank ? ' · место ' + a.rank + ' из ' + a.of : '') + '</div>'
    + '<div style="font-size:34px;font-weight:750;font-variant-numeric:tabular-nums;margin:4px 0">' + money(row.total) + '</div>'
    + '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="badge ' + st[1] + '">' + st[0] + '</span>'
    + '<span class="' + (delta >= 0 ? 'up' : 'down') + ' num" style="font-size:13px">' + (delta >= 0 ? '+' : '−') + money(Math.abs(delta))
    + ' к прошлому месяцу</span>' + (row.paid_at ? '<span class="faint" style="font-size:13px">выплата ' + esc(row.paid_at) + '</span>' : '')
    + '</div></div>'
    + '<div class="kv" style="margin-top:10px">'
    + '<div class="cell"><div class="k">Побед</div><div class="v">' + fmtInt(a.wins) + '</div></div>'
    + '<div class="cell"><div class="k">Выиграно</div><div class="v">' + money(a.won) + '</div></div>'
    + '<div class="cell"><div class="k">Средний приз</div><div class="v sm">' + money(a.avg) + '</div></div>'
    + '<div class="cell"><div class="k">Лучший</div><div class="v sm">' + (a.best ? money(a.best.value) : '—') + '</div></div>'
    + '</div>';
  // Выиграно → начислено по каждому типу: у йобо своя пара столбцов в книге,
  // и в панели он такой же гражданин, как крипта и скины.
  html += '<div class="section-label">Начислено</div><div class="kv">'
    + '<div class="cell"><div class="k">Крипта</div><div class="v sm">' + money(row.crypto) + ' → ' + money(row.pay_money) + '</div></div>'
    + '<div class="cell"><div class="k">Скины</div><div class="v sm">' + money(row.skins) + ' → ' + money(row.pay_skins) + '</div></div>'
    + '<div class="cell"><div class="k">йобо</div><div class="v sm">' + money(row.yobo) + ' → ' + money(row.pay_yobo) + '</div></div>'
    + '<div class="cell"><div class="k">Доля</div><div class="v sm">' + Math.round((row.share || 0) * 100) + '%</div></div>'
    // Расходы месяца вычитаются до дележа, поэтому на долю ложится своя часть.
    // Показываем и расход, и удержание, и саму арифметику: невидимый вычет —
    // это просто другой процент, а он тут написан.
    + (row.expenses ? '<div class="cell"><div class="k">Расходы</div><div class="v sm">' + money(row.expenses) + '</div></div>'
      + '<div class="cell"><div class="k">Удержано с доли</div><div class="v sm">−' + money(row.withheld) + '</div></div>' : '')
    + '</div>'
    // gross_pay — из самой аналитики: asdict() отдаёт поля строки, но не её
    // вычисляемые свойства, так что в row его нет.
    + (row.withheld ? '<div class="faint" style="font-size:12px;margin-top:6px">' + money(a.gross_pay)
      + ' − ' + money(row.withheld) + ' = ' + money(row.total) + '</div>' : '');
  const kinds = Object.keys(a.by_kind || {}).sort((x, y) => a.by_kind[y] - a.by_kind[x]);
  if (kinds.length) {
    const max = a.by_kind[kinds[0]] || 1;
    html += '<div class="section-label">По типам</div><div class="panel pad bars">'
      + kinds.map((k) => '<div class="barline"><div class="top"><span>' + esc(k) + '</span><span class="num">' + money(a.by_kind[k])
        + '</span></div><div class="track"><i style="width:' + Math.round(100 * a.by_kind[k] / max) + '%"></i></div></div>').join('')
      + '</div>';
  }
  if (a.best) {
    html += '<div class="section-label">Лучший приз месяца</div>'
      + '<div class="list">' + item({ lead: icon('trophy', 16), leadCls: 'on', title: esc(a.best.title || a.best.kind),
        desc: esc(a.best.kind + ' · ' + a.best.day), end: '<div class="t num">' + money(a.best.value) + '</div>' }) + '</div>';
  }
  html += '<div class="section-label">За всё время</div><div class="kv">'
    + '<div class="cell"><div class="k">Заработано</div><div class="v sm">' + money(a.all_time) + '</div></div>'
    + '<div class="cell"><div class="k">Ждёт выплаты</div><div class="v sm">' + money(a.all_time_pending) + '</div></div>'
    + '</div>';
  // Month by month: "was August paid?" answered without flipping months.
  const history = a.history || [];
  if (history.length > 1) {
    html += '<div class="section-label">Все месяцы</div><div class="list">'
      + history.map((h) => {
        const st = SAL_STATUS[h.status] || SAL_STATUS.none;
        return item({
          act: 'month', data: { v: h.month }, chev: false,
          lead: icon(h.status === 'paid' ? 'check' : 'clock', 15), leadCls: h.status === 'paid' ? 'on' : (h.status === 'pending' ? 'warn' : ''),
          title: esc(salaryMonthLabel(h.month)),
          desc: '<span class="badge ' + st[1] + '">' + st[0] + '</span>' + (h.paid_at ? ' <span class="dim">' + esc(h.paid_at) + '</span>' : ''),
          end: '<div class="t num">' + money(h.total) + '</div>',
        });
      }).join('') + '</div>';
  }
  return html;
}

const SALARY_MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];

function salaryMonthLabel(key) {
  const parts = String(key || '').split('-');
  const month = SALARY_MONTHS[Number(parts[1]) - 1];
  return month ? month[0].toUpperCase() + month.slice(1) + ' ' + parts[0] : key;
}
