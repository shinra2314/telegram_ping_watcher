"""Изменение одной записи: проверить, записать, разослать, отразить в заметке.

Раньше это жило целиком в ``routers/pings.py``, поэтому «забрал» из веба
дописывал галочку в обсидиановую заметку, а такое же действие из бота — нет.
Пока обе поверхности живы, они обязаны звать одну функцию; когда роутер уйдёт,
здесь ничего не изменится.

Проверка статусов — не формальность: в ``pings`` пишется произвольная строка, и
опечатка в статусе тихо выпадает из каждого фильтра, который по нему ищет.
"""
from __future__ import annotations

from typing import Optional

from database import get_ping_by_id, update_ping_meta

from .app_ctx import logger, settings, state
from .statuses import ACTION_STATUSES, GIVEAWAY_STATUSES, GIVEAWAY_TO_ACTION_STATUS, PING_STATUSES

# Статусы, после которых приз считается взятым — их зеркалим в заметку.
CLAIMED = "claimed"


class UnknownStatus(ValueError):
    """Статуса нет в словаре — записывать его нельзя."""


def validate(status: Optional[str] = None, giveaway_status: Optional[str] = None,
             action_status: Optional[str] = None) -> None:
    if status is not None and status not in PING_STATUSES:
        raise UnknownStatus(f"Unknown status: {status}")
    if giveaway_status is not None and giveaway_status not in GIVEAWAY_STATUSES:
        raise UnknownStatus(f"Unknown giveaway status: {giveaway_status}")
    if action_status is not None and action_status not in ACTION_STATUSES:
        raise UnknownStatus(f"Unknown action status: {action_status}")


def action_for_giveaway(giveaway_status: str) -> Optional[str]:
    """Действие, которое подразумевает статус розыгрыша (веб делал это в JS)."""
    return GIVEAWAY_TO_ACTION_STATUS.get(giveaway_status)


async def apply_ping_meta(
    ping_id: int,
    *,
    status: Optional[str] = None,
    note: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    giveaway_status: Optional[str] = None,
    action_status: Optional[str] = None,
) -> None:
    """Записать изменения и сделать всё, что из них следует."""
    validate(status, giveaway_status, action_status)
    await update_ping_meta(
        ping_id,
        status=status,
        note=note,
        is_favorite=is_favorite,
        giveaway_status=giveaway_status,
        action_status=action_status,
    )
    if giveaway_status is not None or action_status is not None:
        # One prize, one decision: copies of the same winners post follow it.
        from database import propagate_status_to_duplicates

        await propagate_status_to_duplicates(ping_id, giveaway_status, action_status)
    if CLAIMED in (giveaway_status, action_status):
        await _mirror_claim(ping_id)


async def _mirror_claim(ping_id: int) -> None:
    """Поставить галочку в заметке Obsidian — best effort, никогда не ломает запись."""
    if not settings.obsidian_sync_write:
        return
    try:
        from .obsidian_debts import mark_link_done

        ping = await get_ping_by_id(ping_id)
        if ping and ping.get("link"):
            await mark_link_done(state, settings, ping["link"], True)
    except Exception:
        logger.debug("Obsidian note update on status change failed", exc_info=True)
