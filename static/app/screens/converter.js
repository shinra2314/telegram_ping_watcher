// Converter + rates. Every figure is computed here from the `usd` table the
// server sends (price of one unit in USD), so typing never waits on a request.
'use strict';

const CURRENCY_NAMES = {
  USD: 'Доллар США', UAH: 'Гривна', EUR: 'Евро', RUB: 'Рубль', PLN: 'Злотый', GBP: 'Фунт',
  TRY: 'Лира', CZK: 'Крона', CHF: 'Франк', CAD: 'Канадский доллар', JPY: 'Иена', CNY: 'Юань',
  BTC: 'Bitcoin', ETH: 'Ethereum', TON: 'Toncoin', SOL: 'Solana', BNB: 'BNB', NOT: 'Notcoin',
  DOGS: 'DOGS', USDT: 'Tether',
};
const QUICK_AMOUNTS = ['1', '10', '100', '1000'];

const Conv = {
  rates: null,
  loadedAt: 0,
  from: 'USD',
  to: 'UAH',
  amount: '100',
  edited: 'from',
  quote: 'USD',
  favorites: [],
  recent: [],
  restored: false,

  isFiat(code) { return Boolean(this.rates && this.rates.signs && this.rates.signs[code]); },
  flag(code) { return (this.rates && this.rates.flags && this.rates.flags[code]) || '•'; },
  usd(code) { return this.rates && this.rates.usd ? this.rates.usd[code] : null; },

  convert(amount, src, dst) {
    const a = this.usd(src);
    const b = this.usd(dst);
    if (!a || !b || !isFinite(amount)) return null;
    return amount * a / b;
  },
  money(value, code) {
    if (value == null) return '—';
    const sign = this.rates.signs[code];
    return sign ? sign + fmtAmount(value, true) : fmtAmount(value, false) + ' ' + code;
  },
  pairKey() { return this.from + '-' + this.to; },

  async ensureRates(force) {
    if (!force && this.rates && Date.now() - this.loadedAt < 60000) return;
    this.rates = await api('/api/app/market');
    this.loadedAt = Date.now();
  },
  async restore() {
    if (this.restored) return;
    this.restored = true;
    const saved = await Store.json('pd.conv', null);
    if (saved) {
      this.from = saved.from || this.from;
      this.to = saved.to || this.to;
      this.amount = saved.amount || this.amount;
      this.quote = saved.quote || this.quote;
    }
    this.favorites = await Store.json('pd.fav', ['USD-UAH', 'USDT-UAH', 'BTC-USD', 'TON-UAH']);
    this.recent = await Store.json('pd.recent', []);
  },
  save() {
    Store.set('pd.conv', JSON.stringify({ from: this.from, to: this.to, amount: this.amount, quote: this.quote }));
  },
  remember(code) {
    this.recent = [code].concat(this.recent.filter((c) => c !== code)).slice(0, 6);
    Store.set('pd.recent', JSON.stringify(this.recent));
  },
};

function parseAmount(raw) {
  const text = String(raw || '').replace(/[\s ]/g, '').replace(',', '.');
  if (!text) return NaN;
  return Number(text);
}

