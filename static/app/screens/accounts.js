// Accounts: status and control for every Telegram session, and the login
// wizard (phone -> code -> cloud password). The wizard exists only here: a
// code typed into a Telegram chat is voided by Telegram, a page field is not.
'use strict';

const ACCOUNT_STATUS = {
  online: ['онлайн', 'on'],
  connecting: ['подключается', 'warn'],
  auth_requesting: ['запрос кода', 'warn'],
  auth_code_sent: ['ждёт код', 'warn'],
  rate_limited: ['лимит Telegram', 'bad'],
  auth_error: ['ошибка входа', 'bad'],
  auth_key_duplicated: ['сессия сброшена', 'bad'],
  offline: ['отключён', ''],
  known: ['не запущен', ''],
};

App.register('accounts', {
  tab: 'more',
  title: 'Аккаунты',

  async render() {
    const data = await api('/api/app/accounts');
    let html = '<div class="kv">'
      + '<div class="cell"><div class="k">Онлайн</div><div class="v">' + data.online + ' <span class="dim" style="font-size:14px">/ ' + data.total + '</span></div></div>'
      + '<div class="cell"><div class="k">Упоминаний всего</div><div class="v">'
      + fmtInt(data.items.reduce((s, a) => s + (a.pings_total || 0), 0)) + '</div></div></div>'
      + '<div class="stack" style="margin-top:12px">'
      + '<button class="btn primary" data-act="login">' + icon('plus', 18) + 'Подключить аккаунт</button></div>';

    if (!data.items.length) {
      return html + emptyView('users', 'Сессий нет', 'Подключите первый аккаунт кнопкой выше.');
    }
    html += '<div class="section-label">Сессии</div><div class="list">'
      + data.items.map((a) => {
        const st = ACCOUNT_STATUS[a.status] || [a.status || '?', ''];
        const online = a.status === 'online';
        const bits = [st[0]];
        if (a.username) bits.push('@' + a.username);
        if (a.wins_30d != null) bits.push(a.wins_30d + ' ' + plural(a.wins_30d, 'победа', 'победы', 'побед') + ' за 30 дн');
        else if (a.wins) bits.push(a.wins + ' ' + plural(a.wins, 'победа', 'победы', 'побед'));
        // A problem (banned, stuck, silent for 12 h) outranks the raw error text.
        const trouble = a.problem || (a.last_error ? String(a.last_error).slice(0, 120) : '');
        const extra = [];
        if (trouble) extra.push('<span class="down">' + esc(trouble) + '</span>');
        if (a.spam_check) extra.push('<span class="dim">' + icon('shield', 12) + ' ' + esc(a.spam_check) + '</span>');
        return item({
          act: 'account', data: { name: a.session_name, online: online ? 1 : 0 },
          lead: '<span class="dot ' + (a.problem ? 'bad' : (st[1] || '')) + '"></span>',
          title: esc(a.session_name) + (a.cooldown ? ' <span class="badge warn">пауза</span>' : ''),
          desc: esc(bits.join(' · ')) + (extra.length ? '<br>' + extra.join('<br>') : ''),
          wrap: Boolean(extra.length),
          end: a.last_ping_at ? '<div class="d num">' + esc(fmtTime(a.last_ping_at)) + '</div>' : '',
        });
      }).join('') + '</div>'
      + '<div style="margin-top:14px"><button class="btn ghost" data-act="restart">' + icon('refresh', 18)
      + 'Перезапустить мониторинг</button></div>';
    return html;
  },

  actions: {
    login: () => App.go('login'),
    account: (el) => {
      const name = el.dataset.name;
      const online = el.dataset.online === '1';
      Sheet.open(name, '<div class="stack">'
        + (online
          ? '<button class="btn danger" data-act="disconnect" data-name="' + esc(name) + '">' + icon('power', 18) + 'Отключить</button>'
          : '<button class="btn primary" data-act="reconnect" data-name="' + esc(name) + '">' + icon('plug', 18) + 'Подключить</button>')
        + (online ? '<button class="btn" data-act="spam" data-name="' + esc(name) + '">' + icon('shield', 18) + 'Проверить спам-блок</button>' : '')
        + '<button class="btn ghost" data-act="relogin" data-name="' + esc(name) + '">' + icon('lock', 18) + 'Войти заново по номеру</button>'
        + '</div>');
    },
    spam: async (el) => {
      Sheet.close();
      const name = el.dataset.name;
      toast('Спрашиваю @SpamBot от ' + name + '…');
      const res = await api('/api/app/accounts/' + encodeURIComponent(name) + '/spam', {});
      Sheet.open('@SpamBot · ' + name, '<div class="panel pad" style="font-size:14px">'
        + '<span class="badge ' + (res.free ? 'good' : 'bad') + '">' + esc(res.summary) + '</span>'
        + (res.text ? '<div class="dim" style="margin-top:10px;white-space:pre-wrap;font-size:13px">' + esc(res.text) + '</div>' : '')
        + '</div>');
      return App.render({ quiet: true });
    },
    disconnect: async (el) => {
      Sheet.close();
      if (!(await confirmBox('Отключить ' + el.dataset.name + '? Мониторинг этого аккаунта остановится.'))) return;
      await api('/api/app/accounts/' + encodeURIComponent(el.dataset.name) + '/disconnect', {});
      toast('Отключён: ' + el.dataset.name);
      return App.render({ quiet: true });
    },
    reconnect: async (el) => {
      Sheet.close();
      const res = await api('/api/app/accounts/' + encodeURIComponent(el.dataset.name) + '/reconnect', {});
      toast(res.message || 'Подключаю…');
      setTimeout(() => { if (App.current().name === 'accounts') App.render({ quiet: true }); }, 4000);
      return App.render({ quiet: true });
    },
    relogin: (el) => { Sheet.close(); return App.go('login', { session: el.dataset.name }); },
    restart: async () => {
      if (!(await confirmBox('Переподключить все аккаунты? Идущий скан прервётся.'))) return;
      const res = await api('/api/app/accounts/restart', {});
      toast('Перезапущено: ' + res.restarted);
      setTimeout(() => { if (App.current().name === 'accounts') App.render({ quiet: true }); }, 5000);
    },
  },
});

