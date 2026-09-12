"""Показ экрана, который может быть картинкой.

Telegram не умеет превращать текстовое сообщение в медийное и обратно: правка
текстового сообщения с ``file=`` отклоняется, а правка карточки-фото чистым
текстом — тоже. Значит, при смене типа экрана единственный честный путь —
удалить сообщение и отправить заново; при совпадении типа работает обычная
правка, и лента чата не дёргается.

Отсюда же кэш: рендер Pillow идёт в ``asyncio.to_thread`` (общий event loop с
uvicorn), но даже поток стоит сотни миллисекунд, а callback-query живёт ~15 с.
Один и тот же экран, открытый десять раз подряд, обязан рисоваться один раз.
"""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Optional

from ..app_ctx import logger, settings
from .reply import safe_edit

# Подпись под медиа — 1024 символа, обычное сообщение — 4096. Экран, который не
# влезает в подпись, показывается текстом, а не обрезается до бессмыслицы.
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
CARDS_DIR_NAME = "cards"
CARDS_KEEP_DAYS = 1


def fits_caption(text: str) -> bool:
    return len(text) <= CAPTION_LIMIT


def caption_for(text: str) -> str:
    """Обрезать до подписи по границе строки — обрубленное слово читается как баг."""
    if fits_caption(text):
        return text
    cut = text[:CAPTION_LIMIT - 1]
    newline = cut.rfind("\n")
    if newline > CAPTION_LIMIT // 2:
        cut = cut[:newline]
    return cut.rstrip() + "…"


async def _has_media(event) -> bool:
    """Медийное ли сообщение, на котором нажали кнопку."""
    getter = getattr(event, "get_message", None)
    if getter is None:
        return False
    try:
        message = await getter()
    except Exception:
        return False
    return bool(getattr(message, "media", None))


async def show_screen(event, text: str, buttons: Any = None,
                      image: Optional[str] = None) -> None:
    """Показать экран на месте нажатой кнопки, картинкой или текстом.

    Смена типа (текст ↔ медиа) выполняется как удалить+отправить: Telegram не
    даёт заменить одно другим правкой.
    """
    had_media = await _has_media(event)
    if image and had_media:
        with suppress(Exception):
            await event.edit(caption_for(text), file=image, buttons=buttons)
            return
    if image:
        # Текст → картинка: правка невозможна, поэтому старое сообщение уходит.
        with suppress(Exception):
            await event.delete()
        await event.respond(caption_for(text), file=image, buttons=buttons,
                            link_preview=False)
        await _answer_quietly(event)
        return
    if had_media:
        # Картинка → текст: та же история в обратную сторону.
        with suppress(Exception):
            await event.delete()
        await event.respond(text[:TEXT_LIMIT], buttons=buttons, link_preview=False)
        await _answer_quietly(event)
        return
    await safe_edit(event, text[:TEXT_LIMIT], buttons=buttons, link_preview=False)


async def _answer_quietly(event) -> None:
    answer = getattr(event, "answer", None)
    if answer is None:
        return
    with suppress(Exception):
        await answer()


def cards_dir() -> Path:
    """Куда складывать отрисованные экраны (рядом с карточками дайджеста)."""
    return Path(settings.data_dir) / CARDS_DIR_NAME


def cache_key(*parts: Any) -> str:
    """Короткий ключ по данным экрана: одинаковые данные — один файл."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


async def render_cached(name: str, key: str,
                        build: Callable[[Path], Optional[str]]) -> Optional[str]:
    """Путь к отрисованному экрану; рисуется только если такого файла ещё нет.

    ``build`` синхронный и уходит в поток: Pillow на event loop подвесил бы и
    API, и все кнопки бота. Никогда не бросает — не нарисовалось, значит раздел
    покажет текстовый вариант.
    """
    directory = cards_dir()
    path = directory / f"{name}_{key}.png"
    if path.exists():
        return str(path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(build, path)
    except Exception:  # pragma: no cover - рендер best effort
        logger.exception("Screen render failed (%s); falling back to text", name)
        return None
    return result


def prune_cards() -> None:
    """Убрать вчерашние экраны — кэш живёт ровно до смены данных."""
    from .render.canvas import prune

    with suppress(Exception):
        prune(cards_dir(), "*.png", CARDS_KEEP_DAYS)
