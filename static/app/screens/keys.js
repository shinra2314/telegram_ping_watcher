// Keys and the people holding them: the bot's key:* panel and /access as
// screens. Every change posts at once and the answer (the whole key or
// member card) is painted from the response — nothing is refetched, and the
// secret is only ever fetched by «Ссылка-приглашение».
'use strict';

const KEY_STATE = {
  active: ['активен', 'good'],
  revoked: ['отозван', 'bad'],
  expired: ['истёк', 'warn'],
  used: ['использован', ''],
};
const EXPIRY_PRESETS = [[0, 'бессрочно'], [1, '1 день'], [7, '7 дней'], [30, '30 дней'], [90, '90 дней']];
const DELAY_PRESETS = [0, 5, 15, 30, 60];
const WEEKDAYS = [[1, 'пн'], [2, 'вт'], [3, 'ср'], [4, 'чт'], [5, 'пт'], [6, 'сб'], [7, 'вс']];

App.register('keys', {
  tab: 'more',
  title: 'Ключи',

  async render() {
    const data = await api('/api/app/keys');
    App.sub.textContent = data.items.length + ' ' + plural(data.items.length, 'ключ', 'ключа', 'ключей');
    let html = '<div class="stack"><button class="btn primary" data-act="new">' + icon('plus', 18) + 'Новый ключ</button></div>';
    if (!data.items.length) return html + emptyView('lock', 'Ключей нет', 'Ключ — это приглашение в бота с набором прав.');
    html += '<div class="list" style="margin-top:12px">' + data.items.map((k) => {
      const st = KEY_STATE[k.state] || [k.state, ''];
      const bits = [k.members + ' ' + plural(k.members, 'человек', 'человека', 'человек'),
        k.accounts.length ? k.accounts.map((a) => '@' + a).join(', ') : 'все аккаунты'];
      return item({
        act: 'open', data: { id: k.id }, lead: icon(k.role === 'premium' ? 'flame' : 'lock', 16),
        leadCls: k.state === 'active' ? 'on' : '',
        title: esc(k.label || 'без метки') + ' <span class="badge ' + st[1] + '">' + st[0] + '</span>',
        desc: esc(bits.join(' · ')), wrap: true,
        end: k.expires_at ? '<div class="d num">до ' + esc(fmtTime(k.expires_at)) + '</div>' : '',
      });
    }).join('') + '</div>';
    return html;
  },

  actions: {
    open: (el) => App.go('key', { id: el.dataset.id }),
    new: () => {
      Sheet.open('Новый ключ', '<div class="stack">'
        + '<div class="field"><label for="key-label">Метка — как вы его узнаете</label>'
        + '<input class="input" id="key-label" maxlength="40" placeholder="Например: Вова" data-enter="create" autocomplete="off"></div>'
        + '<div class="hint">Права по умолчанию как у /newkey: просмотр, все разделы, без срока. Поменять можно сразу после создания.</div>'
        + '<button class="btn primary" data-act="create">' + icon('plus', 18) + 'Создать</button></div>',
      (sheet) => setTimeout(() => { const f = sheet.querySelector('#key-label'); if (f) f.focus(); }, 60));
    },
    create: async () => {
      const input = document.getElementById('key-label');
      const res = await api('/api/app/keys', { label: input ? input.value : '' });
      Sheet.close();
      toast('Ключ создан');
      return App.go('key', { id: String(res.id) });
    },
  },
});

const KEY = { card: null };