// ── Login wizard ──────────────────────────────────────────────────────
const Login = { step: 'phone', phone: '', session: '', sms: false, delivery: '', message: '', user: '', sentAt: 0, busy: false };
const RESEND_SECONDS = 60;

App.register('login', {
  tab: 'more',
  title: 'Подключение',

  render(params) {
    if (params.session !== undefined && Login.step === 'phone' && !Login.session) Login.session = params.session;
    const idx = { phone: 0, code: 1, password: 2, done: 3 }[Login.step];
    const dots = '<div class="step-dots">' + [0, 1, 2].map((i) => '<i class="' + (i <= idx ? 'on' : '') + '"></i>').join('') + '</div>';
    if (Login.step === 'phone') return dots + phoneStep();
    if (Login.step === 'code') return dots + codeStep();
    if (Login.step === 'password') return dots + passwordStep();
    return doneStep();
  },

  mounted() {
    const focus = document.querySelector('[data-autofocus]');
    if (focus) setTimeout(() => focus.focus(), 60);
    if (Login.step !== 'code') return null;
    const timer = setInterval(updateResend, 1000);
    updateResend();
    return () => clearInterval(timer);
  },

  inputs: {
    code: (el) => {
      el.value = el.value.replace(/\D/g, '').slice(0, 8);
      // Telegram codes are five digits; submit on the fifth, like the apps do.
      if (el.value.length === 5 && !Login.busy) App.screens.login.actions.signin();
    },
  },

  actions: {
    sms: () => { Login.sms = !Login.sms; document.getElementById('sms-switch').classList.toggle('on', Login.sms); },
    'send-code': async () => {
      const phone = document.getElementById('phone').value;
      const session = document.getElementById('session').value;
      await busy('send-code', async () => {
        const res = await api('/api/app/login/code', { phone: phone, session_name: session, force_sms: Login.sms });
        if (res.status !== 'code_sent') throw new Error(res.message);
        Object.assign(Login, { step: 'code', phone: phone, session: res.session_name, delivery: res.delivery, message: res.message, sentAt: Date.now() });
      });
      return App.render({ quiet: true });
    },
    signin: async () => {
      const input = document.getElementById('code');
      const code = input ? input.value : '';
      let next = null;
      await busy('signin', async () => {
        const res = await api('/api/app/login/sign-in', { phone: Login.phone, code: code });
        if (res.status === 'password_needed') next = 'password';
        else if (res.status === 'ok') { next = 'done'; Login.user = res.user; }
        else {
          if (input) { input.value = ''; input.focus(); }
          throw new Error(res.message);
        }
      });
      if (next) { Login.step = next; return App.render({ quiet: true }); }
    },
    password: async () => {
      const input = document.getElementById('password');
      const secret = input ? input.value : '';
      if (input) input.value = '';   // never leave the password sitting in the DOM
      let ok = false;
      await busy('password', async () => {
        const res = await api('/api/app/login/password', { phone: Login.phone, password: secret });
        if (res.status !== 'ok') { if (input) input.focus(); throw new Error(res.message); }
        Login.user = res.user;
        ok = true;
      });
      if (ok) { Login.step = 'done'; return App.render({ quiet: true }); }
    },
    resend: async () => {
      Login.step = 'phone';
      await App.render({ quiet: true });
      return App.screens.login.actions['send-code']();
    },
    cancel: async () => {
      if (Login.phone && Login.step !== 'done') {
        try { await api('/api/app/login/cancel', { phone: Login.phone }); } catch (e) { /* already gone */ }
      }
      resetLogin();
      return App.back();
    },
    finish: () => { resetLogin(); App.stack = [{ name: 'more', params: {} }]; return App.go('accounts'); },
    another: () => { resetLogin(); return App.render({ quiet: true }); },
  },
});

