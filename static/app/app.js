// Mini App screens. Classic script, one global scope, no build step — the same
// convention static/js/app-*.js follows. `icon()` comes from icons.js, which
// index.html loads first.
const tg = window.Telegram ? window.Telegram.WebApp : null;
const screenEl = document.getElementById('screen');
const barEl = document.getElementById('bar');
const subEl = document.getElementById('sub');

let view = 'home';
let feed = { sort: 'd', wins: false, account: -1, page: 1 };
let sections = { giveaways: true, market: true };

function tap() {
  if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
}

// Every call carries initData; the server verifies it each time, so there is no
// session to expire under a panel left open on a phone.
async function api(path) {
  const res = await fetch(path, {
    headers: { 'X-Telegram-Init-Data': (tg && tg.initData) || '' },
  });
  if (!res.ok) {
    let detail = 'Ошибка ' + res.status;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep default */ }
    throw new Error(detail);
  }
  return res.json();
}

function esc(text) {
  return String(text == null ? '' : text).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
  });
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(String(iso).replace(' ', 'T'));
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function fail(message) {
  screenEl.innerHTML = '<div class="err">' + esc(message) + '</div>';
}

function renderBar() {
  const tabs = [['home', 'menu', 'Меню']];
  if (sections.giveaways) tabs.push(['giveaways', 'gift', 'Розыгрыши']);
  if (sections.market) tabs.push(['market', 'coins', 'Курсы']);
  const active = view === 'card' ? 'giveaways' : (view === 'convert' ? 'market' : view);
  barEl.innerHTML = tabs.map(function (t) {
    return '<div class="pill ' + (active === t[0] ? 'on' : '') + '" data-go="' + t[0] + '">'
      + icon(t[1], 19) + '<span>' + t[2] + '</span></div>';
  }).join('');
}

function card(wide, name, label, count, go) {
  return '<div class="card ' + (wide ? 'wide' : '') + '" data-go="' + go + '">'
    + icon(name, wide ? 24 : 26)
    + '<span class="label">' + label + '</span>'
    + (count === '' ? '' : '<span class="count">' + count + '</span>')
    + '</div>';
}

function chip(label, on, action) {
  return '<div class="chip ' + (on ? 'on' : '') + '" data-act="' + action + '">' + label + '</div>';
}

async function showHome() {
  view = 'home';
  const home = await api('/api/app/home');
  sections = home.sections;
  subEl.textContent = home.name || '';
  // One wide hero, then pairs. An odd tail would leave a hole in the two-column
  // grid, so the last narrow card widens to close the row.
  const hero = [];
  const pairs = [];
  if (sections.giveaways) {
    hero.push(['trophy', 'Победы', home.counters.wins, 'wins']);
    pairs.push(['gift', 'Розыгрыши', home.counters.giveaways, 'giveaways']);
  }
  if (sections.market) {
    pairs.push(['coins', 'Курсы', '', 'market']);
    pairs.push(['swap', 'Конвертер', '', 'convert']);
  }
  if (!hero.length && pairs.length) hero.push(pairs.shift());
  const cards = hero.map(function (c) { return card(true, c[0], c[1], c[2], c[3]); })
    .concat(pairs.map(function (c, i) {
      return card(pairs.length % 2 === 1 && i === pairs.length - 1, c[0], c[1], c[2], c[3]);
    }));
  screenEl.innerHTML = cards.length
    ? '<div class="grid">' + cards.join('') + '</div>'
    : '<div class="note">Ваш ключ пока не открывает ни одного раздела.</div>';
  renderBar();
}

async function showGiveaways() {
  view = 'giveaways';
  const data = await api('/api/app/giveaways?sort=' + feed.sort + '&wins=' + feed.wins
    + '&account=' + feed.account + '&page=' + feed.page);
  subEl.textContent = data.total + ' в очереди';
  const filters = chip('Все', !feed.wins, 'f-all')
    + chip('Победы', feed.wins, 'f-wins')
    + chip(feed.sort === 'd' ? 'Обнаружено' : 'Дата поста', false, 'f-sort');
  const rows = data.items.map(function (it) {
    return '<div class="row ' + (it.is_win ? 'win' : '') + '" data-open="' + it.id + '">'
      + icon(it.is_win ? 'trophy' : 'gift', 18)
      + '<span class="chat">' + esc(it.chat) + '</span>'
      + '<span class="when">' + fmtTime(feed.sort === 'p' ? it.date : it.detected_at) + '</span>'
      + '</div>';
  }).join('');
  const pager = (feed.page > 1 ? chip('← Новее', false, 'p-prev') : '')
    + (data.has_more ? chip('Старее →', false, 'p-next') : '');
  screenEl.innerHTML = '<div class="chips">' + filters + '</div>'
    + (rows ? '<div class="rows">' + rows + '</div>'
            : '<div class="note">Здесь пока пусто.</div>')
    + (pager ? '<div class="chips">' + pager + '</div>' : '');
  renderBar();
}