App.register('converter', {
  tab: 'converter',
  title: 'Конвертер',

  async render(params) {
    await Promise.all([Conv.restore(), Conv.ensureRates(params.force)]);
    const r = Conv.rates;
    if (!r.usd || !Object.keys(r.usd).length) {
      return emptyView('coins', 'Курсы ещё не собраны', 'Фоновая задача рынка заполнит их после первого опроса.');
    }
    if (!Conv.usd(Conv.from)) Conv.from = 'USD';
    if (!Conv.usd(Conv.to)) Conv.to = 'UAH';

    const html = '<div class="conv">'
      + leg('from') + leg('to')
      + '<button class="swap-btn" data-act="swap" aria-label="Поменять местами">' + icon('swap', 20) + '</button>'
      + '</div>'
      + '<div class="rate-line" id="rate-line"></div>'
      + '<div class="chips">' + QUICK_AMOUNTS.map((a) =>
        '<button class="chip" data-act="quick" data-v="' + a + '">' + group(a) + ' ' + esc(Conv.from) + '</button>').join('')
      + '</div>'
      + '<div class="ask"><input class="input" id="ask" placeholder="Например: 250 евро в грн" data-enter="ask" enterkeyhint="go" autocomplete="off">'
      + '<button class="icon-btn" data-act="ask" aria-label="Посчитать">' + icon('send', 18) + '</button></div>'
      + favoritesBlock()
      + ratesBlock();
    return html;
  },

  mounted() {
    recompute();
  },

  inputs: {
    amount: (el) => {
      Conv.edited = el.dataset.side;
      const value = el.value;
      if (Conv.edited === 'from') Conv.amount = value;
      recompute(true);
    },
  },

  actions: {
    swap: () => {
      const other = document.querySelector('[data-side="to"]');
      Conv.amount = other ? other.value.replace(/[\s ]/g, '') : Conv.amount;
      const f = Conv.from; Conv.from = Conv.to; Conv.to = f;
      Conv.edited = 'from';
      Conv.save();
      haptic('select');
      return App.render({ quiet: true });
    },
    quick: (el) => {
      Conv.amount = el.dataset.v;
      Conv.edited = 'from';
      const input = document.querySelector('[data-side="from"]');
      if (input) input.value = el.dataset.v;
      recompute(true);
    },
    pick: (el) => pickCurrency(el.dataset.side),
    'pick-code': (el) => {
      const side = el.dataset.side;
      const code = el.dataset.code;
      if (side === 'from' && code === Conv.to) Conv.to = Conv.from;
      if (side === 'to' && code === Conv.from) Conv.from = Conv.to;
      Conv[side] = code;
      Conv.remember(code);
      Conv.edited = 'from';
      Conv.save();
      Sheet.close();
      return App.render({ quiet: true });
    },
    fav: () => {
      const key = Conv.pairKey();
      const has = Conv.favorites.indexOf(key) !== -1;
      Conv.favorites = has ? Conv.favorites.filter((k) => k !== key) : [key].concat(Conv.favorites).slice(0, 8);
      Store.set('pd.fav', JSON.stringify(Conv.favorites));
      toast(has ? 'Убрано из избранного' : 'Пара в избранном');
      return App.render({ quiet: true });
    },
    pair: (el) => {
      const parts = el.dataset.v.split('-');
      Conv.from = parts[0];
      Conv.to = parts[1];
      Conv.edited = 'from';
      Conv.save();
      haptic('select');
      return App.render({ quiet: true });
    },
    'copy-result': () => {
      const to = document.querySelector('[data-side="to"]');
      const from = document.querySelector('[data-side="from"]');
      const target = Conv.edited === 'from' ? to : from;
      if (target && target.value) copyText(target.value.replace(/[\s ]/g, ''));
    },
    ask: async () => {
      const input = document.getElementById('ask');
      const q = (input && input.value || '').trim();
      if (!q) { input && input.focus(); return; }
      const res = await api('/api/app/convert?q=' + encodeURIComponent(q));
      Conv.from = res.src;
      Conv.to = res.dst;
      Conv.amount = String(res.amount);
      Conv.edited = 'from';
      Conv.save();
      input.blur();
      await App.render({ quiet: true });
      haptic('ok');
    },
    quote: (el) => {
      Conv.quote = el.dataset.v;
      Conv.save();
      return App.render({ quiet: true });
    },
    'rate-row': (el) => {
      Conv.from = el.dataset.code;
      Conv.to = el.dataset.code === Conv.quote ? (Conv.quote === 'USD' ? 'UAH' : 'USD') : Conv.quote;
      Conv.amount = '1';
      Conv.edited = 'from';
      Conv.save();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return App.render({ quiet: true });
    },
  },
});

function leg(side) {
  const code = Conv[side];
  return '<div class="leg" id="leg-' + side + '">'
    + '<input class="amount" data-act="amount" data-side="' + side + '" inputmode="decimal" autocomplete="off"'
    + ' placeholder="0" value="' + (side === 'from' ? esc(Conv.amount) : '') + '" aria-label="Сумма">'
    + '<div class="side"><button class="cur" data-act="pick" data-side="' + side + '">'
    + '<span class="flag">' + Conv.flag(code) + '</span>' + esc(code) + '<span class="chev">' + icon('down', 14) + '</span></button>'
    + '<span class="usd" id="usd-' + side + '"></span></div></div>';
}

// Update the non-edited field, the USD hints and the rate line in place —
// re-rendering would steal focus from the field being typed into.
function recompute(typing) {
  const fromEl = document.querySelector('[data-side="from"]');
  const toEl = document.querySelector('[data-side="to"]');
  if (!fromEl || !toEl) return;
  const src = Conv.edited === 'from' ? fromEl : toEl;
  const dst = Conv.edited === 'from' ? toEl : fromEl;
  const amount = parseAmount(src.value);
  const value = isNaN(amount) ? null
    : Conv.convert(amount, Conv.edited === 'from' ? Conv.from : Conv.to, Conv.edited === 'from' ? Conv.to : Conv.from);
  const dstCode = Conv.edited === 'from' ? Conv.to : Conv.from;
  dst.value = value == null ? '' : fmtAmount(value, Conv.isFiat(dstCode)).replace(/ /g, ' ');
  if (Conv.edited === 'to') Conv.amount = fromEl.value.replace(/\s/g, '');
  ['from', 'to'].forEach((side) => {
    const el = document.getElementById('usd-' + side);
    const input = side === 'from' ? fromEl : toEl;
    const n = parseAmount(input.value);
    const code = Conv[side];
    el.textContent = code === 'USD' || isNaN(n) ? (Conv.names(code)) : '≈ ' + fmtUsd(n * Conv.usd(code));
  });
  const one = Conv.convert(1, Conv.from, Conv.to);
  const fav = Conv.favorites.indexOf(Conv.pairKey()) !== -1;
  document.getElementById('rate-line').innerHTML = '<span data-act="copy-result">1 ' + esc(Conv.from) + ' = '
    + esc(Conv.money(one, Conv.to)) + '</span><span class="faint">· ' + esc(Conv.rates.updated) + '</span>'
    + '<span class="star' + (fav ? ' on' : '') + '" data-act="fav" aria-label="В избранное">' + icon('star', 18) + '</span>';
  if (typing) Conv.save();
}
Conv.names = (code) => CURRENCY_NAMES[code] || code;

