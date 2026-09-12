"""Служебное владельца: логи и перезапуск мониторинга.

Чтение лога идёт через ``asyncio.to_thread`` и ``tail_lines`` (сик с конца, а не
чтение файла целиком): хендлеры бота и uvicorn делят один event loop, так что
синхронное чтение подвесило бы и API, и все кнопки.

Перезапуск — двухшаговый: ``menu_restart`` рисует подтверждение, работу делает
``adm:restart:go`` в :mod:`.members`.
"""
from __future__ import annotations

import asyncio

from ...app_ctx import LOG_FILE
from ...common import tail_lines
from ..cards import restart_confirm_card
from ..keyboards import logs_keyboard, restart_confirm_keyboard
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV

TAIL_LINES = 20
# Telegram caps a message at 4096 chars; the fence and header need the rest.
TAIL_CHARS = 3500


async def handle_logs(click: Click) -> None:
    if not LOG_FILE.exists():
        await click.event.answer("Логов нет")
        return
    lines = await asyncio.to_thread(tail_lines, LOG_FILE, TAIL_LINES)
    await safe_edit(
        click.event,
        "📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-TAIL_CHARS:] + "\n```",
        buttons=logs_keyboard(),
    )


async def handle_restart(click: Click) -> None:
    await safe_edit(click.event, restart_confirm_card(), buttons=restart_confirm_keyboard())


def register(router: CallbackRouter) -> None:
    router.exact("menu_logs", admin=True)(handle_logs)
    router.exact("menu_restart", admin=True)(handle_restart)
