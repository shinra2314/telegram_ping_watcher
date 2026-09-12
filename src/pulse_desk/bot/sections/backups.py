"""Резервные копии базы: список, создание, отправка файлом.

Отправка ограничена: база сейчас под сотню мегабайт, а бот не почтовый сервер.
Всё, что тяжелее ``MAX_UPLOAD_MB``, не уезжает в чат — бот называет путь и
размер, файл забирается с машины руками. Это осознанно: молчаливая неудачная
загрузка на десятой минуте хуже честного отказа на первой.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from telethon import Button

from database import create_db_backup, list_db_backups, record_event

from ..chrome import empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, fmt_dt

# Телеграм принимает до 2 ГБ, но копия базы в чате — это копия базы в облаке.
MAX_UPLOAD_MB = 45
LIST_LIMIT = 8


def _mb(size: Any) -> float:
    try:
        return float(size) / 1024 / 1024
    except (TypeError, ValueError):
        return 0.0


def card(items: list[dict[str, Any]]) -> str:
    lines = [header("💾", "Бэкапы", "Управление › Бэкапы"), kv("📦", "Копий", len(items)), DIV]
    if not items:
        lines.append(empty("Копий пока нет."))
    for item in items[:LIST_LIMIT]:
        lines.append(f"• `{item.get('name')}` · {_mb(item.get('size')):.1f} МБ · "
                     f"{fmt_dt(item.get('created_at'))}")
    lines.append(f"\n__Файлом уезжает копия до {MAX_UPLOAD_MB} МБ; тяжелее — забирайте с диска.__")
    return "\n".join(lines)


def keyboard(items: list[dict[str, Any]]) -> list[list[Button]]:
    rows: list[list[Button]] = [
        [Button.inline(f"⬇️ {str(item.get('name'))[:32]}", f"bk:get:{i}".encode())]
        for i, item in enumerate(items[:LIST_LIMIT])
    ]
    rows.append([Button.inline("➕ Создать копию", b"bk:new")])
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"bk"),
    ])
    return rows


def collect() -> list[dict[str, Any]]:
    try:
        return list_db_backups(limit=50)
    except Exception:
        return []


async def _show(click: Click) -> None:
    items = collect()
    await safe_edit(click.event, card(items), buttons=keyboard(items))


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "new":
        created = create_db_backup()
        if not created:
            await click.event.answer("Базы нет — копировать нечего", alert=True)
            return
        await record_event("INFO", "backup", "Database backup created from the bot",
                           {"name": created.get("name")})
        await click.event.answer(f"Копия создана: {created.get('name')}")
        await _show(click)
        return
    if action == "get":
        await _send(click)
        return
    await _show(click)


async def _send(click: Click) -> None:
    """Отдать копию файлом — если она помещается в разумную отправку."""
    index = click.int_arg(2)
    items = collect()
    if index is None or not 0 <= index < len(items[:LIST_LIMIT]):
        await click.event.answer("Список изменился — откройте заново", alert=True)
        return
    item = items[index]
    path = Path(str(item.get("path") or ""))
    size_mb = _mb(item.get("size"))
    if not path.exists():
        await click.event.answer("Файл не найден", alert=True)
        return
    if size_mb > MAX_UPLOAD_MB:
        await click.event.answer(
            f"{size_mb:.0f} МБ — больше лимита {MAX_UPLOAD_MB} МБ.\n{path}", alert=True)
        return
    await click.event.respond(f"💾 `{item.get('name')}` · {size_mb:.1f} МБ", file=str(path))
    await click.event.answer()


def register(router: CallbackRouter) -> None:
    router.group("bk", admin=True)(handle)
