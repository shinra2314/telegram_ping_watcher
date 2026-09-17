// My notifications: what the bot sends this member, within what their key
// grants. Every control saves on its own and repaints in place — re-rendering
// would jump the scroll and drop the slider mid-drag.
'use strict';

const PREFS = { data: null, saving: 0 };

const PREF_ICONS = { mentions: 'message', giveaways: 'gift', wins: 'trophy', digest: 'chart' };
const AUTOCLEAN_LABELS = { 0: 'Выкл', 6: '6 ч', 24: '24 ч', 47: '47 ч' };

App.register('prefs', {
  tab: 'more',
  title: 'Уведомления',

  async render() {
    const d = await api('/api/app/prefs');
    PREFS.data = d;
    const p = d.prefs;
    const types = d.types.filter((t) => d.allowed.indexOf(t.code) !== -1);

    let html = '<div class="list' + (p.muted ? ' dimmed' : '') + '" id="pref-list">'
      + prefRow('muted', p.muted ? 'bell-off' : 'bell', 'Все уведомления',
        p.muted ? 'Бот сейчас ничего не присылает' : 'Главный выключатель', !p.muted, 'master')
      + types.map((t) => prefRow(t.code, PREF_ICONS[t.code] || 'bell', t.label, t.hint, Boolean(p[t.code]))).join('')
      + '</div>';

    if (d.hidden.length) {
      const names = d.types.filter((t) => d.hidden.indexOf(t.code) !== -1).map((t) => t.label.toLowerCase());
      html += '<div class="locked">' + icon('lock', 15) + '<div>Закрыто владельцем: ' + esc(names.join(', '))
        + (d.hidden.indexOf('digest') !== -1 && d.accounts.length ? '. Дайджест показывает все аккаунты, поэтому ключу на свои аккаунты он не приходит.' : '')
        + '</div></div>';
    }

    if (d.allowed.indexOf('giveaways') !== -1) {
      const score = Number(p.min_score) || 0;
      html += '<div class="section-label">Порог розыгрышей</div>'
        + '<div class="panel slider"><div class="top"><div><div style="font-weight:600">Минимальный score</div>'
        + '<div class="dim" style="font-size:12.5px">Розыгрыши ниже порога не присылаются</div></div>'
        + '<div class="val num" id="score-val">' + scoreLabel(score) + '</div></div>'
        + '<input class="range" id="score" type="range" min="0" max="100" step="5" value="' + score + '"'
        + ' data-act="score" style="--fill:' + score + '%" aria-label="Минимальный score">'
        + '<div class="scale"><span>все</span><span>50</span><span>100</span></div></div>';
    }

    html += '<div class="section-label">Удалять упоминания из чата</div>'
      + seg(d.autoclean_choices.map((h) => [String(h), AUTOCLEAN_LABELS[h] || h + ' ч']), String(p.autoclean_hours), 'autoclean')
      + '<div class="hint" style="margin-top:8px">Победы и розыгрыши не удаляются никогда. Telegram даёт боту стереть сообщение только первые 48 часов.</div>';

    html += '<div class="section-label">Ключ</div><div class="list">'
      + item({ lead: icon('at', 16), title: 'Аккаунты', end: '<div class="d">' + esc(d.accounts.length ? d.accounts.map((a) => '@' + a).join(', ') : 'все') + '</div>' })
      + item({ lead: icon('clock', 16), title: 'Задержка копий', end: '<div class="d">' + esc(d.delay_text) + '</div>' })
      + '</div>';
    return html;
  },

  actions: {
    pref: (el) => {
      const code = el.dataset.code;
      const p = PREFS.data.prefs;
      const next = code === 'muted' ? !p.muted : !p[code];
      const patch = {};
      patch[code] = next;
      return savePrefs(patch, () => paintPrefs());
    },
    autoclean: (el) => {
      const hours = Number(el.dataset.v);
      el.parentNode.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === el));
      haptic('select');
      return savePrefs({ autoclean_hours: hours }, () => {
        const cur = String(PREFS.data.prefs.autoclean_hours);
        el.parentNode.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === cur));
      });
    },
  },

  inputs: {
    // Label and fill follow the finger; the value is saved once, on release.
    score: (el) => {
      el.style.setProperty('--fill', el.value + '%');
      document.getElementById('score-val').textContent = scoreLabel(Number(el.value));
    },
  },

  changes: {
    score: (el) => savePrefs({ min_score: Number(el.value) }, () => {
      const saved = Number(PREFS.data.prefs.min_score) || 0;
      el.value = saved;
      el.style.setProperty('--fill', saved + '%');
      document.getElementById('score-val').textContent = scoreLabel(saved);
    }),
  },
});

function scoreLabel(score) {
  return score ? '≥ ' + score : 'любой';
}

function prefRow(code, iconName, title, hint, on, extra) {
  return '<div class="pref' + (extra ? ' ' + extra : '') + (on ? '' : ' off') + '" data-act="pref" data-code="' + code + '">'
    + '<div class="lead" style="width:30px;height:30px;border-radius:10px;display:grid;place-items:center;flex:none;'
    + 'background:' + (on ? 'var(--accent-soft);color:var(--accent)' : 'var(--card-2);color:var(--faint)') + '">' + icon(iconName, 16) + '</div>'
    + '<div class="body"><div class="t">' + esc(title) + '</div>' + (hint ? '<div class="d">' + esc(hint) + '</div>' : '') + '</div>'
    + '<span class="switch' + (on ? ' on' : '') + '" role="switch" aria-checked="' + on + '"></span></div>';
}

// Repaint every row from PREFS.data — after a save, or to undo an optimistic flip.
function paintPrefs() {
  const list = document.getElementById('pref-list');
  if (!list) return;
  const p = PREFS.data.prefs;
  list.classList.toggle('dimmed', Boolean(p.muted));
  list.querySelectorAll('.pref').forEach((row) => {
    const code = row.dataset.code;
    const on = code === 'muted' ? !p.muted : Boolean(p[code]);
    row.classList.toggle('off', !on);
    row.querySelector('.switch').classList.toggle('on', on);
    row.querySelector('.switch').setAttribute('aria-checked', String(on));
    const lead = row.querySelector('.lead');
    lead.style.background = on ? 'var(--accent-soft)' : 'var(--card-2)';
    lead.style.color = on ? 'var(--accent)' : 'var(--faint)';
    if (code === 'muted') {
      lead.innerHTML = icon(p.muted ? 'bell-off' : 'bell', 16);
      row.querySelector('.d').textContent = p.muted ? 'Бот сейчас ничего не присылает' : 'Главный выключатель';
    }
  });
}

// Optimistic: the control has already moved. On failure the server's last
// known state is painted back and the reason shown.
async function savePrefs(patch, repaint) {
  const before = JSON.parse(JSON.stringify(PREFS.data.prefs));
  Object.assign(PREFS.data.prefs, patch);
  if (patch.muted !== undefined || Object.keys(patch).some((k) => k in PREF_ICONS)) paintPrefs();
  haptic();
  try {
    const fresh = await api('/api/app/prefs', patch);
    PREFS.data = fresh;
    if (repaint) repaint();
    toast('Сохранено');
  } catch (err) {
    PREFS.data.prefs = before;
    if (repaint) repaint();
    paintPrefs();
    toast(err.status === 401 ? 'Откройте панель заново из бота — сессия для изменений истекла' : (err.message || 'Не сохранилось'), true);
  }
}
