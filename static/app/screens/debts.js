// Debts: wins not yet claimed. Tap selects, the dock claims the selection.
'use strict';

const Debts = { seg: 'a', selected: new Set(), open: null };

App.register('debts', {
  tab: 'more',
  title: 'Долги',

  async render() {
    const data = await api('/api/app/debts?seg=' + Debts.seg);
    const ids = new Set(data.items.map((r) => r.id));
    Debts.selected.forEach((id) => { if (!ids.has(id)) Debts.selected.delete(id); });
    const stats = data.stats || {};
    let html = '<div class="kv">'
      + cell('В очереди', fmtInt(data.count)) + cell('Оценка', data.value ? fmtUsd(data.value) : '—')
      + cell('Критичных', fmtInt(stats.critical)) + cell('Новых', fmtInt(stats.new))
      + '</div><div style="margin-top:12px">'
      + seg(data.segments.map((s) => [s.code, s.label]), data.segment, 'seg') + '</div>';
    if (!data.items.length) {
      return html + emptyView('wallet', 'Долгов нет', 'Под этот сегмент ничего не попало.');
    }
    html += '<div class="section-label">Нажмите, чтобы отметить'
      + (Debts.selected.size ? '<span class="more" data-act="clear">Снять ' + Debts.selected.size + '</span>' : '<span class="more" data-act="all">Все</span>')
      + '</div><div class="list">'
      + data.items.map((r) => {
        const sel = Debts.selected.has(r.id);
        const hot = r.id === data.hottest;
        r.hot = hot;
        const look = leadLook(r, sel);
        return item({
          act: 'toggle', data: { id: r.id }, chev: false, cls: sel ? 'selected' : '',
          lead: icon(look[0], 16), leadCls: look[1],
          title: esc(r.chat),
          desc: (hot ? '<span class="badge warn">горячий</span> ' : '') + esc(r.text.replace(/\s+/g, ' ').slice(0, 90)),
          end: '<div class="t num">' + (r.value ? esc(fmtUsd(r.value)) : '') + '</div>'
            + '<div class="d num">' + esc(fmtTime(r.detected_at)) + '</div>',
        });
      }).join('') + '</div>';
    Debts.items = data.items;
    return html + '<div style="height:70px"></div>';
  },

  mounted() {
    dock();
    return () => { const d = document.querySelector('.action-dock'); if (d) d.remove(); };
  },

  actions: {
    seg: (el) => { Debts.seg = el.dataset.v; return App.render({ quiet: true }); },
    // Selecting is local: repaint the row and the dock, never refetch the board.
    toggle: (el) => {
      const id = Number(el.dataset.id);
      const on = !Debts.selected.has(id);
      if (on) Debts.selected.add(id); else Debts.selected.delete(id);
      el.classList.toggle('selected', on);
      const lead = el.querySelector('.lead');
      const row = (Debts.items || []).find((r) => r.id === id);
      if (lead && row) {
        const look = leadLook(row, on);
        lead.className = 'lead ' + look[1];
        lead.innerHTML = icon(look[0], 16);
      }
      const more = document.querySelector('.section-label .more');
      if (more) {
        const n = Debts.selected.size;
        more.dataset.act = n ? 'clear' : 'all';
        more.textContent = n ? 'Снять ' + n : 'Все';
      }
      haptic('select');
      dock();
    },
    all: () => { (Debts.items || []).forEach((r) => Debts.selected.add(r.id)); return App.render({ quiet: true }); },
    clear: () => { Debts.selected.clear(); return App.render({ quiet: true }); },
    peek: () => {
      const id = Array.from(Debts.selected)[0];
      const r = (Debts.items || []).find((x) => x.id === id);
      if (!r) return;
      Sheet.open(r.chat, '<div class="text-block">' + esc(r.text || '—') + '</div>'
        + (r.link ? '<div style="margin-top:10px"><button class="btn" data-act="link" data-url="' + esc(r.link) + '">'
          + icon('external', 18) + 'Открыть пост</button></div>' : ''));
    },
    claim: (el) => settle(el.dataset.v),
  },
});

// [icon, lead class] for a debt row in either selection state.
function leadLook(row, selected) {
  if (selected) return ['check', ''];
  return [row.hot ? 'flame' : 'trophy', row.score >= 90 ? 'bad' : 'on'];
}

function cell(k, v) {
  return '<div class="cell"><div class="k">' + esc(k) + '</div><div class="v">' + v + '</div></div>';
}

function dock() {
  const old = document.querySelector('.action-dock');
  if (old) old.remove();
  const n = Debts.selected.size;
  if (!n) return;
  const el = document.createElement('div');
  el.className = 'action-dock';
  el.innerHTML = (n === 1 ? '<button class="btn small" data-act="peek" aria-label="Пост">' + icon('message', 18) + '</button>' : '')
    + '<button class="btn primary" data-act="claim" data-v="claimed">' + icon('check', 18) + 'Забрал · ' + n + '</button>'
    + '<button class="btn danger small" data-act="claim" data-v="scam">' + icon('shield', 18) + '</button>';
  document.body.appendChild(el);
}

async function settle(status) {
  const ids = Array.from(Debts.selected);
  if (!ids.length) return;
  const word = status === 'claimed' ? 'забранными' : 'скамом';
  if (!(await confirmBox('Отметить ' + ids.length + ' ' + plural(ids.length, 'запись', 'записи', 'записей') + ' ' + word + '?'))) return;
  const res = await api('/api/app/debts/claim', { ids: ids, status: status });
  Debts.selected.clear();
  toast((status === 'claimed' ? 'Забрано: ' : 'Скам: ') + res.count);
  return App.render({ quiet: true });
}
