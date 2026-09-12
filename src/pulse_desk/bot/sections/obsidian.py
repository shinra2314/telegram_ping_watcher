"""Заметка «Долги.md» в Obsidian: прогресс, галочки, принудительный синк.

Перенос панели из вкладки «Долги». Правило синхронизации не меняется: **заметка
главнее** — приложение подхватывает её галочки, а не навязывает свои. Поэтому
и раздел устроен так: галочка ставится в файле, а статус записи подтягивается
следом (`toggle_item` → реконсиляция).

Ссылка `t.me/...` длиннее callback-бюджета, поэтому пункт адресуется индексом в
том же порядке, в котором его показали; список собирается из файла заново на
каждый клик, и если файл поменялся — индекс честно проверяется по границам.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from telethon import Button

from ...app_ctx import settings, state
from ...obsidian_debts import current_config, load_snapshot, sync_once, toggle_item
from ..chrome import bar, empty, header, kv
from ..pending import prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV

PAGE_SIZE = 8


def flat_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Пункты всех групп подряд — тот порядок, которым их адресует кнопка."""
    items: list[dict[str, Any]] = []
    for group in snapshot.get("groups") or []:
        for item in group.get("items") or []:
            items.append({**item, "username": group.get("username") or ""})
    return items


def progress_text(snapshot: dict[str, Any]) -> str:
    overall = snapshot.get("overall") or {}
    done, total = int(overall.get("done") or 0), int(overall.get("total") or 0)
    return f"{bar(done, total, width=10)} `{done}/{total}`"


def card(snapshot: dict[str, Any], page: int) -> str:
    if not snapshot.get("enabled"):
        reason = {"no_path": "путь к заметке не задан",
                  "missing_file": "файл заметки не найден"}.get(
                      str(snapshot.get("reason")), "синхронизация выключена")
        return (header("🗒", "Obsidian", "Долги › Заметка") + "\n" + empty(reason)
                + "\n\n__Путь настраивается кнопкой ниже.__")
    lines = [
        header("🗒", "Obsidian", "Долги › Заметка"),
        progress_text(snapshot),
    ]
    sync = snapshot.get("sync") or {}
    lines.append(f"{kv('✍️', 'Запись в файл', 'вкл' if sync.get('write_enabled') else 'выкл')}   "
                 f"{kv('🕐', 'Синк', sync.get('last_sync_at') or '—')}")
    lines.append(DIV)
    for group in snapshot.get("groups") or []:
        total = int(group.get("total") or 0)
        if not total:
            continue
        lines.append(f"👤 {group.get('username')}: {bar(int(group.get('done') or 0), total)} "
                     f"`{group.get('done')}/{total}`")
    items = flat_items(snapshot)
    if not items:
        lines.append(empty("В заметке нет пунктов."))
    else:
        lines.append(f"\n📄 Страница {page} · всего пунктов: `{len(items)}`")
    return "\n".join(lines)


