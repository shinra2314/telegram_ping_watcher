// «Уборка каналов»: channels silent for longer than the setting, across all
// accounts — the bot's gw:cl list, but with a checkbox per row and one
// «Выйти из N». Leaving is irreversible (a private channel needs a new
// invite), so the confirmation names every channel and account.
'use strict';

const Cleanup = { items: null, warning: '', picked: new Set() };

App.register('cleanup', {
  tab: 'more',
  title: 'Уборка каналов',
  reset: () => { Cleanup.items = null; },

  async render(params) {
    if (params.force || !Cleanup.items) {
      const data = await api('/api/app/cleanup' + (params.force ? '?force=true' : ''));
      Cleanup.items = data.items;
      Cleanup.warning = data.warning || '';
      Cleanup.picked = new Set(Array.from(Cleanup.picked).filter((id) => data.items.some((i) => i.chat_id === id)));
    }
    const items = Cleanup.items;
    App.sub.textContent = items.length + ' ' + plural(items.length, 'канал', 'канала', 'каналов');
    let html = '<div class="hint" style="margin:0 4px 10px">Сначала каналы без побед за 90 дней. '
      + icon('trophy', 13) + ' — канал уже платил: из такого выходить не стоит.</div>';
    if (Cleanup.warning) html += '<div class="locked">' + icon('alert', 15) + '<div>' + esc(Cleanup.warning) + '</div></div>';
    // With no account online nothing was checked — "all quiet" would be a lie.
    if (!items.length) return html + (Cleanup.warning ? '' : emptyView('check', 'Молчащих каналов нет', 'Все каналы писали недавно.'));
    html += '<div class="section-label">Отметьте, из каких выйти'
      + '<span class="more" data-act="' + (Cleanup.picked.size ? 'none' : 'safe') + '">'
      + (Cleanup.picked.size ? 'Снять ' + Cleanup.picked.size : 'Все без побед') + '</span></div>'
      + '<div class="list">' + items.map(cleanupRow).join('') + '</div><div style="height:70px"></div>';
    return html;
  },

  mounted() {
    cleanupDock();
    return () => { const d = document.querySelector('.action-dock'); if (d) d.remove(); };
  },

  actions: {
    toggle: (el) => {
      const id = Number(el.dataset.id);
      if (Cleanup.picked.has(id)) Cleanup.picked.delete(id); else Cleanup.picked.add(id);
      el.classList.toggle('selected', Cleanup.picked.has(id));
      const lead = el.querySelector('.lead');
      const row = Cleanup.items.find((i) => i.chat_id === id);
      if (lead && row) lead.outerHTML = cleanupLead(row);
      haptic('select');
      cleanupDock();
      const more = document.querySelector('.section-label .more');
      if (more) {
        more.dataset.act = Cleanup.picked.size ? 'none' : 'safe';
        more.textContent = Cleanup.picked.size ? 'Снять ' + Cleanup.picked.size : 'Все без побед';
      }
    },
    safe: () => {
      Cleanup.items.filter((i) => !i.wins).slice(0, 20).forEach((i) => Cleanup.picked.add(i.chat_id));
      return App.render({ quiet: true });
    },
    none: () => { Cleanup.picked.clear(); return App.render({ quiet: true }); },
    leave: async () => {
      const chosen = Cleanup.items.filter((i) => Cleanup.picked.has(i.chat_id)).slice(0, 20);
      if (!chosen.length) return;
      const paid = chosen.filter((i) => i.wins).length;
      const names = chosen.slice(0, 6).map((i) => i.title).join(', ') + (chosen.length > 6 ? ' и ещё ' + (chosen.length - 6) : '');
      const text = 'Выйти из ' + chosen.length + ' ' + plural(chosen.length, 'канала', 'каналов', 'каналов') + ': ' + names
        + '? Выйдут все аккаунты, что в них сидят.' + (paid ? ' ' + paid + ' из них уже платили.' : '')
        + ' Вернуться в приватный канал можно только по новой ссылке.';
      if (!(await confirmBox(text))) return;
      const dock = document.querySelector('.action-dock .btn.danger');
      if (dock) { dock.disabled = true; dock.innerHTML = icon('refresh', 18) + 'Выхожу…'; }
      const res = await api('/api/app/cleanup/leave', { chat_ids: chosen.map((i) => i.chat_id) });
      const failed = res.results.filter((r) => !r.left);
      res.results.filter((r) => r.left).forEach((r) => Cleanup.picked.delete(r.chat_id));
      Cleanup.items = Cleanup.items.filter((i) => !res.results.some((r) => r.left && r.chat_id === i.chat_id));
      toast('Вышли из ' + res.left + (failed.length ? ' · не вышло: ' + failed.length + (failed[0].error ? ' (' + failed[0].error + ')' : '') : ''),
        Boolean(failed.length));
      return App.render({ quiet: true });
    },
  },
});

function cleanupLead(row) {
  const on = Cleanup.picked.has(row.chat_id);
  return '<div class="lead ' + (on ? '' : (row.wins ? 'on' : '')) + '">' + icon(on ? 'check' : (row.wins ? 'trophy' : 'power'), 16) + '</div>';
}

function cleanupRow(row) {
  const bits = [(row.inactive_days != null ? 'молчит ' + row.inactive_days + ' дн' : ''),
    row.accounts.length + ' ' + plural(row.accounts.length, 'аккаунт', 'аккаунта', 'аккаунтов')];
  if (row.wins) bits.push(row.wins + ' ' + plural(row.wins, 'победа', 'победы', 'побед'));
  return item({
    act: 'toggle', data: { id: row.chat_id }, chev: false, cls: Cleanup.picked.has(row.chat_id) ? 'selected' : '',
    lead: '', title: esc(row.title) + (row.username ? ' <span class="dim">@' + esc(row.username) + '</span>' : ''),
    desc: esc(bits.filter(Boolean).join(' · ')),
  }).replace('<div class="lead "></div>', cleanupLead(row));
}

function cleanupDock() {
  const old = document.querySelector('.action-dock');
  if (old) old.remove();
  const n = Cleanup.picked.size;
  if (!n) return;
  const el = document.createElement('div');
  el.className = 'action-dock';
  el.innerHTML = '<button class="btn danger" data-act="leave">' + icon('power', 18) + 'Выйти из ' + n + '</button>';
  document.body.appendChild(el);
}
