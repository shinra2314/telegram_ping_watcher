// Mentions feed and search: the same selection as the bot's mon:f: callback,
// cut to the key's accounts on the server. Read-only — statuses, notes and
// tags stay in the owner's bot.
'use strict';

// Filters persist (Saved 'feed'), search text does not. Rows are cached like
// the giveaways queue; «Показать ещё» continues from the last row on screen.
const FEED = {
  type: 'a', status: 'a', sort: 'd', asc: false, q: '', fav: false,
  picking: false, picked: new Set(),
  rows: null, meta: null, at: 0,
  STALE_MS: 60000,
  keep() { Saved.put('feed', { type: this.type, status: this.status, sort: this.sort, asc: this.asc }); },
  restore() {
    const s = Saved.get('feed', {});
    const known = (table, code) => table.some((t) => t[0] === code);
    if (known(FEED_TYPES, s.type)) this.type = s.type;
    if (known(FEED_STATUSES, s.status)) this.status = s.status;
    if (known(FEED_SORTS, s.sort)) this.sort = s.sort;
    this.asc = Boolean(s.asc);
  },
  reset() { this.rows = null; },
  // Any filter change: remember it, forget the rows, repaint in place.
  set(changes) {
    Object.assign(this, changes);
    this.keep();
    this.reset();
    return App.render({ quiet: true });
  },
  async load() {
    const data = await api(feedQuery());
    this.rows = data.items;
    this.meta = data;
    this.at = Date.now();
    Pulse.saw('feed');
  },
  async more() {
    const last = this.rows[this.rows.length - 1];
    const data = await api(feedQuery() + '&after=' + last.id + '&loaded=' + this.rows.length);
    const have = new Set(this.rows.map((r) => r.id));
    this.rows = this.rows.concat(data.items.filter((r) => !have.has(r.id)));
    this.meta = Object.assign({}, this.meta, { has_more: data.has_more });
  },
};

const FEED_TYPES = [['a', 'Все'], ['w', 'Победы'], ['g', 'Розыгрыши'], ['i', 'Важные'], ['c', 'Каналы'], ['r', 'Группы'], ['p', 'Личные']];
const FEED_SORTS = [['d', 'Сначала новые'], ['m', 'По дате поста'], ['p', 'По приоритету']];
const FEED_STATUSES = [['a', 'Любой статус'], ['n', 'Новые'], ['r', 'Прочитанные'], ['i', 'Важные'], ['g', 'Игнор'], ['s', 'Решённые']];

function feedQuery() {
  return '/api/app/feed?type=' + FEED.type + '&status=' + FEED.status + '&sort=' + FEED.sort
    + '&asc=' + FEED.asc + (FEED.fav ? '&fav=true' : '') + (FEED.q ? '&q=' + encodeURIComponent(FEED.q) : '');
}

function feedDock() {
  const old = document.querySelector('.action-dock');
  if (old) old.remove();
  const n = FEED.picked.size;
  if (!FEED.picking || !n) return;
  const el = document.createElement('div');
  el.className = 'action-dock';
  el.innerHTML = '<button class="btn primary" data-act="read">' + icon('check', 18) + 'Прочитано · ' + n + '</button>';
  document.body.appendChild(el);
}

function feedLead(r) {
  if (r.is_win) return [icon('trophy', 16), 'on'];
  if (r.is_giveaway) return [icon('gift', 16), ''];
  if (r.priority === 'critical' || r.priority === 'high') return [icon('flame', 16), 'warn'];
  return [icon(r.chat_type === 'group' ? 'users' : 'message', 16), ''];
}

function labelOf(table, code) {
  const row = table.find((t) => t[0] === code);
  return row ? row[1] : '';
}