async function showCard(id) {
  view = 'card';
  const it = await api('/api/app/giveaways/' + id);
  subEl.textContent = it.chat;
  const badge = '<div class="row static ' + (it.is_win ? 'win' : '') + '">'
    + icon(it.is_win ? 'trophy' : 'gift', 20)
    + '<span class="chat">' + (it.is_win ? 'Победа' : 'Розыгрыш') + '</span>'
    + '<span class="when">' + fmtTime(it.detected_at) + '</span></div>';
  const link = it.link
    ? '<div class="row" data-link="' + esc(it.link) + '">' + icon('spark', 18)
      + '<span class="chat">Открыть в Telegram</span></div>'
    : '';
  screenEl.innerHTML = '<div class="rows">' + badge + link + '</div>'
    + '<div class="text" style="margin-top:10px">' + esc(it.text || '—') + '</div>'
    + '<div class="chips">' + chip('← Назад', false, 'back') + '</div>';
  renderBar();
}

async function showMarket() {
  view = 'market';
  const data = await api('/api/app/market');
  subEl.textContent = data.updated || '';
  const coins = data.coins.map(function (c) {
    const dir = c.change == null ? '' : (c.change >= 0 ? 'up' : 'down');
    const change = c.change == null ? '' : (c.change >= 0 ? '+' : '') + c.change.toFixed(1) + '%';
    return '<div class="row static"><span class="chat">' + c.emoji + ' ' + esc(c.code) + '</span>'
      + '<span class="when ' + dir + '">' + change + '</span>'
      + '<span class="num">' + esc(c.usd_text) + '</span></div>';
  }).join('');
  const fiat = data.fiat.map(function (f) {
    return '<div class="row static"><span class="chat">' + f.flag + ' 1 USD → ' + esc(f.code)
      + '</span><span class="num">' + esc(f.text) + '</span></div>';
  }).join('');
  screenEl.innerHTML = (coins ? '<div class="rows">' + coins + '</div>'
                              : '<div class="note">Курсы ещё не собраны.</div>')
    + (fiat ? '<div class="rows spaced">' + fiat + '</div>' : '')
    + '<div class="chips">' + chip('Конвертер', false, 'to-convert') + '</div>';
  renderBar();
}

function showConvert() {
  view = 'convert';
  subEl.textContent = '';
  screenEl.innerHTML = '<div class="conv">'
    + '<input class="amt" id="amt" inputmode="decimal" value="100" aria-label="Сумма">'
    + '<input class="code" id="src" value="USD" aria-label="Из">'
    + '<input class="code" id="dst" value="UAH" aria-label="В">'
    + '</div><div class="chips">' + chip('Посчитать', true, 'do-convert')
    + chip('USD→UAH', false, 'pre-usd') + chip('BTC→USD', false, 'pre-btc') + '</div>'
    + '<div class="out muted" id="out">Введите сумму и пару.</div>';
  renderBar();
}

async function runConvert() {
  const amount = parseFloat(document.getElementById('amt').value.replace(',', '.'));
  const src = document.getElementById('src').value.trim();
  const dst = document.getElementById('dst').value.trim();
  const out = document.getElementById('out');
  if (!amount || amount <= 0) {
    out.className = 'out muted';
    out.textContent = 'Сумма должна быть больше нуля.';
    return;
  }
  try {
    const r = await api('/api/app/convert?amount=' + amount
      + '&src=' + encodeURIComponent(src) + '&dst=' + encodeURIComponent(dst));
    out.className = 'out';
    out.innerHTML = esc(r.from_text) + ' = <span class="to">' + esc(r.to_text) + '</span>';
  } catch (e) {
    out.className = 'out muted';
    out.textContent = e.message;
  }
}

function preset(src, dst) {
  document.getElementById('src').value = src;
  document.getElementById('dst').value = dst;
  runConvert();
}

const ROUTES = {
  home: showHome,
  giveaways: function () { feed.wins = false; feed.page = 1; return showGiveaways(); },
  wins: function () { feed.wins = true; feed.page = 1; return showGiveaways(); },
  market: showMarket,
  convert: function () { showConvert(); },
};

async function go(name) {
  tap();
  try {
    await ROUTES[name]();
  } catch (e) {
    fail(e.message);
  }
}

const ACTIONS = {
  'f-all': function () { feed.wins = false; feed.page = 1; return showGiveaways(); },
  'f-wins': function () { feed.wins = true; feed.page = 1; return showGiveaways(); },
  'f-sort': function () {
    feed.sort = feed.sort === 'd' ? 'p' : 'd';
    feed.page = 1;
    return showGiveaways();
  },
  'p-prev': function () { feed.page = Math.max(1, feed.page - 1); return showGiveaways(); },
  'p-next': function () { feed.page += 1; return showGiveaways(); },
  back: function () { return showGiveaways(); },
  'to-convert': function () { showConvert(); },
  'do-convert': function () { return runConvert(); },
  'pre-usd': function () { preset('USD', 'UAH'); },
  'pre-btc': function () { preset('BTC', 'USD'); },
};

document.addEventListener('click', function (event) {
  const target = event.target.closest('[data-go],[data-open],[data-link],[data-act]');
  if (!target) return;
  tap();
  if (target.dataset.go) { go(target.dataset.go); return; }
  if (target.dataset.open) {
    showCard(target.dataset.open).catch(function (e) { fail(e.message); });
    return;
  }
  if (target.dataset.link) {
    const url = target.dataset.link;
    if (tg && url.indexOf('t.me/') !== -1) tg.openTelegramLink(url);
    else if (tg) tg.openLink(url);
    return;
  }
  const run = ACTIONS[target.dataset.act];
  if (run) Promise.resolve(run()).catch(function (e) { fail(e.message); });
});

if (tg) {
  tg.ready();
  tg.expand();
}
go('home');
