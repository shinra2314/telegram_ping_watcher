"""Резервные копии базы и уборка: список, создание, отправка файлом, «уборка сейчас».

Копия — согласованный снимок базы, сжатый в zip (см. ``database/backups.py``),
поэтому обычно укладывается в ``MAX_UPLOAD_MB`` и уезжает в чат файлом. Всё,
что тяжелее (старые несжатые ``.db``), не отправляется — бот называет путь и
размер, файл забирается с машины руками: молчаливая неудачная загрузка на
десятой минуте хуже честного отказа на первой.

Уборка — тот же проход, что раз в час делает джоб ``maintenance``: удаление
старых строк, VACUUM, ротация копий, чистка кэша картинок.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from telethon import Button

from database import backups_total_bytes, create_db_backup, list_db_backups, record_event

from ...app_ctx import logger, state
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


def _cap_mb() -> int:
    try:
        return int(os.getenv("BACKUP_MAX_TOTAL_MB", "1024"))
    except ValueError:
        return 1024


def maintenance_lines(stats: Optional[dict[str, Any]]) -> list[str]:
    """Итог последней уборки — или честное «ещё не было»."""
    if not stats:
        return [empty("Уборка ещё не проходила — первая через 10 минут после запуска.")]
    lines = [
        kv("♻️", "Уборка", fmt_dt(stats.get("finished_at"))),
        kv("💾", "База", f"{_mb(stats.get('db_bytes')):.1f} МБ · пустых {stats.get('freelist_pct', 0)}%"),
        kv("💾", "Диск свободен",f"{_mb(stats.get('disk_free_bytes')) / 1024:.1f} ГБ"),
    ]
    freed = _mb(stats.get("freed_bytes"))
    if freed >= 0.1:
        lines.append(kv("🗑", "Освобождено", f"{freed:.1f} МБ"))
    return lines


def card(items: list[dict[str, Any]], total_bytes: Optional[int] = None,
         stats: Optional[dict[str, Any]] = None) -> str:
    lines = [header("💾", "Бэкапы", "Управление › Бэкапы"), kv("📦", "Копий", len(items))]
    if total_bytes is not None:
        lines.append(kv("📂", "Занято",f"{_mb(total_bytes):.1f} из {_cap_mb()} МБ"))
    lines.append(DIV)
    if not items:
        lines.append(empty("Копий пока нет."))
    for item in items[:LIST_LIMIT]:
        lines.append(f"• `{item.get('name')}` · {_mb(item.get('size')):.1f} МБ · "
                     f"{fmt_dt(item.get('created_at'))}")
    lines.append(DIV)
    lines += maintenance_lines(stats)
    lines.append(f"\n__Файлом уезжает копия до {MAX_UPLOAD_MB} МБ; тяжелее — забирайте с диска. "
                 "Хранятся 3 последние, по одной за день (неделя) и за неделю (месяц).__")
    return "\n".join(lines)


def keyboard(items: list[dict[str, Any]]) -> list[list[Button]]:
    rows: list[list[Button]] = [
        [Button.inline(f"⬇️ {str(item.get('name'))[:32]}", f"bk:get:{i}".encode())]
        for i, item in enumerate(items[:LIST_LIMIT])
    ]
    rows.append([Button.inline("➕ Создать копию", b"bk:new"), Button.inline("🧹 Уборка сейчас", b"bk:clean")])
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
    items = await asyncio.to_thread(collect)
    total = await asyncio.to_thread(backups_total_bytes)
    await safe_edit(click.event, card(items, total, state.maintenance_stats), buttons=keyboard(items))


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "new":
        # Снимок + zip всей базы — секунды; в event loop это заморозило бы бота.
        created = await asyncio.to_thread(create_db_backup)
        if not created:
            await click.event.answer("Базы нет — копировать нечего", alert=True)
            return
        await record_event("INFO", "backup", "Database backup created from the bot",
                           {"name": created.get("name")})
        await click.event.answer(f"Копия создана: {created.get('name')}")
        await _show(click)
        return
    if action == "clean":
        await _clean(click)
        return
    if action == "get":
        await _send(click)
        return
    await _show(click)


async def _clean(click: Click) -> None:
    """Один проход уборки по кнопке. Ответ на нажатие — сразу: VACUUM может
    занять дольше, чем живёт callback-query."""
    from ...loops import run_maintenance_once

    await click.event.answer("Уборка запущена…")
    try:
        stats = await run_maintenance_once(force_daily=True)
    except Exception:
        logger.exception("Maintenance pass from the bot failed")
        await click.event.respond("⚠️ Уборка не удалась — подробности в /logs.")
        return
    freed = _mb(stats.get("freed_bytes"))
    note = f"♻️ Уборка готова: освобождено `{freed:.1f} МБ`." if freed >= 0.1 else "♻️ Уборка готова: чистить было нечего."
    items = await asyncio.to_thread(collect)
    total = await asyncio.to_thread(backups_total_bytes)
    await safe_edit(click.event, note + "\n\n" + card(items, total, stats), buttons=keyboard(items))


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
