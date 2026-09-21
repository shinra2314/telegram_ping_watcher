// «Система»: what /api/health and 🩺 Диагностика know, as one screen, plus
// the four maintenance buttons the bot has (scan, clean-up, backup, resend).
'use strict';

App.register('system', {
  tab: 'more',
  title: 'Система',

  async render() {
    const s = await api('/api/app/system');
    const ok = s.status === 'ok';
    App.sub.textContent = ok ? 'всё в порядке' : 'есть проблемы';
    const bot = s.bot || {};
    const botLine = !bot.configured ? 'не настроен'
      : (bot.connected
        ? 'на связи' + (bot.last_update_seconds != null ? ' · апдейт ' + ago(bot.last_update_seconds) + ' назад' : '')
        : 'нет связи' + (bot.offline_seconds ? ' ' + ago(bot.offline_seconds) : ''));
    // A job that died is both "missing" and "unhealthy": name it once.
    const badJobs = Array.from(new Set(s.unhealthy_jobs.map((j) => j.job).concat(s.missing_jobs)));
    const jobsLine = badJobs.length
      ? 'не работают: ' + badJobs.slice(0, 4).join(', ') + (badJobs.length > 4 ? ' и ещё ' + (badJobs.length - 4) : '')
      : s.jobs + ' работают';
    const rows = [
      sysRow(bot.connected ? 'on' : 'bad', 'Бот', botLine),
      sysRow(s.accounts.online ? 'on' : 'bad', 'Аккаунты', s.accounts.online + ' из ' + s.accounts.configured + ' онлайн'),
      sysRow(s.owed_ping_cards ? 'warn' : 'on', 'Карточки владельцу',
        s.owed_ping_cards ? 'не доставлено ' + s.owed_ping_cards : 'все доставлены'),
      sysRow(badJobs.length ? 'bad' : 'on', 'Фоновые задачи', jobsLine),
      sysRow(s.scan.error ? 'warn' : (s.scan.running ? 'warn' : 'on'), 'Скан', scanLine(s.scan)),
    ];
    let html = '<div class="panel hero ' + (ok ? 'good' : 'warn') + '"><div class="lamp">' + icon(ok ? 'check' : 'alert', 22) + '</div>'
      + '<div style="flex:1"><div class="h">' + (ok ? 'Всё работает' : 'Есть что проверить') + '</div>'
      + '<div class="s">версия ' + esc(s.version || '—') + ' · работает ' + ago(s.uptime_seconds || 0) + '</div></div></div>'
      + '<div class="list" style="margin-top:12px">' + rows.join('') + '</div>';

    html += '<div class="section-label">База и диск</div><div class="kv">'
      + sysCell('База', s.db_size_mb + ' МБ', s.db_freelist_pct != null ? 'пустого ' + Math.round(s.db_freelist_pct) + '%' : '')
      + sysCell('Свободно на диске', s.disk_free_mb != null ? fmtInt(s.disk_free_mb / 1024) + ' ГБ' : '—', '')
      + sysCell('Последний бэкап', s.last_backup ? fmtTime(s.last_backup.at) : 'нет', s.backups_mb ? 'всего ' + s.backups_mb + ' МБ' : '')
      + sysCell('Уборка', s.maintenance_running ? 'идёт…' : (s.last_maintenance_at ? fmtTime(s.last_maintenance_at) : 'ещё не было'), '')
      + '</div>';

    html += '<div class="section-label">Действия</div><div class="tiles">'
      + '<button class="btn" data-act="scan"' + (s.scan.running ? ' disabled' : '') + '>' + icon('scan', 18) + (s.scan.running ? 'Скан идёт' : 'Скан сейчас') + '</button>'
      + '<button class="btn" data-act="clean"' + (s.maintenance_running ? ' disabled' : '') + '>' + icon('spark', 18) + 'Уборка</button>'
      + '<button class="btn" data-act="backup">' + icon('lock', 18) + 'Бэкап</button>'
      + '<button class="btn" data-act="resend"' + (s.owed_ping_cards ? '' : ' disabled') + '>' + icon('send', 18) + 'Дослать</button>'
      + '</div>';

    if (s.journal && s.journal.length) {
      html += '<div class="section-label">Действия в панели</div><div class="list">'
        + s.journal.map((j) => item({
          lead: icon(j.who === 'вы' ? 'user' : 'users', 15), leadCls: j.who === 'вы' ? '' : 'on',
          title: esc(journalWhat(j)), desc: esc(j.who), end: '<div class="d num">' + esc(fmtTime(j.at)) + '</div>',
        })).join('') + '</div>';
    }
    if (s.events.length) {
      html += '<div class="section-label">Предупреждения и ошибки</div><div class="list">'
        + s.events.map((e) => item({
          lead: icon(String(e.level).toUpperCase() === 'ERROR' ? 'flame' : 'alert', 15),
          leadCls: String(e.level).toUpperCase() === 'ERROR' ? 'bad' : 'warn',
          title: esc(e.source || '—'), desc: esc(e.message), wrap: true,
          end: '<div class="d num">' + esc(fmtTime(e.at)) + '</div>',
        })).join('') + '</div>';
    }
    return html;
  },

  actions: {
    scan: async () => {
      const res = await api('/api/app/system/scan', {});
      toast(res.started ? 'Скан запущен' : 'Скан уже идёт');
      return App.render({ quiet: true });
    },
    clean: async () => {
      const res = await api('/api/app/system/maintenance', {});
      toast(res.started ? 'Уборка запущена — итог появится здесь' : 'Уборка уже идёт');
      return App.render({ quiet: true });
    },
    backup: async (el) => {
      el.disabled = true;
      el.innerHTML = icon('refresh', 18) + 'Копирую…';
      const res = await api('/api/app/system/backup', {});
      toast('Копия: ' + res.name);
      return App.render({ quiet: true });
    },
    resend: async () => {
      const res = await api('/api/app/system/resend', {});
      toast('Дослано: ' + (res.sent || 0) + (res.failed ? ' · не ушло: ' + res.failed : ''), Boolean(res.failed));
      return App.render({ quiet: true });
    },
  },
});