App.register('key', {
  tab: 'more',
  title: 'Ключ',

  async render(params) {
    if (params.force || !KEY.card || String(KEY.card.id) !== String(params.id)) {
      KEY.card = await api('/api/app/keys/' + encodeURIComponent(params.id));
    }
    const k = KEY.card;
    const g = k.grants;
    const o = k.options;
    const st = KEY_STATE[k.state] || [k.state, ''];
    App.sub.textContent = k.label || 'без метки';
    const scoped = g.accounts.length ? g.accounts : o.accounts;
    let html = '<div class="panel pad keyed">'
      + '<div style="display:flex;gap:8px;align-items:center"><span class="badge ' + st[1] + '">' + st[0] + '</span>'
      + '<span class="dim" style="font-size:13px">' + k.members + ' ' + plural(k.members, 'держатель', 'держателя', 'держателей') + '</span></div>'
      + '<div class="search" style="margin-top:12px">' + icon('user', 18) + '<input class="input" id="key-name" maxlength="40" value="' + esc(k.label) + '" placeholder="Метка" data-enter="rename">'
      + '<button class="icon-btn" data-act="rename" aria-label="Сохранить метку">' + icon('check', 18) + '</button></div>'
      + '<div class="btn-row" style="margin-top:10px">'
      + '<button class="btn" data-act="link">' + icon('copy', 18) + 'Ссылка</button>'
      + '<button class="btn' + (k.state === 'revoked' ? ' primary' : ' danger') + '" data-act="revoke">'
      + icon(k.state === 'revoked' ? 'refresh' : 'x', 18) + (k.state === 'revoked' ? 'Вернуть' : 'Отозвать') + '</button></div></div>';

    html += '<div class="section-label">Доступ</div>'
      + seg([['viewer', 'Просмотр'], ['premium', 'Премиум']], k.role, 'role')
      + '<div class="chips">' + EXPIRY_PRESETS.map((p) =>
        '<button class="chip' + (expiryPick(k.expires_at) === p[0] ? ' on' : '') + '" data-act="expiry" data-v="' + p[0] + '">' + p[1] + '</button>').join('') + '</div>'
      + '<div class="hint" style="margin:0 4px">' + (k.expires_at ? 'Действует до ' + esc(fmtTime(k.expires_at)) : 'Без срока') + '</div>'
      + switchRow('once', 'Одноразовый', 'Закроется после первого входа', k.max_uses === 1);

    html += '<div class="section-label">Разделы</div><div class="list">'
      + o.features.map((f) => switchRow('feature', f.label, f.hint, g.features.indexOf(f.code) !== -1, f.code)).join('') + '</div>';
    html += '<div class="section-label">Уведомления</div><div class="list">'
      + o.notify.map((n) => switchRow('notify', n.label, n.hint, g.notify.indexOf(n.code) !== -1, n.code)).join('') + '</div>';

    html += '<div class="section-label">Аккаунты<span class="more" data-act="all-accounts">' + (g.accounts.length ? 'Все' : '') + '</span></div>'
      + '<div class="tags">' + o.accounts.map((a) => '<button class="chip' + (scoped.indexOf(a) !== -1 ? ' on' : '')
        + '" data-act="account" data-v="' + esc(a) + '">@' + esc(a) + '</button>').join('') + '</div>'
      + '<div class="hint" style="margin:6px 4px 0">' + (g.accounts.length ? 'Видит только отмеченные.' : 'Видит все аккаунты, в том числе будущие.') + '</div>';

    const delay = g.delay_minutes;
    html += '<div class="section-label">Задержка копий</div><div class="panel slider"><div class="top"><div>'
      + '<div style="font-weight:600">Через сколько приходят уведомления</div></div>'
      + '<div class="val num" id="delay-val">' + delayLabel(delay) + '</div></div>'
      + '<input class="range" id="delay" type="range" min="0" max="60" step="5" value="' + Math.min(60, delay) + '" data-act="delay" style="--fill:' + Math.round(100 * Math.min(60, delay) / 60) + '%">'
      + '<div class="scale"><span>сразу</span><span>30 мин</span><span>1 ч</span></div></div>';

    if (k.holders.length) {
      html += '<div class="section-label">Держатели</div><div class="list">'
        + k.holders.map((h) => item({ act: 'member', data: { tg: h.tg_id }, lead: icon('user', 16), leadCls: h.blocked ? 'bad' : 'on',
          title: esc(h.name), desc: h.username ? '@' + esc(h.username) : '', end: h.blocked ? '<span class="badge bad">блок</span>' : '' })).join('') + '</div>';
    }
    html += '<div style="margin-top:18px"><button class="btn ghost danger" data-act="delete">' + icon('x', 18) + 'Удалить ключ</button></div>';
    return html;
  },

  actions: {
    rename: () => {
      const input = document.getElementById('key-name');
      const label = (input && input.value || '').trim();
      if (!label || label === KEY.card.label) return null;
      return saveKey({ label: label }, 'Метка сохранена');
    },
    role: (el) => saveKey({ role: el.dataset.v }),
    expiry: (el) => saveKey({ expires_days: Number(el.dataset.v) }, 'Срок обновлён'),
    once: () => saveKey({ once: KEY.card.max_uses !== 1 }),
    feature: (el) => saveKey({ features: toggled(KEY.card.grants.features, el.dataset.code) }),
    notify: (el) => saveKey({ notify: toggled(KEY.card.grants.notify, el.dataset.code) }),
    account: (el) => {
      const all = KEY.card.options.accounts;
      const now = KEY.card.grants.accounts.length ? KEY.card.grants.accounts : all.slice();
      const next = toggled(now, el.dataset.v);
      if (!next.length) { toast('Хотя бы один аккаунт должен остаться', true); return null; }
      return saveKey({ accounts: next });
    },
    'all-accounts': () => saveKey({ accounts: KEY.card.options.accounts.slice() }),
    link: async () => {
      const res = await api('/api/app/keys/' + KEY.card.id + '/link', {});
      Sheet.open('Приглашение', '<div class="text-block mono" style="font-size:13px">' + esc(res.link || res.secret) + '</div>'
        + (res.state !== 'active' ? '<div class="hint" style="margin:8px 4px 0;color:var(--amber)">Ключ сейчас не действует — ссылка не откроет доступ.</div>' : '')
        + '<div class="stack" style="margin-top:12px"><button class="btn primary" data-act="copy" data-text="' + esc(res.link || res.secret) + '">'
        + icon('copy', 18) + 'Скопировать</button></div>');
    },
    revoke: async () => {
      const revoked = KEY.card.state !== 'revoked';
      if (revoked && !(await confirmBox('Отозвать ключ? Ссылка перестанет открывать доступ. Кто уже вошёл — останется.'))) return;
      await api('/api/app/keys/' + KEY.card.id + '/revoke', { revoked: revoked });
      KEY.card = null;
      toast(revoked ? 'Ключ отозван' : 'Ключ снова работает');
      return App.render({ quiet: true });
    },
    delete: async () => {
      if (!(await confirmBox('Удалить ключ «' + (KEY.card.label || 'без метки') + '» навсегда? Вошедшие сохранят доступ — отключить их можно в карточке человека.'))) return;
      await api('/api/app/keys/' + KEY.card.id + '/delete', {});
      KEY.card = null;
      toast('Ключ удалён');
      return App.back();
    },
    member: (el) => App.go('member', { tg: el.dataset.tg }),
  },

  inputs: {
    delay: (el) => {
      el.style.setProperty('--fill', Math.round(100 * Number(el.value) / 60) + '%');
      document.getElementById('delay-val').textContent = delayLabel(Number(el.value));
    },
  },
  changes: {
    delay: (el) => saveKey({ delay_minutes: Number(el.value) }, 'Задержка сохранена'),
  },
});

