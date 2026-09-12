"""Управляемые сервисы: чужие рантаймы, которые поднимает Pulse Desk.

Перенос вкладки «Сервисы». Одно не переносится физически — встроенная панель
сервиса: в вебе это был iframe, в Telegram такого нет, поэтому вместо панели
печатается её адрес.

Сервис адресуется **индексом** в манифесте: имена в 64 байта не всегда влезают,
а список один и тот же на отрисовке и на клике (`config/services.json`).
"""
from __future__ import annotations

from typing import Any

from telethon import Button

from ...process_supervisor import get_supervisor
from ..chrome import dot, empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV

LOG_LINES = 20
LOG_CHARS = 3000


def snapshot() -> list[dict[str, Any]]:
    try:
        return get_supervisor().status_all()
    except Exception:
        return []


def card(items: list[dict[str, Any]]) -> str:
    running = sum(1 for s in items if s.get("running"))
    lines = [
        header("🧩", "Сервисы", "Управление › Сервисы"),
        kv("▶️", "Запущено", f"{running}/{len(items)}"),
        DIV,
    ]
    if not items:
        lines.append(empty("Манифест пуст — config/services.json не настроен."))
    for item in items:
        lines.append(f"{dot(bool(item.get('running')))} `{item.get('name')}` · "
                     f"pid `{item.get('pid') or '—'}` · аптайм `{item.get('uptime_seconds', 0)}с` · "
                     f"рестартов `{item.get('restarts', 0)}`")
        if item.get("last_error"):
            lines.append(f"   ⚠️ __{str(item['last_error'])[:120]}__")
        if item.get("panel_url"):
            # Панель сервиса — отдельное веб-приложение; в Telegram её не
            # встроить, поэтому просто адрес.
            lines.append(f"   🔗 {item['panel_url']}")
    return "\n".join(lines)


def keyboard(items: list[dict[str, Any]]) -> list[list[Button]]:
    rows: list[list[Button]] = []
    for i, item in enumerate(items):
        name = str(item.get("name") or "?")[:18]
        if item.get("running"):
            rows.append([
                Button.inline(f"⏹ {name}", f"sv:stop:{i}".encode()),
                Button.inline("♻️", f"sv:re:{i}".encode()),
                Button.inline("📜", f"sv:log:{i}".encode()),
            ])
        else:
            rows.append([
                Button.inline(f"▶️ {name}", f"sv:start:{i}".encode()),
                Button.inline("📜", f"sv:log:{i}".encode()),
            ])
    if items:
        rows.append([
            Button.inline("▶️ Все", b"sv:allup"),
            Button.inline("⏹ Все", b"sv:alldown"),
        ])
    rows.append([
        Button.inline("⬅️ Управление", b"adm:home"),
        Button.inline("🔄 Обновить", b"sv"),
    ])
    return rows


async def _show(click: Click) -> None:
    items = snapshot()
    await safe_edit(click.event, card(items), buttons=keyboard(items), link_preview=False)


async def handle(click: Click) -> None:
    action = click.arg(1)
    supervisor = get_supervisor()
    if action in ("allup", "alldown"):
        result = await (supervisor.start_all() if action == "allup" else supervisor.stop_all())
        await click.event.answer(f"Готово: {len(result)}")
        await _show(click)
        return
    items = snapshot()
    index = click.int_arg(2)
    if action in ("start", "stop", "re", "log"):
        if index is None or not 0 <= index < len(items):
            await click.event.answer("Список сервисов изменился — откройте заново", alert=True)
            return
        name = str(items[index].get("name"))
        if action == "log":
            await _show_logs(click, supervisor, name)
            return
        try:
            if action == "start":
                await supervisor.start(name)
                await click.event.answer("Запускаю")
            elif action == "stop":
                await supervisor.stop(name)
                await click.event.answer("Останавливаю")
            else:
                await supervisor.restart(name)
                await click.event.answer("Перезапускаю")
        except Exception as exc:
            await click.event.answer(str(exc)[:180], alert=True)
        await _show(click)
        return
    await _show(click)


async def _show_logs(click: Click, supervisor, name: str) -> None:
    lines = supervisor.logs(name, limit=LOG_LINES)
    body = "\n".join(lines)[-LOG_CHARS:] or "пусто"
    await safe_edit(
        click.event,
        f"📜 **Логи** · `{name}`\n{DIV}\n```\n{body}\n```",
        buttons=[[Button.inline("⬅️ Сервисы", b"sv"), Button.inline("🔄", b"sv")]],
    )


def register(router: CallbackRouter) -> None:
    router.group("sv", admin=True)(handle)