App.register('feed', {
  tab: 'feed',
  title: 'Лента',

  reset: () => FEED.reset(),

  mounted() {
    feedDock();
    return () => { const d = document.querySelector('.action-dock'); if (d) d.remove(); };
  },

  async render(params) {
    if (params.force || !FEED.rows || Date.now() - FEED.at > FEED.STALE_MS) await FEED.load();
    const last = FEED.meta;
    const rows = FEED.rows;
    App.sub.textContent = last.accounts.length ? last.accounts.map((a) => '@' + a).join(', ') : 'все аккаунты';

    let html = '';
    if (last.can_search) {
      html += '<div class="search">' + icon('search', 18)
        + '<input class="input" id="feed-q" type="search" placeholder="Поиск по тексту, чату, автору" value="' + esc(FEED.q) + '"'
        + ' data-enter="search" enterkeyhint="search" autocomplete="off" maxlength="64">'
        + '<button class="icon-btn" data-act="search" aria-label="Найти">' + icon('send', 18) + '</button></div>';
      if (FEED.q) {
        html += '<div class="search-note">' + icon('search', 14) + '<span>Найдено по «<b>' + esc(FEED.q) + '</b>»</span>'
          + '<button class="link" data-act="clear-search">Сбросить</button></div>';
      }
    }
    html += '<div class="chips">' + FEED_TYPES.map((t) =>
      '<button class="chip' + (t[0] === FEED.type ? ' on' : '') + '" data-act="type" data-v="' + t[0] + '">' + esc(t[1]) + '</button>').join('')
      + '</div>'
      + '<div class="chips" style="margin-top:-4px">'
      + '<button class="chip" data-act="sort">' + icon('clock', 15) + esc(labelOf(FEED_SORTS, FEED.sort)) + '</button>'
      + '<button class="chip" data-act="order" aria-label="Порядок">' + icon(FEED.asc ? 'down' : 'swap', 15) + (FEED.asc ? 'Старые первыми' : 'Новые первыми') + '</button>'
      + (last.can_status
        ? '<button class="chip' + (FEED.status !== 'a' ? ' on' : '') + '" data-act="status">' + esc(labelOf(FEED_STATUSES, FEED.status)) + '</button>'
          + '<button class="chip' + (FEED.fav ? ' on' : '') + '" data-act="fav">' + icon('star', 15) + 'Избранное</button>'
          + '<button class="chip' + (FEED.picking ? ' on' : '') + '" data-act="pick">' + icon('check', 15) + (FEED.picking ? 'Готово' : 'Выбрать') + '</button>'
        : '')
      + '</div>';

    if (!rows.length) {
      return html + (FEED.q
        ? emptyView('search', 'Ничего не нашлось', 'Попробуйте другое слово или снимите фильтр типа.')
        : emptyView('list', 'Пока пусто', 'Здесь появятся упоминания ваших аккаунтов.'));
    }
    html += '<div class="list">' + rows.map((r) => {
      const lead = FEED.picking && FEED.picked.has(r.id) ? [icon('check', 16), ''] : feedLead(r);
      const who = r.sender ? esc(r.sender) + ' · ' : '';
      const cls = [r.deleted ? 'gone' : '', FEED.picked.has(r.id) ? 'selected' : '', r.status === 'new' ? 'unread' : ''].join(' ').trim();
      return item({
        act: FEED.picking ? 'toggle' : 'open', data: { id: r.id }, chev: false, cls: cls,
        lead: lead[0], leadCls: lead[1],
        title: (r.favorite ? icon('star', 13) + ' ' : '') + esc(r.chat),
        desc: who + esc(r.snippet || '—'), wrap: true,
        end: '<div class="d num">' + esc(fmtTime(FEED.sort === 'm' ? r.date : r.detected_at)) + '</div>',
      }).replace('class="d wrap"', 'class="d two"');
    }).join('') + '</div>';
    if (last.has_more) {
      html += '<div style="margin-top:12px"><button class="btn ghost" data-act="more">Показать ещё</button></div>';
    }
    return html;
  },

  actions: {
    type: (el) => { haptic('select'); return FEED.set({ type: el.dataset.v }); },
    sort: () => {
      const codes = FEED_SORTS.map((s) => s[0]);
      return FEED.set({ sort: codes[(codes.indexOf(FEED.sort) + 1) % codes.length] });
    },
    order: () => FEED.set({ asc: !FEED.asc }),
    status: () => {
      Sheet.open('Статус', '<div class="list">' + FEED_STATUSES.map((s) => item({
        act: 'pick-status', data: { v: s[0] }, chev: false, cls: s[0] === FEED.status ? 'selected' : '',
        lead: icon(s[0] === FEED.status ? 'check' : 'list', 16), title: esc(s[1]),
      })).join('') + '</div>');
    },
    'pick-status': (el) => { Sheet.close(); return FEED.set({ status: el.dataset.v }); },
    search: async () => {
      const input = document.getElementById('feed-q');
      const q = (input && input.value || '').trim();
      if (input) input.blur();
      if (q === FEED.q) return null;
      FEED.q = q;
      FEED.reset();
      return App.render({ quiet: true });
    },
    'clear-search': () => { FEED.q = ''; FEED.reset(); return App.render({ quiet: true }); },
    more: async (el) => {
      el.disabled = true;
      try { await FEED.more(); } finally { el.disabled = false; }
      return App.render({ quiet: true });
    },
    fav: () => FEED.set({ fav: !FEED.fav }),
    pick: () => { FEED.picking = !FEED.picking; FEED.picked.clear(); return App.render({ quiet: true }); },
    // Selecting repaints the row and the dock only — the list stays put.
    toggle: (el) => {
      const id = Number(el.dataset.id);
      if (FEED.picked.has(id)) FEED.picked.delete(id); else FEED.picked.add(id);
      el.classList.toggle('selected', FEED.picked.has(id));
      haptic('select');
      feedDock();
    },
    read: async () => {
      const ids = Array.from(FEED.picked);
      if (!ids.length) return;
      const res = await api('/api/app/feed/read', { ids: ids }, 'прочитано · ' + ids.length);
      FEED.rows.forEach((r) => { if (FEED.picked.has(r.id)) r.status = 'read'; });
      FEED.picked.clear();
      FEED.picking = false;
      toast('Прочитано: ' + res.count);
      return App.render({ quiet: true });
    },
    open: (el) => App.go('ping', { id: el.dataset.id }),
  },
});

