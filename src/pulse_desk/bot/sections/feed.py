"""Лента упоминаний: выборка, поиск, карточка записи и действия по ней.

Это перенос вкладки «Дашборд» из веба — там фильтры лежали в localStorage и
уезжали запросом, здесь вся выборка живёт в самой кнопке (``FeedFilter`` → 19
байт) и потому переживает перезапуск приложения.

Исключение одно: текст поиска. В 64 байта callback-данных он не влезает, так
что в кнопке едет только флаг, а строка лежит в ``state.bot_feed_queries`` и
живёт до перезапуска — после него лента честно показывается без поиска.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from telethon import Button

from database import (
    add_ping_tag, get_ping_by_id, get_pings, get_recent_giveaway_actions, get_setting,
    mark_ping_read, mark_pings_read, remove_ping_tag, search_pings_fts, set_setting,
    toggle_favorite,
)

from ...app_ctx import state
from ...bot_membership import configured_admin_ids, resolve_member_access
from ...bot_permissions import accounts_allowed, full_permissions
from ...common import record_app_event
from ...ping_actions import UnknownStatus, action_for_giveaway, apply_ping_meta
from ..cards import feed_badge, feed_header, ping_card
from ..keyboards import (
    MON_FILTERS, feed_filters_keyboard, feed_keyboard, feed_presets_keyboard, ping_card_keyboard,
    ping_giveaway_keyboard, ping_status_keyboard, ping_tags_keyboard,
)
from ..pending import prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import (
    DIV, FEED_SORT_DB, FEED_STATUS_DB, FEED_TYPE_CODE, FeedFilter, describe_feed_filter,
    feed_filter_from_legacy, feed_state_cb, fmt_dt, giveaway_status_label, parse_feed_filter,
    ping_open_cb, ping_status_label,
)

FEATURE = "recent"
PAGE_SIZE = 8
LABELS = dict(MON_FILTERS)
# Сохранённые выборки лежат в том же ключе, что писал веб, — пресеты владельца
# переживают переезд.
PRESETS_KEY = "saved_filters"
PRESET_LIMIT = 12
QUERY_TTL = timedelta(hours=1)
EXPORT_LIMIT = 5000
NOT_FOUND = "Запись не найдена"


# ---- поисковый запрос ---------------------------------------------------
def current_query(sender_id: int) -> str:
    """Строка поиска этого человека, если она ещё не протухла."""
    entry = state.bot_feed_queries.get(sender_id)
    if not entry:
        return ""
    text, armed = entry
    if datetime.now() - armed > QUERY_TTL:
        state.bot_feed_queries.pop(sender_id, None)
        return ""
    return text


def set_query(sender_id: int, text: str) -> None:
    state.bot_feed_queries[sender_id] = (text, datetime.now())


# ---- выборка ------------------------------------------------------------
def _searching_only(state_filter: FeedFilter) -> bool:
    """Поиск без остальных фильтров — случай, который умеет делать индекс."""
    return state_filter.query and state_filter.with_(query=False, page=1) == FeedFilter()


async def fetch(state_filter: FeedFilter, perms: dict, query: str) -> tuple[list[dict], bool]:
    """Страница ленты плюс признак, что дальше есть ещё.

    Берём на одну строку больше, чем показываем: это дешевле, чем считать
    COUNT(*) по тем же условиям ради одной стрелки.

    Голый поиск идёт через FTS5: ``get_pings(search=…)`` подмешивает к MATCH
    ещё и LIKE с ведущим ``%``, а это отказ от индекса и полный проход по
    135-мегабайтной таблице — при пятнадцатисекундном сроке жизни колбэка так
    нельзя. Поиск вместе с другими фильтрами всё же уходит в ``get_pings``:
    FTS не умеет ни статус, ни избранное, а дублировать эти условия на Python
    значит завести вторую правду о том, что такое «важные».
    """
    perms = perms or full_permissions()
    wanted = PAGE_SIZE + 1
    offset = (max(1, state_filter.page) - 1) * PAGE_SIZE
    search = query if (state_filter.query and query) else None

    if search and _searching_only(state_filter):
        # Аккаунтные ограничения ключа FTS тоже не знает — отсеиваем после.
        scoped = bool(perms.get("accounts"))
        found = await search_pings_fts(search, limit=(wanted + offset) * (8 if scoped else 1))
        if found:
            rows = [r for r in found if accounts_allowed(perms, r.get("mentions"))]
            window = rows[offset:offset + wanted]
            return window[:PAGE_SIZE], len(window) > PAGE_SIZE

    rows = await get_pings(
        limit=wanted,
        offset=offset,
        chat_type=state_filter.db_type,
        status=state_filter.db_status,
        favorite=True if state_filter.favorite else None,
        search=search,
        sort_by=state_filter.db_sort,
        sort_order=state_filter.db_order,
        mention_any=perms.get("accounts") or None,
    )
    return rows[:PAGE_SIZE], len(rows) > PAGE_SIZE


def visible_pings(rows: list[dict], perms: dict, limit: int) -> list[dict]:
    """Drop rows about accounts this key was not granted, then trim."""
    return [r for r in rows if accounts_allowed(perms, r.get("mentions"))][:limit]


async def render(state_filter: Optional[FeedFilter] = None, perms: Optional[dict] = None,
                 query: str = "", is_admin: bool = False):
    state_filter = state_filter or FeedFilter()
    rows, has_more = await fetch(state_filter, perms or full_permissions(), query)
    items = []
    for r in rows:
        hhmm = fmt_dt(r.get("detected_at"))[-5:]
        badge = "🗑" if r.get("deleted_at") else feed_badge(r.get("priority_label"))
        label = f"{badge} {hhmm} {r.get('chat') or '?'}"
        items.append((int(r["id"]), label[:48]))
    head = feed_header(state_filter.type_label, len(rows))
    head += f"\n🔎 __{describe_feed_filter(state_filter, query)}__"
    return head, feed_keyboard(items, state_filter, has_more=has_more, is_admin=is_admin)


async def open_search(sender_id: int, query: str, perms: Optional[dict] = None,
                      is_admin: bool = False):
    """Запомнить запрос и показать ленту по нему — общий путь для /search и кнопки."""
    text = (query or "").strip()[:64]
    set_query(sender_id, text)
    return await render(FeedFilter(query=True), perms, text, is_admin=is_admin)


async def open_card(ping_id: int, is_admin: bool, perms: Optional[dict] = None,
                    state_filter: Optional[FeedFilter] = None):
    ping = await get_ping_by_id(ping_id)
    if not ping:
        return None
    if perms is not None and not accounts_allowed(perms, ping.get("mentions")):
        return None
    return card_text(ping), ping_card_keyboard(ping, is_admin, state_filter)


def card_text(ping: dict) -> str:
    """Карточка плюс то, что раньше жило только в модалке веба."""
    text = ping_card(ping)
    extras = [f"🏷 Статус: `{ping_status_label(ping.get('status'))}`"]
    if ping.get("is_giveaway") or ping.get("is_win"):
        extras.append(f"🎁 Розыгрыш: `{giveaway_status_label(ping.get('giveaway_status'))}`")
    tags = _tags_of(ping)
    if tags:
        extras.append("🏷 Теги: " + ", ".join(f"`{t}`" for t in tags))
    if ping.get("note"):
        extras.append(f"📝 __{str(ping['note'])[:300]}__")
    return f"{text}\n{DIV}\n" + "\n".join(extras)


def _tags_of(ping: dict) -> list[str]:
    """Теги записи: колонка хранит JSON, но в старых строках лежит «a, b»."""
    raw = ping.get("tags")
    if isinstance(raw, list):
        return [str(t) for t in raw]
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return []
    if text[0] in "[{":
        # Похоже на JSON — если он битый, это не список тегов, а мусор, и
        # разбирать его запятыми значит показать человеку «{» как тег.
        try:
            parsed = json.loads(text)
        except ValueError:
            return []
        return [str(t) for t in parsed] if isinstance(parsed, list) else []
    return [p.strip() for p in text.split(",") if p.strip()]


# ---- показ --------------------------------------------------------------
async def _show_feed(click: Click, state_filter: FeedFilter) -> None:
    text, kb = await render(state_filter, click.perms, current_query(click.sender_id),
                            is_admin=click.role == "admin")
    await safe_edit(click.event, text, buttons=kb, link_preview=False)


async def _show_card(click: Click, ping_id: int, state_filter: FeedFilter) -> None:
    res = await open_card(ping_id, click.role == "admin", click.perms, state_filter)
    if res is None:
        await click.event.answer(NOT_FOUND, alert=True)
        return
    text, kb = res
    await safe_edit(click.event, text, buttons=kb, link_preview=False)


# ---- свободный ввод -----------------------------------------------------
async def _consume_search(event, pending: dict, raw: str) -> None:
    if not raw:
        await event.respond("❌ Пустой запрос.")
        return
    # Поиск открыт всем, у кого есть раздел, поэтому выборка рисуется правами
    # именно этого человека: по ключу, привязанному к одному аккаунту, чужие
    # упоминания не должны находиться поиском.
    role, perms = await resolve_member_access(event.sender_id,
                                              admin_ids=configured_admin_ids())
    text, kb = await open_search(event.sender_id, raw, perms, is_admin=role == "admin")
    await event.respond(f"🔎 Поиск: «{raw[:64]}»\n\n{text}", buttons=kb, link_preview=False)


async def _consume_note(event, pending: dict, raw: str) -> None:
    ping_id = int(pending.get("scope") or 0)
    if not ping_id:
        return
    await apply_ping_meta(ping_id, note=raw[:1000])
    ping = await get_ping_by_id(ping_id)
    if not ping:
        await event.respond(f"❌ {NOT_FOUND}.")
        return
    await event.respond(f"✅ Заметка сохранена.\n\n{card_text(ping)}",
                        buttons=ping_card_keyboard(ping, True), link_preview=False)


async def _consume_tag(event, pending: dict, raw: str) -> None:
    ping_id = int(pending.get("scope") or 0)
    tag = raw.strip().lstrip("#")[:24]
    if not ping_id or not tag:
        await event.respond("❌ Пустой тег.")
        return
    tags = await add_ping_tag(ping_id, tag)
    await event.respond(f"✅ Тег «{tag}» добавлен.",
                        buttons=ping_tags_keyboard(ping_id, tags))


async def _consume_preset_name(event, pending: dict, raw: str) -> None:
    name = raw.strip()[:40]
    if not name:
        await event.respond("❌ Пустое имя.")
        return
    state_filter = parse_feed_filter((pending.get("scope") or "").split(":"))
    presets = await _load_presets()
    presets = [p for p in presets if p.get("name") != name]
    presets.append({"name": name, "query": _preset_query(state_filter)})
    await _save_presets(presets[-PRESET_LIMIT:])
    await event.respond(f"💾 Пресет «{name}» сохранён.",
                        buttons=feed_presets_keyboard(await _preset_names(), state_filter))


SEARCH_INPUT = register_prompt(
    "feed_search", "Пришлите текст для поиска по упоминаниям.", _consume_search, admin=False)
NOTE_INPUT = register_prompt("ping_note", "Пришлите заметку к записи.", _consume_note)
TAG_INPUT = register_prompt("ping_tag", "Пришлите тег (одно слово).", _consume_tag)
PRESET_INPUT = register_prompt(
    "feed_preset", "Пришлите имя для этой выборки (до 40 символов).", _consume_preset_name)


# ---- пресеты ------------------------------------------------------------
def _preset_query(state_filter: FeedFilter) -> dict[str, Any]:
    """Выборка в том же виде, в каком её писал веб, — ключ настроек общий."""
    return {
        "type": state_filter.db_type,
        "status": state_filter.db_status or "",
        "favorite": bool(state_filter.favorite),
        "sort_by": state_filter.db_sort,
        "sort_order": state_filter.db_order,
    }


def _preset_to_filter(query: dict[str, Any]) -> FeedFilter:
    sort = next((code for code, value in FEED_SORT_DB.items()
                 if value == query.get("sort_by")), "d")
    status = next((code for code, value in FEED_STATUS_DB.items()
                   if value == (query.get("status") or None)), "a")
    return FeedFilter(
        type=FEED_TYPE_CODE.get(str(query.get("type") or "all"), "a"),
        status=status,
        favorite=bool(query.get("favorite")),
        sort=sort,
        ascending=str(query.get("sort_order") or "DESC").upper() == "ASC",
    )


async def _load_presets() -> list[dict[str, Any]]:
    saved = await get_setting(PRESETS_KEY, [])
    return [p for p in saved if isinstance(p, dict) and p.get("name")] if isinstance(saved, list) else []


async def _save_presets(presets: list[dict[str, Any]]) -> None:
    await set_setting(PRESETS_KEY, presets)


async def _preset_names() -> list[str]:
    return [str(p.get("name")) for p in await _load_presets()]


# ---- экспорт ------------------------------------------------------------
EXPORT_COLUMNS = ("id", "detected_at", "date", "chat", "sender", "status", "giveaway_status",
                  "priority_score", "is_win", "is_giveaway", "link", "text")


def _export_bytes(rows: Sequence[dict], fmt: str) -> tuple[bytes, str]:
    """Файл выгрузки в памяти: строк тут тысячи, не мегабайты."""
    if fmt == "j":
        payload = json.dumps([{k: r.get(k) for k in EXPORT_COLUMNS} for r in rows],
                             ensure_ascii=False, indent=1)
        return payload.encode("utf-8"), "pings.json"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(EXPORT_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) for k in EXPORT_COLUMNS})
    # BOM — иначе Excel открывает кириллицу кракозябрами.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8"), "pings.csv"


# ---- обработчики --------------------------------------------------------
async def handle(click: Click) -> None:
    """Всё семейство `mon:` — лента, карточка, фильтры, поиск, пресеты, выгрузка."""
    event, seg = click.event, click.seg
    action = click.arg(1)

    if action == "open":
        ping_id = click.int_arg(2)
        if ping_id is None:
            await event.answer("Некорректная команда", alert=True)
            return
        await _show_card(click, ping_id, parse_feed_filter(seg[3:]))
        return
    if action == "f":
        await _show_feed(click, parse_feed_filter(seg[2:]))
        return
    if action == "feed":
        # Старая кнопка `mon:feed:<фильтр>[:<стр>]` — она ещё лежит в чатах.
        await _show_feed(click, feed_filter_from_legacy(click.arg(2, "all"),
                                                        click.int_arg(3) or 1))
        return
    if action == "ff":
        state_filter = parse_feed_filter(seg[2:])
        await safe_edit(event, _filters_text(state_filter, current_query(click.sender_id)),
                        buttons=feed_filters_keyboard(state_filter, click.role == "admin"))
        return
    if action in ("cs", "ss", "so", "fv", "qx"):
        state_filter = parse_feed_filter(seg[2:])
        if action == "cs":
            state_filter = state_filter.next_status()
        elif action == "ss":
            state_filter = state_filter.next_sort()
        elif action == "so":
            state_filter = state_filter.with_(ascending=not state_filter.ascending)
        elif action == "fv":
            state_filter = state_filter.with_(favorite=not state_filter.favorite)
        else:
            state.bot_feed_queries.pop(click.sender_id, None)
            state_filter = state_filter.with_(query=False)
        await safe_edit(event, _filters_text(state_filter, current_query(click.sender_id)),
                        buttons=feed_filters_keyboard(state_filter, click.role == "admin"))
        return
    if action == "q":
        await prompt_pending(event, SEARCH_INPUT)
        return
    if action == "ra":
        await _handle_bulk_read(click, parse_feed_filter(seg[2:]), confirmed=False)
        return
    if action == "rag":
        await _handle_bulk_read(click, parse_feed_filter(seg[2:]), confirmed=True)
        return
    if action == "pr":
        await _show_presets(click, FeedFilter())
        return
    if action in ("ps", "pd"):
        await _handle_preset(click, action)
        return
    if action == "pn":
        # Пресеты — общая настройка владельца (тот же ключ, что писал веб), а не
        # личная закладка гостя; кнопка гостю не рисуется, но колбэк подбирается.
        if click.role != "admin":
            await event.answer("Только владелец", alert=True)
            return
        state_filter = parse_feed_filter(seg[2:])
        await prompt_pending(event, PRESET_INPUT,
                             scope=":".join(state_filter.cb().decode().split(":")[2:]))
        return
    if action == "ex":
        await _handle_export(click)
        return
    await _show_feed(click, FeedFilter())


def _filters_text(state_filter: FeedFilter, query: str) -> str:
    lines = [
        "⚙️ **Выборка ленты**",
        DIV,
        f"🏷 Тип: `{state_filter.type_label}`",
        f"📍 Статус: `{state_filter.status_label}`",
        f"⭐ Избранное: `{'да' if state_filter.favorite else 'нет'}`",
        f"⇅ Сортировка: `{state_filter.sort_label}` "
        f"{'по возрастанию' if state_filter.ascending else 'по убыванию'}",
    ]
    if state_filter.query and query:
        lines.append(f"🔎 Поиск: `{query[:48]}`")
    return "\n".join(lines)


async def _handle_bulk_read(click: Click, state_filter: FeedFilter, confirmed: bool) -> None:
    if click.role != "admin":
        await click.event.answer("Только владелец", alert=True)
        return
    if not confirmed:
        # Массовое действие без отката — сначала показываем, что именно накроет.
        scope = describe_feed_filter(state_filter, current_query(click.sender_id))
        await safe_edit(
            click.event,
            "✅ **Отметить прочитанными?**\n" + DIV +
            f"\nПод выборку попадает: __{scope}__"
            "\n\nОтметятся только новые записи.",
            buttons=_confirm_keyboard(state_filter),
        )
        return
    query = current_query(click.sender_id)
    changed = await mark_pings_read(
        chat_type=state_filter.db_type,
        status=state_filter.db_status,
        favorite=True if state_filter.favorite else None,
        search=query if (state_filter.query and query) else None,
        only_new=True,
    )
    await click.event.answer(f"Отмечено: {changed}")
    await record_app_event("INFO", "pings", "Bulk mark-read from the bot",
                           {"changed": changed, "filter": state_filter._asdict()})
    await _show_feed(click, state_filter)


def _confirm_keyboard(state_filter: FeedFilter):
    return [
        [Button.inline("✅ Да, отметить", feed_state_cb("mon:rag", state_filter))],
        [Button.inline("⬅️ Отмена", state_filter.cb())],
    ]


async def _show_presets(click: Click, state_filter: FeedFilter) -> None:
    names = await _preset_names()
    text = "💾 **Сохранённые выборки**\n" + DIV
    text += "\n" + ("\n".join(f"• {n}" for n in names) if names else "📭 __Пусто.__")
    await safe_edit(click.event, text,
                    buttons=feed_presets_keyboard(names, state_filter,
                                                  can_save=click.role == "admin"))


async def _handle_preset(click: Click, action: str) -> None:
    index = click.int_arg(2)
    presets = await _load_presets()
    if index is None or not 0 <= index < len(presets):
        await click.event.answer("Список пресетов изменился — откройте заново", alert=True)
        return
    if action == "pd":
        if click.role != "admin":
            await click.event.answer("Только владелец", alert=True)
            return
        removed = presets.pop(index)
        await _save_presets(presets)
        await click.event.answer(f"Удалён: {removed.get('name')}")
        await _show_presets(click, FeedFilter())
        return
    await _show_feed(click, _preset_to_filter(presets[index].get("query") or {}))


async def _handle_export(click: Click) -> None:
    if click.role != "admin":
        await click.event.answer("Только владелец", alert=True)
        return
    fmt = click.arg(2, "c")
    state_filter = parse_feed_filter(click.seg[3:])
    query = current_query(click.sender_id)
    rows = await get_pings(
        limit=EXPORT_LIMIT,
        chat_type=state_filter.db_type,
        status=state_filter.db_status,
        favorite=True if state_filter.favorite else None,
        search=query if (state_filter.query and query) else None,
        sort_by=state_filter.db_sort,
        sort_order=state_filter.db_order,
    )
    if not rows:
        await click.event.answer("Под выборку ничего не попало", alert=True)
        return
    payload, name = _export_bytes(rows, fmt)
    buffer = io.BytesIO(payload)
    buffer.name = name
    await click.event.respond(f"⬇️ Выгрузка: {len(rows)} записей", file=buffer)
    await click.event.answer()


# ---- действия по карточке ------------------------------------------------
async def handle_ping_action(click: Click) -> None:
    """Семейство `pg:` — статусы, заметка, теги, история."""
    event = click.event
    action = click.arg(1)
    ping_id = click.int_arg(2)
    if ping_id is None:
        await event.answer("Некорректная команда", alert=True)
        return
    ping = await get_ping_by_id(ping_id)
    if not ping:
        await event.answer(NOT_FOUND, alert=True)
        return
    # Состояние ленты всегда последними семью сегментами; у действий со
    # значением (`sts`, `gws`, `tgd`) перед ним едет ещё и само значение.
    tail = 4 if action in ("sts", "gws", "tgd") else 3
    state_filter = parse_feed_filter(click.seg[tail:])

    if action == "st":
        await safe_edit(event, card_text(ping),
                        buttons=ping_status_keyboard(ping_id, ping.get("status"), state_filter))
        return
    if action == "gw":
        await safe_edit(event, card_text(ping),
                        buttons=ping_giveaway_keyboard(ping_id, ping.get("giveaway_status"),
                                                       state_filter))
        return
    if action == "sts":
        await _apply(click, ping_id, state_filter, status=click.arg(3))
        return
    if action == "gws":
        code = click.arg(3)
        await _apply(click, ping_id, state_filter, giveaway_status=code,
                     action_status=action_for_giveaway(code))
        return
    if action == "nt":
        await prompt_pending(event, NOTE_INPUT, scope=str(ping_id))
        return
    if action == "tg":
        await safe_edit(event, card_text(ping),
                        buttons=ping_tags_keyboard(ping_id, _tags_of(ping), state_filter))
        return
    if action == "tga":
        await prompt_pending(event, TAG_INPUT, scope=str(ping_id))
        return
    if action == "tgd":
        index = click.int_arg(3)
        tags = _tags_of(ping)
        if index is None or not 0 <= index < len(tags):
            await event.answer("Список тегов изменился — откройте заново", alert=True)
            return
        left = await remove_ping_tag(ping_id, tags[index])
        await event.answer(f"Снят: {tags[index]}")
        await safe_edit(event, card_text(await get_ping_by_id(ping_id) or ping),
                        buttons=ping_tags_keyboard(ping_id, left, state_filter))
        return
    if action == "hs":
        await _show_history(click, ping_id, state_filter)
        return
    await _show_card(click, ping_id, state_filter)


async def _apply(click: Click, ping_id: int, state_filter: FeedFilter, **changes) -> None:
    if click.role != "admin":
        await click.event.answer("Только владелец", alert=True)
        return
    try:
        await apply_ping_meta(ping_id, **changes)
    except UnknownStatus:
        await click.event.answer("Неизвестный статус", alert=True)
        return
    await click.event.answer("Сохранено")
    await _show_card(click, ping_id, state_filter)


async def _show_history(click: Click, ping_id: int, state_filter: FeedFilter) -> None:
    actions = await get_recent_giveaway_actions(limit=200)
    mine = [a for a in actions if int(a.get("ping_id") or 0) == ping_id][:8]
    lines = [f"🧾 **История действий** · #{ping_id}", DIV]
    if not mine:
        lines.append("📭 __Действий не было.__")
    else:
        lines += [f"`{fmt_dt(a.get('created_at'))}` · {a.get('action')} · _{a.get('result') or '—'}_"
                  for a in mine]
    await safe_edit(click.event, "\n".join(lines),
                    buttons=[[Button.inline("⬅️ К карточке", ping_open_cb(ping_id, state_filter))]])


async def handle_menu(click: Click) -> None:
    await _show_feed(click, FeedFilter())


async def handle_flag(click: Click) -> None:
    """`ping:fav|read:<id>` — отметки прямо с карточки."""
    ping_id = click.int_arg(2)
    if ping_id is None or click.arg(1) not in ("fav", "read"):
        await click.event.answer("Некорректная команда", alert=True)
        return
    # Ни один из флагов не печатается на карточке (ping_card показывает приоритет
    # и чипы розыгрыша/победы), так что перерисовывать её нечем — вышла бы
    # побайтово та же карточка и гарантированный MessageNotModified.
    if click.arg(1) == "fav":
        await toggle_favorite(ping_id)
        await click.event.answer("Избранное обновлено")
    else:
        await mark_ping_read(ping_id)
        await click.event.answer("Отмечено как прочитанное")


def register(router: CallbackRouter) -> None:
    router.group("mon", feature=FEATURE)(handle)
    router.exact("menu_recent", feature=FEATURE)(handle_menu)
    router.group("ping", admin=True)(handle_flag)
    router.group("pg", admin=True)(handle_ping_action)