def keyboard(snapshot: dict[str, Any], page: int, is_admin: bool) -> list[list[Button]]:
    items = flat_items(snapshot)
    start = (max(1, page) - 1) * PAGE_SIZE
    window = items[start:start + PAGE_SIZE]
    rows: list[list[Button]] = [
        [Button.inline(f"{'✅' if item.get('checked') else '⬜'} "
                       f"{(item.get('title') or item.get('recipient') or item.get('link') or '?')[:40]}",
                       f"ob:t:{start + i}:{page}".encode())]
        for i, item in enumerate(window)
    ]
    has_more = len(items) > start + PAGE_SIZE
    if page > 1 or has_more:
        pager: list[Button] = []
        if page > 1:
            pager.append(Button.inline("◀️", f"ob:p:{page - 1}".encode()))
        pager.append(Button.inline(f"· {page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("▶️", f"ob:p:{page + 1}".encode()))
        rows.append(pager)
    if is_admin:
        rows.append([
            Button.inline("🔄 Синхронизировать", f"ob:sync:{page}".encode()),
            Button.inline("⚙️ Настройки", b"ob:cfg"),
        ])
    rows.append([
        Button.inline("⬅️ Долги", b"menu_debts"),
        Button.inline("🔄 Обновить", f"ob:p:{page}".encode()),
    ])
    return rows


def config_card(config: dict[str, Any]) -> str:
    return "\n".join([
        header("⚙️", "Obsidian", "Долги › Заметка › Настройки"),
        kv("📂", "Путь", config.get("path") or "—"),
        kv("🔌", "Синхронизация", "вкл" if config.get("enabled") else "выкл"),
        kv("✍️", "Запись в файл", "вкл" if config.get("write") else "выкл"),
        DIV,
        "__Заметка главнее приложения: при расхождении выигрывает файл.__",
    ])


def config_keyboard(config: dict[str, Any]) -> list[list[Button]]:
    return [
        [Button.inline(f"{'✅' if config.get('enabled') else '❌'} Синхронизация", b"ob:en")],
        [Button.inline(f"{'✅' if config.get('write') else '❌'} Запись в файл", b"ob:wr")],
        [Button.inline("📂 Указать путь", b"ob:path")],
        [Button.inline("⬅️ К заметке", b"ob:p:1")],
    ]


async def _save_config(**changes: Any) -> dict[str, Any]:
    """Сохранить настройки синка и применить их к живому объекту настроек."""
    from ...obsidian_debts import apply_prefs, load_prefs, save_prefs

    prefs = await load_prefs()
    prefs.update(changes)
    await save_prefs(prefs)
    apply_prefs(settings, prefs)
    return current_config(settings)


async def _consume_path(event, pending: dict, raw: str) -> None:
    config = await _save_config(path=raw.strip())
    await event.respond(f"📂 Путь сохранён.\n\n{config_card(config)}",
                        buttons=config_keyboard(config))


PATH_INPUT = register_prompt(
    "obsidian_path", "Пришлите полный путь к файлу заметки (`...\\Долги.md`).", _consume_path)


async def _show(click: Click, page: int) -> None:
    snapshot = await load_snapshot(state, settings)
    await safe_edit(click.event, card(snapshot, page),
                    buttons=keyboard(snapshot, page, click.role == "admin"),
                    link_preview=False)


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "cfg":
        config = current_config(settings)
        await safe_edit(click.event, config_card(config), buttons=config_keyboard(config))
        return
    if action in ("en", "wr"):
        config = current_config(settings)
        key = "enabled" if action == "en" else "write"
        config = await _save_config(**{key: not config.get(key)})
        await click.event.answer("Сохранено")
        await safe_edit(click.event, config_card(config), buttons=config_keyboard(config))
        return
    if action == "path":
        await prompt_pending(click.event, PATH_INPUT)
        return
    if action == "sync":
        meta = await sync_once(state, settings, force=True)
        await click.event.answer("Синхронизировано" if meta.get("enabled", True)
                                 else "Синхронизация выключена")
        await _show(click, click.int_arg(2) or 1)
        return
    if action == "t":
        await _toggle(click)
        return
    await _show(click, click.int_arg(2) or 1)


async def _toggle(click: Click) -> None:
    """Переключить галочку пункта. Индекс проверяется по свежему снимку."""
    index = click.int_arg(2)
    page = click.int_arg(3) or 1
    snapshot = await load_snapshot(state, settings)
    items = flat_items(snapshot)
    if index is None or not 0 <= index < len(items):
        await click.event.answer("Список изменился — откройте заново", alert=True)
        return
    item = items[index]
    link = item.get("link_norm")
    if not link:
        await click.event.answer("У пункта нет ссылки — отметить можно только в файле", alert=True)
        return
    result = await toggle_item(state, settings, link, not item.get("checked"))
    if not result.get("ok"):
        await click.event.answer("Не получилось изменить заметку", alert=True)
        return
    await click.event.answer("Отмечено" if not item.get("checked") else "Снято")
    await _show(click, page)


async def handle_menu(click: Click) -> None:
    await _show(click, 1)


def register(router: CallbackRouter) -> None:
    router.group("ob", admin=True)(handle)
    router.exact("menu_obsidian", admin=True)(handle_menu)
