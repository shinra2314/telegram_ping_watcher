"""Кнопки со старых сообщений: подчёркнутые формы, которые ещё живут в чате.

Уведомление, отправленное год назад, по-прежнему лежит в чате со своей
клавиатурой, и нажатие обязано что-то ответить — иначе спиннер крутится, пока
клиент не сдастся. Поэтому эти формы не удаляются вместе с фичей: даже снятое
действие отвечает словами, а не тишиной.

Новые экраны такие формы не рисуют — у них ``ping:fav:<id>`` и остальные
``domain:action:arg``.
"""
from __future__ import annotations

from database import mark_ping_read, toggle_favorite

from ..router import CallbackRouter, Click


async def handle_favorite(click: Click) -> None:
    """`fav_<id>` — звезда на карточке уведомления."""
    try:
        ping_id = int(click.tail())
    except ValueError:
        await click.event.answer("Некорректная команда", alert=True)
        return
    await toggle_favorite(ping_id)
    await click.event.answer("Избранное обновлено")


async def handle_read(click: Click) -> None:
    """`read_<id>` — «прочитано»; карточка после этого убирается из чата."""
    try:
        ping_id = int(click.tail())
    except ValueError:
        await click.event.answer("Некорректная команда", alert=True)
        return
    await mark_ping_read(ping_id)
    await click.event.answer("Отмечено как прочитанное")
    await click.event.delete()


async def handle_retired_giveaway(click: Click) -> None:
    """`gconfirm_` / `gskip_` — автоучастие снято в 2026-07, кнопки остались."""
    await click.event.answer("Действие розыгрышей больше недоступно.", alert=True)


def register(router: CallbackRouter) -> None:
    router.group("fav", sep="_", admin=True)(handle_favorite)
    router.group("read", sep="_", admin=True)(handle_read)
    router.group("gconfirm", "gskip", sep="_", admin=True)(handle_retired_giveaway)
