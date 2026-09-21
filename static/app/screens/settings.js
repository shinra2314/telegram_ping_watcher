// Owner settings: notifications (switches, times), the «Работа» fields built
// from bot/settings_schema, and the ignored chats. Every control saves on its
// own and the screen repaints from the server's answer, never refetching.
'use strict';

const SETS = { data: null };
const AUTOCLEAN_NAMES = { 0: 'Выкл', 6: '6 ч', 24: '24 ч', 47: '47 ч' };

App.register('settings', {
  tab: 'more',
  title: 'Настройки',

  async render(params) {
    if (params.force || !SETS.data) SETS.data = await api('/api/app/settings');
    const d = SETS.data;
    const n = d.notifications;
    let html = '<div class="section-label" style="margin-top:4px">Уведомления владельцу</div><div class="list">'
      + switchRow('notify', 'Уведомления', 'Выключенные — карточки приходят беззвучно, не пропадают', n.enabled, 'enabled')
      + switchRow('notify', 'Розыгрыши', 'Карточки о новых розыгрышах', n.include_giveaways, 'include_giveaways')
      + switchRow('notify', 'Победы', 'Карточки о победах', n.include_wins, 'include_wins')
      + switchRow('notify', 'Модерация рассылок', 'Копии друзьям уходят после вашего «Отправить»', n.moderated, 'moderated')
      + switchRow('notify', 'Кнопка «В панели»', 'На ваших карточках побед и упоминаний — открыть запись здесь', n.panel_button, 'panel_button')
      + switchRow('notify', 'Тихие часы', n.quiet_from + '–' + n.quiet_to + ' — без звука', n.quiet_enabled, 'quiet_enabled')
      + switchRow('notify', 'Дайджест', 'Каждый день в ' + n.digest_time, n.digest_enabled, 'digest_enabled')
      + '</div>'
      + '<div class="panel pad" style="margin-top:10px"><div class="btn-row">'
      + timeField('quiet_from', 'Тишина с', n.quiet_from) + timeField('quiet_to', 'до', n.quiet_to)
      + timeField('digest_time', 'Дайджест в', n.digest_time) + '</div></div>'
      + '<div class="section-label">Удалять мелкие уведомления</div>'
      + seg(n.autoclean_choices.map((h) => [String(h), AUTOCLEAN_NAMES[h] || h + ' ч']), String(n.autoclean_hours), 'autoclean')
      + '<div class="hint" style="margin:8px 4px 0">Упоминания, курсы, «восстановилось». Победы и розыгрыши не удаляются никогда.</div>';

    html += '<div class="section-label">Работа</div><div class="list">'
      + d.fields.map((f) => item({
        act: 'field', data: { i: f.index }, title: esc(f.label),
        desc: f.hint ? esc(f.hint) : '', wrap: Boolean(f.hint),
        end: '<div class="t num">' + esc(f.text) + '</div>',
      })).join('') + '</div>';

    html += '<div class="section-label">Игнор-чаты'
      + (d.ignored.length ? '<span class="more" data-act="restore-all">Вернуть все</span>' : '') + '</div>';
    html += d.ignored.length
      ? '<div class="list">' + d.ignored.map((c) => item({
        lead: icon('bell-off', 15), title: esc(c.title),
        end: '<button class="btn small" data-act="restore" data-id="' + c.chat_id + '">Вернуть</button>', chev: false,
      })).join('') + '</div>'
      : '<div class="hint" style="margin:0 4px">Пусто. Чат добавляется кнопкой 🔇 на карточке сообщения из группы.</div>';
    return html;
  },

  actions: {
    notify: (el) => {
      const key = el.dataset.code;
      const patch = {};
      patch[key] = !SETS.data.notifications[key];
      return saveNotify(patch);
    },
    autoclean: (el) => saveNotify({ autoclean_hours: Number(el.dataset.v) }, 'Сохранено'),
    field: (el) => fieldSheet(SETS.data.fields[Number(el.dataset.i)]),
    preset: (el) => saveField(Number(el.dataset.i), el.dataset.v),
    'field-save': () => {
      const input = document.getElementById('field-value');
      if (!input) return null;
      return saveField(Number(input.dataset.i), input.value);
    },
    restore: (el) => restoreChats([Number(el.dataset.id)]),
    'restore-all': async () => {
      if (!(await confirmBox('Снова следить за всеми игнорируемыми чатами?'))) return;
      return restoreChats(SETS.data.ignored.map((c) => c.chat_id));
    },
  },
  changes: {
    time: (el) => {
      const patch = {};
      patch[el.dataset.key] = el.value;
      return saveNotify(patch, 'Время сохранено');
    },
  },
});

function timeField(key, label, value) {
  return '<div class="field" style="flex:1;min-width:0"><label>' + esc(label) + '</label>'
    + '<input class="input" type="time" value="' + esc(value) + '" data-act="time" data-key="' + key + '" style="font-size:15px;padding:0 8px"></div>';
}

async function saveNotify(patch, note) {
  SETS.data = await api('/api/app/settings/notifications', patch, 'настройки уведомлений');
  if (note) toast(note); else haptic('select');
  return App.render({ quiet: true });
}

function fieldSheet(f) {
  let body = '';
  if (f.kind === 'choice') {
    body = '<div class="list">' + f.choices.map((c) => item({ act: 'preset', data: { i: f.index, v: c }, chev: false,
      cls: c === f.value ? 'selected' : '', lead: icon(c === f.value ? 'check' : 'list', 15), title: esc(c) })).join('') + '</div>';
  } else {
    if (f.presets.length) {
      body += '<div class="chips" style="margin-top:0">' + f.presets.map((p) => '<button class="chip' + (String(p) === String(f.value) ? ' on' : '')
        + '" data-act="preset" data-i="' + f.index + '" data-v="' + esc(p) + '">' + esc(p) + (f.unit ? ' ' + esc(f.unit) : '') + '</button>').join('') + '</div>';
    }
    const range = f.min != null ? 'от ' + f.min + (f.max != null ? ' до ' + f.max : '') + (f.unit ? ' ' + f.unit : '') : '';
    body += '<div class="search plain"><input class="input" id="field-value" data-i="' + f.index + '" value="' + esc(f.value == null ? '' : f.value) + '"'
      + (f.kind === 'text' ? '' : ' inputmode="decimal"') + ' data-enter="field-save" autocomplete="off">'
      + '<button class="icon-btn" data-act="field-save" aria-label="Сохранить">' + icon('check', 18) + '</button></div>'
      + (range ? '<div class="hint" style="margin:8px 4px 0">' + esc(range) + '</div>' : '');
  }
  if (f.hint) body += '<div class="hint" style="margin:10px 4px 0">' + esc(f.hint) + '</div>';
  Sheet.open(f.label, body);
}

async function saveField(index, value) {
  const res = await api('/api/app/settings/field', { index: index, value: value }, 'настройка');
  SETS.data.fields[index] = res.field;
  Sheet.close();
  toast(res.field.label + ': ' + res.field.text);
  return App.render({ quiet: true });
}

async function restoreChats(ids) {
  const res = await api('/api/app/settings/ignored/restore', { chat_ids: ids });
  SETS.data = res;
  toast('Снова отслеживаются: ' + res.restored);
  return App.render({ quiet: true });
}
