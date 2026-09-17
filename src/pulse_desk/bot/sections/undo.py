"""`ud:<token>` — the «↩️ Отменить» button that follows a status change.

The rows go back to their snapshot (see :mod:`..undo`), then the screen the
action was taken on is redrawn by dispatching its own callback, so the owner
sees the restored state rather than the card that still says «забрал».
"""
from __future__ import annotations

from typing import Optional

from ..router import CallbackRouter, Click
from ..undo import undo

_router: Optional[CallbackRouter] = None


async def handle(click: Click) -> None:
    result = await undo(click.sender_id, click.arg(1))
    if result is None:
        await click.event.answer("Отменить уже нельзя — прошло больше 30 секунд", alert=True)
        return
    restored, back = result
    await click.event.answer(f"↩️ Отменено: {restored}")
    if back and _router is not None:
        await _router.dispatch(Click(click.event, back, click.role, click.perms, click.has_feature))


def register(router: CallbackRouter) -> None:
    global _router
    _router = router
    router.group("ud", admin=True)(handle)