function resetLogin() {
  Object.assign(Login, { step: 'phone', phone: '', session: '', sms: false, delivery: '', message: '', user: '', sentAt: 0, busy: false });
}

async function busy(act, fn) {
  if (Login.busy) return;
  Login.busy = true;
  const btn = document.querySelector('[data-act="' + act + '"]');
  if (btn) { btn.disabled = true; btn.dataset.label = btn.innerHTML; btn.innerHTML = icon('refresh', 18) + 'Секунду…'; btn.classList.add('spin'); }
  try {
    await fn();
    haptic('ok');
  } catch (err) {
    toast(err.message || 'Ошибка', true);
  } finally {
    Login.busy = false;
    if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = btn.dataset.label; }
  }
}

function phoneStep() {
  return '<h2 class="title">Подключить аккаунт</h2>'
    + '<p class="hint" style="margin-bottom:16px">Telegram пришлёт код в приложение этого аккаунта. Код вводится здесь — '
    + 'в чат бота его отправлять нельзя, Telegram его аннулирует.</p>'
    + '<div class="stack">'
    + '<div class="field"><label for="phone">Номер телефона</label>'
    + '<input class="input" id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+380 67 123 45 67"'
    + ' value="' + esc(Login.phone) + '" data-enter="send-code" data-autofocus></div>'
    + '<div class="field"><label for="session">Имя сессии <span class="faint">— необязательно</span></label>'
    + '<input class="input" id="session" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="по номеру: session_380…"'
    + ' value="' + esc(Login.session) + '" data-enter="send-code" style="font-size:15px"></div>'
    + '<div class="toggle" data-act="sms"><div><div style="font-weight:550">Прислать SMS</div>'
    + '<div class="hint" style="margin:0">если приложение на том номере недоступно</div></div>'
    + '<span class="switch' + (Login.sms ? ' on' : '') + '" id="sms-switch"></span></div>'
    + '<button class="btn primary" data-act="send-code" style="margin-top:6px">' + icon('send', 18) + 'Получить код</button>'
    + '<button class="btn ghost" data-act="cancel">Отмена</button>'
    + '</div>';
}

function codeStep() {
  return '<h2 class="title">Код из Telegram</h2>'
    + '<p class="hint" style="margin-bottom:16px">' + esc(Login.message) + '<br>Номер: <span class="num">' + esc(Login.phone)
    + '</span> · сессия <span class="mono">' + esc(Login.session) + '</span></p>'
    + '<div class="stack">'
    + '<input class="input code" id="code" data-act="code" inputmode="numeric" autocomplete="one-time-code" maxlength="8"'
    + ' placeholder="•••••" data-enter="signin" data-autofocus>'
    + '<button class="btn primary" data-act="signin">' + icon('check', 18) + 'Войти</button>'
    + '<div style="display:flex;justify-content:space-between;align-items:center;padding:0 4px">'
    + '<button class="link" id="resend" data-act="resend" disabled>Запросить заново</button>'
    + '<button class="link" data-act="cancel" style="color:var(--dim)">Отмена</button></div>'
    + '</div>';
}

function updateResend() {
  const btn = document.getElementById('resend');
  if (!btn) return;
  const left = RESEND_SECONDS - Math.floor((Date.now() - Login.sentAt) / 1000);
  btn.disabled = left > 0;
  btn.textContent = left > 0 ? 'Запросить заново через ' + left + ' с' : 'Запросить заново';
}

function passwordStep() {
  return '<h2 class="title">Облачный пароль</h2>'
    + '<p class="hint" style="margin-bottom:16px">На аккаунте включена двухэтапная проверка. Пароль уходит только в Telegram '
    + 'и нигде не сохраняется.</p>'
    + '<div class="stack">'
    + '<input class="input" id="password" type="password" autocomplete="current-password" placeholder="Пароль 2FA"'
    + ' data-enter="password" data-autofocus>'
    + '<button class="btn primary" data-act="password">' + icon('lock', 18) + 'Войти</button>'
    + '<button class="btn ghost" data-act="cancel">Отмена</button>'
    + '</div>';
}

function doneStep() {
  return '<div style="text-align:center">'
    + '<div class="big-ok">' + icon('check', 36) + '</div>'
    + '<h2 class="title" style="margin-bottom:6px">Аккаунт подключён</h2>'
    + '<p class="dim" style="margin:0 0 20px">' + esc(Login.user) + ' · сессия <span class="mono">' + esc(Login.session)
    + '</span><br>Мониторинг запускается — через несколько секунд он появится онлайн.</p></div>'
    + '<div class="stack"><button class="btn primary" data-act="finish">' + icon('users', 18) + 'К аккаунтам</button>'
    + '<button class="btn ghost" data-act="another">Подключить ещё один</button></div>';
}