async function saveKey(patch, note) {
  KEY.card = await api('/api/app/keys/' + KEY.card.id, patch, 'настройки ключа');
  if (note) toast(note); else haptic('select');
  return App.render({ quiet: true });
}

function toggled(list, code) {
  const set = list.slice();
  const i = set.indexOf(code);
  if (i === -1) set.push(code); else set.splice(i, 1);
  return set;
}

// Which preset chip matches the stored expiry: the nearest by days left.
function expiryPick(iso) {
  const at = parseTime(iso);
  if (!at) return 0;
  const days = Math.round((at - new Date()) / 86400000);
  const hit = EXPIRY_PRESETS.find((p) => p[0] && Math.abs(p[0] - days) < 1);
  return hit ? hit[0] : -1;
}

function delayLabel(minutes) {
  if (!minutes) return 'сразу';
  return minutes < 60 ? minutes + ' мин' : (minutes / 60).toFixed(minutes % 60 ? 1 : 0).replace('.', ',') + ' ч';
}

function switchRow(act, title, hint, on, code) {
  return '<div class="pref' + (on ? '' : ' off') + '" data-act="' + act + '"' + (code ? ' data-code="' + esc(code) + '"' : '') + '>'
    + '<div class="body"><div class="t">' + esc(title) + '</div>' + (hint ? '<div class="d">' + esc(hint) + '</div>' : '') + '</div>'
    + '<span class="switch' + (on ? ' on' : '') + '" role="switch" aria-checked="' + on + '"></span></div>';
}

