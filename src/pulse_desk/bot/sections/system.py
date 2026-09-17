"""Служебное владельца: логи и перезапуск мониторинга.

Чтение лога идёт через ``asyncio.to_thread`` и ``tail_lines`` (сик с конца, а не
чтение файла целиком): хендлеры бота и uvicorn делят один event loop, так что
синхронное чтение подвесило бы и API, и все кнопки.

Логи — три вида: последние строки, «⚠️ только проблемы» (WARNING/ERROR вместе с
их трейсбеками — в двадцати последних строках ошибка обычно уже уехала) и
«📎 файлом» — хвост лога документом, когда разбираться надо по-настоящему.

Перезапуск — двухшаговый: ``menu_restart`` рисует подтверждение, работу делает
``adm:restart:go`` в :mod:`.members`.
"""
from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime
from pathlib import Path

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
# How far back «только проблемы» looks.
PROBLEM_SCAN_LINES = 2000
# The file export: enough for a day of normal logging, small for a chat.
FILE_TAIL_BYTES = 2 * 1024 * 1024

_STAMPED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_PROBLEM = re.compile(r"\[(WARNING|ERROR|CRITICAL)\]")


def problem_lines(lines: list[str]) -> list[str]:
    """WARNING/ERROR records with their continuation lines (tracebacks).

    A record starts with a timestamp; anything without one belongs to the
    record above it, so a traceback stays attached to its error.
    """
    out: list[str] = []
    keep = False
    for line in lines:
        if _STAMPED.match(line):
            keep = bool(_PROBLEM.search(line))
        if keep:
            out.append(line)
    return out


def tail_bytes(path: Path, limit: int = FILE_TAIL_BYTES) -> bytes:
    """The last ``limit`` bytes of a file, starting at a line boundary."""
    size = path.stat().st_size
    with open(path, "rb") as handle:
        handle.seek(max(0, size - limit))
        data = handle.read()
    if size > limit:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else data
    return data


def _fenced(title: str, lines: list[str]) -> str:
    body = "\n".join(lines)[-TAIL_CHARS:] or "—"
    return f"{title}\n{DIV}\n```\n{body}\n```"


async def handle_logs(click: Click) -> None:
    if not LOG_FILE.exists():
        await click.event.answer("Логов нет")
        return
    lines = await asyncio.to_thread(tail_lines, LOG_FILE, TAIL_LINES)
    await safe_edit(click.event, _fenced("📜 **Последние логи**", lines), buttons=logs_keyboard())


async def handle_log_views(click: Click) -> None:
    """`lg:err` — только проблемы, `lg:file` — хвост лога документом."""
    if not LOG_FILE.exists():
        await click.event.answer("Логов нет")
        return
    action = click.arg(1)
    if action == "file":
        payload = await asyncio.to_thread(tail_bytes, LOG_FILE)
        buffer = io.BytesIO(payload)
        buffer.name = f"app_{datetime.now():%Y%m%d_%H%M}.log"
        await click.event.respond(f"📎 Хвост лога · {len(payload) / 1024:.0f} КБ", file=buffer)
        await click.event.answer()
        return
    lines = problem_lines(await asyncio.to_thread(tail_lines, LOG_FILE, PROBLEM_SCAN_LINES))
    if not lines:
        await click.event.answer(f"В последних {PROBLEM_SCAN_LINES} строках проблем нет", alert=True)
        return
    await safe_edit(click.event, _fenced("⚠️ **Проблемы в логе**", lines), buttons=logs_keyboard())


async def handle_restart(click: Click) -> None:
    await safe_edit(click.event, restart_confirm_card(), buttons=restart_confirm_keyboard())


def register(router: CallbackRouter) -> None:
    router.exact("menu_logs", admin=True)(handle_logs)
    router.group("lg", admin=True)(handle_log_views)
    router.exact("menu_restart", admin=True)(handle_restart)
