// "Ещё": the sections that do not earn a tab of their own.
'use strict';

App.register('more', {
  tab: 'more',
  title: 'Ещё',

  render() {
    const s = App.sections;
    const rows = [];
    if (s.accounts) rows.push(item({ act: 'go', data: { to: 'triage' }, lead: icon('check', 17), leadCls: 'on', title: 'Разбор', desc: 'Всё, что ждёт решения, по одной карточке' }));
    if (s.analytics) rows.push(item({ act: 'go', data: { to: 'analytics' }, lead: icon('chart', 17), leadCls: 'on', title: 'Статистика', desc: 'Дни, часы, чаты и скорость' }));
    if (s.prefs) rows.push(item({ act: 'go', data: { to: 'prefs' }, lead: icon('bell', 17), leadCls: 'on', title: 'Уведомления', desc: 'Что присылать и когда удалять' }));
    if (s.accounts) rows.push(item({ act: 'go', data: { to: 'accounts' }, lead: icon('users', 17), leadCls: 'on', title: 'Аккаунты', desc: 'Статус сессий, подключение по номеру' }));
    if (s.debts) rows.push(item({ act: 'go', data: { to: 'debts' }, lead: icon('wallet', 17), leadCls: 'on', title: 'Долги', desc: 'Выигранное, но не забранное' }));
    if (s.accounts) {
      rows.push(item({ act: 'go', data: { to: 'system' }, lead: icon('pulse', 17), leadCls: 'on', title: 'Система', desc: 'Бот, джобы, база, бэкап, ошибки' }));
      rows.push(item({ act: 'go', data: { to: 'keys' }, lead: icon('lock', 17), leadCls: 'on', title: 'Ключи и люди', desc: 'Права, срок, расписание доступа' }));
      rows.push(item({ act: 'go', data: { to: 'settings' }, lead: icon('grid', 17), leadCls: 'on', title: 'Настройки', desc: 'Уведомления, работа скана, игнор-чаты' }));
    }
    if (s.accounts) rows.push(item({ act: 'go', data: { to: 'cleanup' }, lead: icon('power', 17), leadCls: 'on', title: 'Уборка каналов', desc: 'Молчащие каналы: выйти всеми аккаунтами' }));
    if (s.salary) rows.push(item({ act: 'go', data: { to: 'salary' }, lead: icon('cash', 17), leadCls: 'on', title: 'Зарплата', desc: 'Книга «Учет розыгрышей»' }));
    let html = rows.length ? '<div class="list">' + rows.join('') + '</div>' : emptyView('grid', 'Здесь пусто', '');
    if (s.accounts) {
      html += '<div class="section-label">Быстро</div><div class="stack">'
        + '<button class="btn" data-act="go" data-to="login">' + icon('plus', 18) + 'Подключить аккаунт</button></div>';
    }
    // Telegram 8.0+: a shortcut on the phone's home screen that opens the panel.
    if (tg && tg.addToHomeScreen && tg.isVersionAtLeast && tg.isVersionAtLeast('8.0')) {
      html += '<div style="margin-top:12px"><button class="btn ghost" data-act="home-screen">' + icon('phone', 18)
        + 'На главный экран телефона</button></div>';
    }
    html += '<div style="margin-top:22px"><button class="btn ghost" data-act="app-close">' + icon('message', 18) + 'Вернуться в бота</button></div>';
    return html;
  },

  actions: {
    go: (el) => App.go(el.dataset.to),
    'home-screen': () => { try { tg.addToHomeScreen(); } catch (e) { toast('Этот Telegram так не умеет', true); } },
  },
});