function favoritesBlock() {
  const pairs = Conv.favorites.filter((k) => { const p = k.split('-'); return Conv.usd(p[0]) && Conv.usd(p[1]); });
  if (!pairs.length) return '';
  return '<div class="section-label">Избранные пары</div><div class="chips" style="margin-top:0">'
    + pairs.map((k) => {
      const p = k.split('-');
      const rate = Conv.convert(1, p[0], p[1]);
      return '<button class="chip' + (k === Conv.pairKey() ? ' on' : '') + '" data-act="pair" data-v="' + esc(k) + '">'
        + esc(p[0]) + '→' + esc(p[1]) + ' <span class="faint num">' + esc(Conv.money(rate, p[1])) + '</span></button>';
    }).join('') + '</div>';
}

function ratesBlock() {
  const r = Conv.rates;
  const quote = Conv.usd(Conv.quote) ? Conv.quote : 'USD';
  let html = '<div class="section-label">Курсы <span class="faint" style="letter-spacing:0;text-transform:none;font-weight:500">'
    + esc(r.updated) + '</span></div>'
    + seg([['USD', 'в долларах'], ['UAH', 'в гривнах'], ['EUR', 'в евро']], quote, 'quote')
    + '<div class="list" style="margin-top:10px">';
  html += r.coins.map((c) => {
    const change = c.change == null ? '' : '<div class="d ' + (c.change >= 0 ? 'up' : 'down') + '">'
      + (c.change >= 0 ? '+' : '') + c.change.toFixed(2) + '%</div>';
    return item({
      act: 'rate-row', data: { code: c.code }, chev: false,
      lead: '<span style="font-size:17px">' + c.emoji + '</span>',
      title: esc(c.code), desc: esc(Conv.names(c.code)),
      end: '<div class="t num">' + esc(Conv.money(Conv.convert(1, c.code, quote), quote)) + '</div>' + change,
    });
  }).join('');
  html += '</div><div class="list" style="margin-top:10px">';
  html += r.fiat.filter((f) => f.code !== quote).map((f) => item({
    act: 'rate-row', data: { code: f.code }, chev: false,
    lead: '<span style="font-size:17px">' + f.flag + '</span>',
    title: esc(f.code), desc: esc(Conv.names(f.code)),
    end: '<div class="t num">' + esc(Conv.money(Conv.convert(1, f.code, quote), quote)) + '</div>',
  })).join('');
  return html + '</div>';
}

function pickCurrency(side) {
  const r = Conv.rates;
  const codes = Object.keys(r.usd);
  const crypto = r.coins.map((c) => c.code);
  const fiat = codes.filter((c) => crypto.indexOf(c) === -1);
  const row = (code) => item({
    act: 'pick-code', data: { code: code, side: side }, chev: false,
    cls: code === Conv[side] ? 'selected' : '',
    lead: '<span style="font-size:17px">' + Conv.flag(code) + '</span>',
    title: esc(code), desc: esc(Conv.names(code)),
    end: code === 'USD' ? '' : '<div class="d num">' + esc(fmtUsd(r.usd[code])) + '</div>',
  });
  const recent = Conv.recent.filter((c) => r.usd[c]);
  const body = '<input class="input" id="cur-search" placeholder="Поиск: btc, гривна, €…" autocomplete="off" style="margin-bottom:12px">'
    + '<div id="cur-list">'
    + (recent.length ? '<div class="section-label" style="margin-top:4px">Недавние</div><div class="list">' + recent.map(row).join('') + '</div>' : '')
    + '<div class="section-label">Валюты</div><div class="list">' + fiat.map(row).join('') + '</div>'
    + '<div class="section-label">Крипта</div><div class="list">' + crypto.map(row).join('') + '</div>'
    + '</div>';
  Sheet.open(side === 'from' ? 'Из валюты' : 'В валюту', body, (sheet) => {
    const input = sheet.querySelector('#cur-search');
    const list = sheet.querySelector('#cur-list');
    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      if (!q) { list.querySelectorAll('.item,.section-label,.list').forEach((n) => { n.style.display = ''; }); return; }
      list.querySelectorAll('.section-label').forEach((n) => { n.style.display = 'none'; });
      const seen = {};
      list.querySelectorAll('.item').forEach((n) => {
        const code = n.dataset.code;
        const hay = (code + ' ' + Conv.names(code) + ' ' + (r.signs[code] || '')).toLowerCase();
        const show = hay.indexOf(q) !== -1 && !seen[code];
        if (show) seen[code] = true;
        n.style.display = show ? '' : 'none';
      });
    });
  });
}