// ── A key holder: access right now and the schedule ─────────────────────
const MEMBER = { card: null, form: { kind: 'work', start: '09:00', end: '18:00', days: [1, 2, 3, 4, 5], tz: '' } };

App.register('member', {
  tab: 'more',
  title: 'Человек',

  async render(params) {
    if (params.force || !MEMBER.card || String(MEMBER.card.tg_id) !== String(params.tg)) {
      MEMBER.card = await api('/api/app/members/' + encodeURIComponent(params.tg));
    }
    const m = MEMBER.card;
    const f = MEMBER.form;
    // The member's own zone if they have one, otherwise Kyiv — never a
    // silent UTC under hours typed as local time.
    if (!f.tz) f.tz = m.timezone && m.timezone !== 'UTC' ? m.timezone : 'Europe/Kyiv';
    const zones = m.zones.indexOf(f.tz) === -1 ? [f.tz].concat(m.zones) : m.zones;
    App.sub.textContent = m.name;
    let html = '<div class="panel pad ' + (m.open_now ? 'keyed' : '') + '">'
      + '<div style="display:flex;gap:10px;align-items:center"><span class="dot ' + (m.blocked ? 'bad' : (m.open_now ? 'on' : 'warn')) + '"></span>'
      + '<div style="flex:1;min-width:0"><div style="font-weight:650">' + esc(m.name) + (m.username ? ' <span class="dim">@' + esc(m.username) + '</span>' : '') + '</div>'
      + '<div class="dim" style="font-size:13px">' + (m.blocked ? 'заблокирован' : (m.open_now ? 'доступ открыт' : 'доступ закрыт'))
      + (m.until ? ' до ' + esc(fmtTime(m.until)) : '') + ' · ключ «' + esc(m.key_label || '—') + '»</div></div></div>'
      + '<div class="btn-row" style="margin-top:12px">'
      + (m.open_now
        ? '<button class="btn" data-act="close" data-h="2">' + icon('clock', 18) + 'Закрыть на 2 ч</button>'
          + '<button class="btn danger" data-act="close" data-h="0">' + icon('lock', 18) + 'Закрыть</button>'
        : '<button class="btn primary" data-act="open">' + icon('check', 18) + 'Открыть сейчас</button>')
      + '</div></div>';

    html += '<div class="section-label">Расписание</div>';
    if (m.windows.length) {
      html += '<div class="list">' + m.windows.map((w) => item({
        lead: icon(w.allow ? 'check' : 'lock', 15), leadCls: w.allow ? 'on' : 'warn',
        title: w.manual ? 'Закрыт вручную' : (w.allow ? 'Открыт' : 'Закрыт'), desc: esc(w.text), wrap: true,
        end: '<button class="icon-btn" data-act="drop" data-id="' + w.id + '" aria-label="Удалить окно">' + icon('x', 16) + '</button>',
      })).join('') + '</div>';
    } else {
      html += '<div class="hint" style="margin:0 4px">Окон нет — доступ по умолчанию: ' + (m.policy === 'deny' ? 'закрыт' : 'открыт') + '.</div>';
    }

    html += '<div class="section-label">Добавить окно</div><div class="panel pad">'
      + seg([['work', 'Открыт в эти часы'], ['mute', 'Закрыт в эти часы']], f.kind, 'kind')
      + '<div class="btn-row" style="margin-top:12px">'
      + '<div class="field" style="flex:1"><label for="w-start">С</label><input class="input" id="w-start" type="time" value="' + f.start + '" data-act="w-start"></div>'
      + '<div class="field" style="flex:1"><label for="w-end">До</label><input class="input" id="w-end" type="time" value="' + f.end + '" data-act="w-end"></div></div>'
      + '<div class="tags" style="margin-top:12px">' + WEEKDAYS.map((d) => '<button class="chip' + (f.days.indexOf(d[0]) !== -1 ? ' on' : '')
        + '" data-act="day" data-v="' + d[0] + '">' + d[1] + '</button>').join('') + '</div>'
      + '<div class="hint" style="margin:8px 0 0">' + (f.kind === 'work'
        ? 'Вне этих часов доступ будет закрыт.' : 'В эти часы бот ничего не присылает и меню закрыто.')
      + ' Без дней — каждый день.</div>'
      + '<div class="field" style="margin-top:10px"><label for="w-tz">Часовой пояс</label>'
      + '<select class="input" id="w-tz" data-act="w-tz">' + zones.map((z) => '<option' + (z === f.tz ? ' selected' : '') + '>' + esc(z) + '</option>').join('')
      + '</select></div>'
      + '<button class="btn primary" style="margin-top:12px" data-act="add">' + icon('plus', 18) + 'Добавить</button></div>';

    html += '<div style="margin-top:18px"><button class="btn ghost' + (m.blocked ? '' : ' danger') + '" data-act="block">'
      + icon(m.blocked ? 'check' : 'x', 18) + (m.blocked ? 'Разблокировать' : 'Заблокировать') + '</button></div>';
    return html;
  },

  actions: {
    open: () => memberAccess({ action: 'open' }, 'Доступ открыт'),
    close: (el) => memberAccess({ action: 'close', hours: Number(el.dataset.h) }, 'Доступ закрыт'),
    block: async () => {
      const block = !MEMBER.card.blocked;
      if (block && !(await confirmBox('Заблокировать ' + MEMBER.card.name + '? Бот перестанет ему отвечать и присылать копии.'))) return;
      return memberAccess({ action: block ? 'block' : 'unblock' }, block ? 'Заблокирован' : 'Разблокирован');
    },
    kind: (el) => { MEMBER.form.kind = el.dataset.v; return App.render({ quiet: true }); },
    // Weekday chips flip in place: the time fields keep what was typed.
    day: (el) => {
      const d = Number(el.dataset.v);
      MEMBER.form.days = toggled(MEMBER.form.days, d);
      el.classList.toggle('on', MEMBER.form.days.indexOf(d) !== -1);
      haptic('select');
    },
    add: async () => {
      const f = MEMBER.form;
      f.start = document.getElementById('w-start').value || f.start;
      f.end = document.getElementById('w-end').value || f.end;
      MEMBER.card = await api('/api/app/members/' + MEMBER.card.tg_id + '/windows',
        { kind: f.kind, start: f.start, end: f.end, days: f.days.slice().sort(), tz: f.tz });
      toast('Окно добавлено');
      return App.render({ quiet: true });
    },
    drop: async (el) => {
      if (!(await confirmBox('Удалить это окно?'))) return;
      MEMBER.card = await api('/api/app/members/' + MEMBER.card.tg_id + '/windows/' + el.dataset.id + '/delete', {});
      toast('Окно удалено');
      return App.render({ quiet: true });
    },
  },
  changes: {
    'w-start': (el) => { MEMBER.form.start = el.value; },
    'w-end': (el) => { MEMBER.form.end = el.value; },
    'w-tz': (el) => { MEMBER.form.tz = el.value; },
  },
});

async function memberAccess(body, note) {
  MEMBER.card = await api('/api/app/members/' + MEMBER.card.tg_id + '/access', body);
  toast(note);
  return App.render({ quiet: true });
}
