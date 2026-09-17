// "Ещё": the sections that do not earn a tab of their own.
'use strict';

App.register('more', {
  tab: 'more',
  title: 'Ещё',

  render() {
    const s = App.sections;
    const rows = [];
    if (s.analytics) rows.push(item({ act: 'go', data: { to: 'analytics' }, lead: icon('chart', 17), leadCls: 'on', title: 'Статистика', desc: 'Дни, часы, чаты и скорость' }));
    if (s.prefs) rows.push(item({ act: 'go', data: { to: 'prefs' }, lead: icon('bell', 17), leadCls: 'on', title: 'Уведомления', desc: 'Что присылать и когда удалять' }));
    if (s.accounts) rows.push(item({ act: 'go', data: { to: 'accounts' }, lead: icon('users', 17), leadCls: 'on', title: 'Аккаунты', desc: 'Статус сессий, подключение по номеру' }));
    if (s.debts) rows.push(item({ act: 'go', data: { to: 'debts' }, lead: icon('wallet', 17), leadCls: 'on', title: 'Долги', desc: 'Выигранное, но не забранное' }));
    if (s.salary) rows.push(item({ act: 'go', data: { to: 'salary' }, lead: icon('cash', 17), leadCls: 'on', title: 'Зарплата', desc: 'Книга «Учет розыгрышей»' }));
    let html = rows.length ? '<div class="list">' + rows.join('') + '</div>' : emptyView('grid', 'Здесь пусто', '');
    if (s.accounts) {
      html += '<div class="section-label">Быстро</div><div class="stack">'
        + '<button class="btn" data-act="go" data-to="login">' + icon('plus', 18) + 'Подключить аккаунт</button></div>';
    }
    html += '<div style="margin-top:22px"><button class="btn ghost" data-act="app-close">' + icon('message', 18) + 'Вернуться в бота</button></div>';
    return html;
  },

  actions: {
    go: (el) => App.go(el.dataset.to),
  },
});
