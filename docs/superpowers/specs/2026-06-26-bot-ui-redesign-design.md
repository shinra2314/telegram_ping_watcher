# Bot UI Redesign — Full Re-IA

**Date:** 2026-06-26
**Status:** Approved (design), pending implementation plan
**Area:** Telegram bot interface (`src/pulse_desk/bot_service.py` → new `src/pulse_desk/bot/` package)

## Goal

Full rethink of the Telegram bot UX, serving owner (admin) and guest (viewer)
equally. Three threads, all in scope:

- **Visual polish** — one consistent visual language across every screen.
- **Navigation** — a real 2-level information architecture with consistent
  back/refresh chrome, edited in place (single-message SPA).
- **New features** — live dashboard home, clickable lists with drilldown,
  admin members/access management via buttons.

This redesign also splits the 1490-line single-function `bot_service.py` into a
testable `bot/` package — a long-standing roadmap item ("split bot_service.py +
router tests").

## Non-goals / explicitly preserved

- **Giveaway detection + board stays.** Only the *acting* on giveaways
  (auto-join/skip) is removed (see Phase 6). The waiting/to_claim/urgent
  counters and candidate detection remain — that is monitoring value.
- **`access_control.py`** (pure schedule resolution) is not touched. It is
  already pure and unit-tested and remains the source of truth for `bot_role`.
- **`work` / `cron` access windows stay CLI-only.** No inline schedule builder.
- **Notification/digest pipeline (`bot_notify.py`) logic** is not touched; only
  the inline buttons it emits are re-stitched to the new callback scheme and
  lose the join button.

## 1. Navigation map & home dashboard

Telegram has no real tabs, so "tabs" = top-level sections you drill into and
back out of. New 2-level IA, single message edited in place (SPA). Slash
commands open the same SPA.

**Home = live dashboard card** (replaces the bare one-line caption). Five
counters, all from existing data sources (`build_analytics`, `get_giveaway_board`
stats, `state.accounts_state`, `state.last_scan_*`) — no new DB work:

```
🛰 PULSE DESK · 👑 владелец
━━━━━━━━━━━━━━━
🆕 Новых пингов:  12      🎁 Срочных: 3
🛰 Аккаунты: 4/4 online   💸 Чеки: 2 свежих
🔄 Скан: 06-26 14:02 · ok
━━━━━━━━━━━━━━━
        [📡 Мониторинг] [🎁 Розыгрыши]
        [📊 Сводка]     [🔔 Уведомления]
        [⚙️ Управление]  ← только владелец
        [🔄 Обновить]   [❓ Помощь]
```

Five top-level sections:

| Section | Contains | Role |
|---|---|---|
| 📡 Мониторинг | Recent feed + filter `[Все][Упоминания][Чеки][Победы]` + `[🔎 Поиск]`; item → detail card | all |
| 🎁 Розыгрыши | Board summary + urgent list; item → candidate detail (read-only) | all |
| 📊 Сводка | Stats + Market + System status combined in one read-only card | all |
| 🔔 Уведомления | Personal prefs (member) / notification + digest settings (admin) | all |
| ⚙️ Управление | Настройки · Ключи · Люди · Доступ · Скан · Логи · Рестарт | admin |

Every non-home screen carries the same footer chrome: `[⬅️ Домой] [🔄 Обновить]`
plus context actions. Breadcrumb in the header subtitle
(`Домой › Мониторинг › Чеки`).

## 2. Visual language & chrome components

Shared vocabulary, reused on every screen. Pure functions in `bot/chrome.py`
(no Telethon event dependency → unit-testable).

```
header(icon, title, crumb)  → "📡 **МОНИТОРИНГ**\n__Домой › Мониторинг__\n━━━━━━━━━━━━━━━"
kv(icon, label, value)      → "🆕 Новых: `12`"
dot(status)                 → 🟢 / 🔴 / 🟡
footer(role, *rows)         → assembles [⬅️ Домой][🔄 Обновить] + context buttons
empty(text)                 → "📭 __Пусто.__"
```

Style rules (fixed):

- Header title in **CAPS** (`МОНИТОРИНГ`) — reads as a section banner. Decided.
- Values always in `monospace` — numbers, dates, statuses.
- One leading emoji per line as an "icon", not a scatter of emoji.
- Status dots 🟢/🔴/🟡 used identically everywhere (online / offline / degraded).
- Dates in one format `MM-DD HH:MM` (existing `_fmt_dt`).
- Footer always `[⬅️ Домой] [🔄 Обновить]`; context buttons between.
- Empty states always via `empty()` — single look, no "Пусто/нет/—" drift.
- Existing `DIV = ━━━━━━━━━━━━━━━` kept as the divider.

**Refresh button** points at the same nav callback that renders the screen
(`nav:mon` re-renders Monitoring). No view-state stored — just a re-render.
Drilldown refresh targets the item callback (`mon:open:<id>`).

Example combined **Сводка** card:

```
📊 СВОДКА
Домой › Сводка
━━━━━━━━━━━━━━━
📨 Записей: 1240   🆕 Новых: 12   ⭐ Избр: 8
━━━━━━━━━━━━━━━
💹 Курсы
🟠 BTC `$98,420`   🔷 ETH `$3,510`
💎 TON `$5.230`    🟣 SOL `$182.4`
━━━━━━━━━━━━━━━
🛰 Система · v1.x
⏱ Uptime `12ч 30м`   💾 База `8.4 MB`
🛰 Аккаунты `4/4`    🔄 Скан `06-26 14:02 · ok`
[⬅️ Домой] [🔄 Обновить]
```

## 3. Clickable lists & drilldown

Lists become actionable. Today they are dead text and ping actions arrive only
via push notifications. New flow: list → item card → actions.

**Monitoring feed** — one item = one full-width button-row with a text label:

```
📡 МОНИТОРИНГ
Домой › Мониторинг
━━━━━━━━━━━━━━━
[🔥 14:02 @channel_x →]
[⚡ 13:40 @channel_y →]
[• 13:12 @channel_z →]
━━━━━━━━━━━━━━━
[Все][Упоминания][Чеки][Победы]   ← filter
[🔎 Поиск]
[⬅️ Домой] [🔄 Обновить]
```

Each item button = `mon:open:<id>` (callback carries the real ping id, not the
list position). Active filter marked (✅ / CAPS). Filter uses
`get_pings(chat_type=...)` — already supported.

Tap → **ping card** (shows full text, the feed truncates):

```
🔥 ПИНГ #842
Домой › Мониторинг › #842
━━━━━━━━━━━━━━━
📅 `06-26 14:02`  ·  @channel_x
🏷 critical  ·  🎁 giveaway
━━━━━━━━━━━━━━━
<full message text, no 160-char truncation>
🔗 https://t.me/channel_x/123
━━━━━━━━━━━━━━━
[⭐ В избранное] [✓ Прочитано]
[⬅️ Назад] [🔄 Обновить]
```

Actions reuse existing `toggle_favorite` / `mark_ping_read`. No giveaway
join/skip buttons (removed, Phase 6).

**Giveaways** — urgent items as button-rows → candidate detail card,
**read-only** (view + link, no act). Board summary counters stay.

## 4. Admin: members & access via buttons

`/access` is almost entirely CLI today. Bring frequent operations to buttons.

**Управление grid:**
```
⚙️ УПРАВЛЕНИЕ
Домой › Управление
━━━━━━━━━━━━━━━
[⚙️ Настройки] [🔑 Ключи]
[👥 Люди]      [⏰ Доступ]
[🔄 Скан]      [📜 Логи]
[♻️ Рестарт]   [⬅️ Домой]
```

**Люди** → member list, each = button-row `[👤 Имя (@ник) · 🟢 →]` → member card:
```
👤 ИВАН (@ivan)
Домой › Управление › Люди › Иван
━━━━━━━━━━━━━━━
🔑 ключ: friends   ·   🟢 активен
🕐 был: 06-26 13:50
⏰ доступ: 🟢 открыт
━━━━━━━━━━━━━━━
[🚫 Заблокировать] [⏰ Доступ →]
[⬅️ Назад] [🔄 Обновить]
```

**Доступ** (per member) — buttons for frequent ops; complex windows stay CLI:
```
⏰ ДОСТУП · ИВАН
Домой › Управление › Люди › Иван › Доступ
━━━━━━━━━━━━━━━
Сейчас: 🟢 открыт  ·  по умолч.: allow
📋 Окна:
#12 ✅ разрешает · ежедневно 09:00–18:00 (Europe/...)
━━━━━━━━━━━━━━━
[🔴 Закрыть] [🔴 На 2ч] [🟢 Открыть]
[🔴 До утра 08:00]
[🗑 Удалить окно…] [↩️ Отмена] [🧾 История]
[⬅️ Назад] [🔄 Обновить]
Сложные окна: /access <user> work … / cron …
```

Button presets for "close": indefinite, 1h, 2h, until 08:00 morning. Maps to
existing `create_disable_until_window` / `_open_member_access` / undo / audit.
`work` + `cron` windows stay CLI (no inline schedule builder).

**Ключи** → list with `[🗑 Отозвать]` + `[➕ Создать ключ]` (creates + shows the
invite link, same as `/newkey`).

## 5. Architecture — split `bot_service.py`

Everything is currently nested closures inside `init_bot()`. Renderers can't be
tested without Telethon, and `callback_handler` is a 200-line if/elif chain.

**New package `src/pulse_desk/bot/`:**

```
bot/
  chrome.py     — header/kv/dot/footer/empty. Pure, no Telethon event. Testable.
  views.py      — render_home/mon/giveaways/summary/... → (text, buttons).
                  Take data, return text + keyboard. No event I/O.
  keyboards.py  — inline keyboard assembly (Button), nav grids, footers.
  callbacks.py  — routing table {prefix → handler} + dispatcher (replaces if/elif).
  service.py    — init_bot: client start, handler registration, slash commands,
                  wiring to the dispatcher. Thin wiring layer.
  access.py     — bot_role / _access_decision / access gating helpers
                  (or kept in service.py — finalised in the plan).
```

`bot_service.py` stays as a thin re-export (`from .bot.service import init_bot`)
so external imports (`loops.py`, `main.py`) don't break.

**Callback data scheme** — structured, `:`-separated:
```
nav:home  nav:mon  nav:gw  nav:sum  nav:adm  nav:notif  nav:help
mon:filter:all|mentions|checks|wins   mon:open:<id>
ping:fav:<id>   ping:read:<id>
gw:open:<id>
adm:keys  adm:members  adm:access  adm:scan  adm:logs  adm:restart  adm:settings
mem:open:<id>  mem:block:<id>  mem:unblock:<id>
acc:show:<id>  acc:off:<id>  acc:off2h:<id>  acc:offmorning:<id>
acc:on:<id>  acc:del:<id>  acc:undo:<id>  acc:log:<id>
set:*   pf:*
```

Dispatcher: split on `:`, look up in the table, gate by prefix centrally
(admin-only prefixes checked once, not per branch). All within Telegram's 64-byte
callback limit.

**Backward compatibility:** push notifications already sitting in chats carry the
old callback data (`fav_42`, `read_…`, `hidebc_…`, `blockmember_…`,
`unblockmember_…`, `revokekey_…`, `accshow_/accoff_/accon_…`). The dispatcher
keeps legacy aliases for these prefixes → new handlers. `gconfirm_/gskip_` are
removed with the feature; a tap on an old one gets a soft "Действие больше
недоступно" reply, never silence.

**Tests:** `chrome.py` + `views.py` + `keyboards.py` are pure → unit tests on
structure (headers present, footer buttons present, filter handling, empty
states, role gating). `callbacks.py` — routing-table test. Closes the "router
tests" roadmap item.

## 6. Phasing, scope, risks

Each phase is independently mergeable; the bot works between phases.

1. **Refactor scaffold** — create `bot/`, move existing renderers/keyboards
   as-is into `views.py`/`keyboards.py`, `bot_service.py` → shim. No behavior
   change. Tests green before and after.
2. **Chrome + dispatcher** — `chrome.py`, `callbacks.py` routing table with the
   structured scheme + legacy aliases. Move navigation to edit-in-place (SPA).
3. **Dashboard home + Сводка** — live home card (5 counters), combined Сводка.
4. **Clickable lists + drilldown** — Monitoring feed (button-rows), ping card
   (full text + ⭐/read), filters; giveaways drilldown (read-only).
5. **Management via buttons** — Люди (member card), Доступ (close/open presets +
   undo/history), Ключи (create/revoke).
6. **Remove giveaway-join** — cut `giveaway_actions.py`,
   `confirm_safe_giveaway_join`, `gconfirm/gskip` buttons, related web endpoints,
   `DRY_RUN_GIVEAWAYS`. Exact file map produced in the plan. Detection + board
   remain.

**Preserved / untouched:** giveaway detection + board (counters); access
schedule resolution (`access_control.py`); `work`/`cron` CLI windows; the
notification/digest pipeline logic.

**Risks:**

- Legacy callbacks in already-sent messages → keep aliases, never go silent.
- 64-byte callback limit → scheme fits.
- `gconfirm` in old pushes after removal → soft "недоступно" reply.

**Verification:** unit tests on `chrome`/`views`/`keyboards`/`callbacks`;
`py_compile`; `import main` (registers all routers); run existing `pytest`.