// "/api/app/pings/5/status" + {status: "claimed"} → «статус #5: claimed».
const JOURNAL_WORDS = [
  [/^\/api\/app\/pings\/(\d+)\/status$/, (m, d) => 'статус #' + m[1] + (d.status ? ': ' + d.status : '')],
  [/^\/api\/app\/debts\/claim$/, (m, d) => (d.status || 'claimed') + ' · ' + ((d.ids || []).length) + ' шт.'],
  [/^\/api\/app\/undo$/, () => 'вернул как было'],
  [/^\/api\/app\/giveaways\/(\d+)\/engagement$/, (m, d) => (d.action === 'joined' ? 'участвую' : 'пропустил') + ' #' + m[1]],
  [/^\/api\/app\/giveaways\/(\d+)\/(analyze|profile|skip)$/, (m) => ({ analyze: 'разбор', profile: 'профиль канала', skip: 'не участвуем' })[m[2]] + ' #' + m[1]],
  [/^\/api\/app\/cleanup\/leave$/, (m, d) => 'выход из каналов: ' + ((d.chat_ids || []).length)],
  [/^\/api\/app\/login\//, () => 'вход в аккаунт'],
  [/^\/api\/app\/accounts\/([^/]+)\/(\w+)$/, (m) => m[2] + ' · ' + decodeURIComponent(m[1])],
  [/^\/api\/app\/keys/, () => 'ключи'],
  [/^\/api\/app\/members\//, () => 'доступ участника'],
  [/^\/api\/app\/settings/, () => 'настройки'],
  [/^\/api\/app\/feed/, () => 'лента'],
  [/^\/api\/app\/prefs$/, () => 'свои уведомления'],
  [/^\/api\/app\/system\/(\w+)$/, (m) => ({ scan: 'скан', maintenance: 'уборка', backup: 'бэкап', resend: 'досылка карточек' })[m[1]] || m[1]],
];

function journalWhat(j) {
  for (const [re, word] of JOURNAL_WORDS) {
    const m = re.exec(j.path);
    if (m) return word(m, j.details || {});
  }
  return j.path;
}

function sysRow(tone, title, text) {
  return item({ lead: '<span class="dot ' + tone + '"></span>', title: esc(title), desc: esc(text), wrap: true });
}

function sysCell(k, v, note) {
  return '<div class="cell"><div class="k">' + esc(k) + '</div><div class="v sm">' + esc(v)
    + (note ? '<small class="of">' + esc(note) + '</small>' : '') + '</div></div>';
}

function scanLine(scan) {
  if (scan.running) {
    const p = scan.progress || {};
    return 'идёт' + (p.total_accounts ? ' · ' + (p.processed_accounts || 0) + '/' + p.total_accounts : '')
      + (p.current_account ? ' · ' + p.current_account : '');
  }
  if (scan.error) return 'ошибка: ' + String(scan.error).slice(0, 120);
  return scan.last_finished_at ? 'последний ' + fmtTime(scan.last_finished_at) + (scan.last_status ? ' · ' + scan.last_status : '') : 'ещё не было';
}

// 90 → «1 мин», 7200 → «2 ч», 200000 → «2 дн».
function ago(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 60) return s + ' с';
  if (s < 3600) return Math.round(s / 60) + ' мин';
  if (s < 86400) return Math.round(s / 3600) + ' ч';
  return Math.round(s / 86400) + ' дн';
}