App.register('ping', {
  tab: 'feed',
  title: 'Упоминание',

  async render(params) {
    // The owner's edits answer with the whole card: paint that, do not refetch.
    if (params.force || !PING.card || String(PING.card.id) !== String(params.id)) {
      PING.card = await api('/api/app/feed/' + encodeURIComponent(params.id));
    }
    const p = PING.card;
    App.sub.textContent = p.chat;
    const lead = feedLead(p);
    const kind = p.is_win ? 'Победа' : (p.is_giveaway ? 'Розыгрыш' : 'Упоминание');
    let html = '<div class="panel pad' + (p.is_win ? ' keyed' : '') + '" style="display:flex;gap:12px;align-items:center">'
      + '<div class="lead" style="width:44px;height:44px;border-radius:14px;display:grid;place-items:center;flex:none;'
      + 'background:' + (lead[1] === 'on' ? 'var(--accent-soft);color:var(--accent)' : 'var(--card-2);color:var(--dim)') + '">'
      + lead[0].replace('width="16" height="16"', 'width="22" height="22"') + '</div>'
      + '<div style="flex:1;min-width:0"><div style="font-weight:650;font-size:16px">' + kind + '</div>'
      + '<div class="dim" style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + esc(p.chat) + ' · ' + esc(fmtTime(p.detected_at)) + '</div></div>'
      + (p.deleted ? '<span class="badge bad">удалён</span>' : '')
      + '</div>';

    const cells = [];
    if (p.sender) cells.push(['Автор', p.sender]);
    if (p.mentions && p.mentions.length) cells.push(['Упоминание', p.mentions.map((m) => '@' + m).join(', ')]);
    if (p.date) cells.push(['Пост', fmtTime(p.date)]);
    if (p.status) cells.push(['Статус', p.status]);
    if (p.giveaway_status) cells.push([p.is_win ? 'Приз' : 'Розыгрыш', p.giveaway_status]);
    // An odd cell would leave a hole in the two-column grid; the last one spans.
    html += '<div class="kv" style="margin-top:10px">' + cells.map((c, i) =>
      '<div class="cell"' + (cells.length % 2 && i === cells.length - 1 ? ' style="grid-column:span 2"' : '') + '>'
      + '<div class="k">' + esc(c[0]) + '</div><div class="v sm" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'
      + esc(c[1]) + '</div></div>').join('') + '</div>';

    if (p.status_code) {
      html += ownerBlock(p);
    } else {
      if (p.tags && p.tags.length) {
        html += '<div class="section-label">Теги</div><div class="tags">'
          + p.tags.map((t) => '<span class="badge">#' + esc(t) + '</span>').join('') + '</div>';
      }
      if (p.note) html += '<div class="section-label">Заметка владельца</div><div class="note-block">' + esc(p.note) + '</div>';
    }
    html += '<div class="section-label">Текст</div><div class="text-block">' + esc(p.text || '—') + '</div>';
    if (p.link) {
      html += '<div class="stack" style="margin-top:12px">'
        + '<button class="btn primary" data-act="link" data-url="' + esc(p.link) + '">' + icon('external', 18) + 'Открыть в Telegram</button>'
        + '<button class="btn ghost" data-act="copy" data-text="' + esc(p.link) + '">' + icon('copy', 18) + 'Скопировать ссылку</button></div>';
    }
    if (p.can_ignore && !p.ignored) {
      html += '<div style="margin-top:10px"><button class="btn ghost" data-act="ignore">' + icon('bell-off', 18) + 'Не следить за этим чатом</button></div>';
    }
    return html;
  },

  actions: {
    st: (el) => pingMeta({ status: el.dataset.v }),
    fav: () => pingMeta({ favorite: !PING.card.favorite }, PING.card.favorite ? 'Звезда снята' : 'В избранном'),
    'note-save': () => {
      const box = document.getElementById('ping-note');
      return pingMeta({ note: box ? box.value : '' }, 'Заметка сохранена');
    },
    'tag-add': () => {
      const input = document.getElementById('ping-tag');
      const tag = (input && input.value || '').trim().replace(/^#/, '');
      if (!tag) return null;
      return pingMeta({ tag_add: tag }, 'Тег добавлен');
    },
    'tag-rm': (el) => pingMeta({ tag_remove: el.dataset.v }),
    ignore: async () => {
      if (!(await confirmBox('Больше не следить за «' + PING.card.chat + '»? Вернуть можно в «Настройки» → «Игнор-чаты».'))) return;
      const res = await api('/api/app/feed/' + PING.card.id + '/ignore', {});
      PING.card.ignored = true;
      toast('Не следим: ' + res.title);
      return App.render({ quiet: true });
    },
  },
});

const PING = { card: null };

async function pingMeta(patch, note) {
  PING.card = await api('/api/app/feed/' + PING.card.id + '/meta', patch, 'правка записи');
  // The list shows status and star: its cached rows are stale now.
  FEED.reset();
  if (note) toast(note); else haptic('select');
  return App.render({ quiet: true });
}

// The owner's inbox controls: status, star, note, tags — the mention card's
// buttons in the bot, with a real text field for the note.
function ownerBlock(p) {
  return '<div class="section-label">Статус<span class="more" data-act="fav">' + icon('star', 14) + (p.favorite ? ' в избранном' : ' в избранное') + '</span></div>'
    + '<div class="chips" style="margin-top:0">' + p.statuses.map((s) =>
      '<button class="chip' + (s.code === p.status_code ? ' on' : '') + '" data-act="st" data-v="' + s.code + '">' + esc(s.label) + '</button>').join('') + '</div>'
    + '<div class="section-label">Теги</div><div class="tags">'
    + p.tags.map((t) => '<button class="chip on" data-act="tag-rm" data-v="' + esc(t) + '">#' + esc(t) + ' ×</button>').join('')
    + '</div><div class="search plain" style="margin-top:8px"><input class="input" id="ping-tag" maxlength="32" placeholder="новый тег" data-enter="tag-add" autocomplete="off">'
    + '<button class="icon-btn" data-act="tag-add" aria-label="Добавить тег">' + icon('plus', 18) + '</button></div>'
    + '<div class="section-label">Заметка</div>'
    + '<textarea class="input note-input" id="ping-note" maxlength="1000" rows="3" placeholder="Для себя: что с этим делать">' + esc(p.note || '') + '</textarea>'
    + '<div style="margin-top:8px"><button class="btn small" data-act="note-save">' + icon('check', 16) + 'Сохранить заметку</button></div>';
}
