"""Кто зовёт панель: проверка initData, роль, гранты.

Каждый JSON-запрос несёт ``initData`` в заголовке ``X-Telegram-Init-Data`` и
проверяется заново — сессии нет, истекать под открытой на телефоне панелью нечему.
Роль и гранты решает ``bot_membership`` — те же правила, что у хендлеров бота,
так что ключ, которому в боте закрыт раздел, не откроет его и здесь.

Порт панели опубликован в интернет, поэтому у изменяющих запросов строже срок:
initData старше ``FRESH_SECONDS`` годится для чтения, но не для действий.
Перехваченная строка не должна сутки управлять аккаунтами.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import Depends, Header, HTTPException

from pulse_desk.app_ctx import BOT_TOKEN
from pulse_desk.bot_membership import resolve_member_access
from pulse_desk.bot_permissions import has_feature
from pulse_desk.miniapp_auth import InitDataError, verify

# Сколько живёт initData для действий. Telegram выдаёт свежую строку при каждом
# открытии панели, так что владельцу это стоит одного переоткрытия в час.
FRESH_SECONDS = 60 * 60

REOPEN = "Откройте панель заново из бота"


class Caller:
    """Проверенный пользователь панели: Telegram id, роль, гранты."""

    def __init__(self, tg_id: int, role: str, perms: dict, name: str, auth_date: int):
        self.tg_id = tg_id
        self.role = role
        self.perms = perms
        self.name = name
        self.auth_date = auth_date

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may(self, feature: str) -> bool:
        return self.is_admin or has_feature(self.perms, feature)

    def require(self, feature: str) -> None:
        if not self.may(feature):
            raise HTTPException(status_code=403, detail="Этот раздел вам не открыт")


async def current_caller(
    x_telegram_init_data: Optional[str] = Header(default=None),
) -> Caller:
    try:
        user = verify(x_telegram_init_data or "", BOT_TOKEN)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail=REOPEN) from exc
    role, perms = await resolve_member_access(user.tg_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Доступ к боту закрыт")
    return Caller(user.tg_id, role, perms, user.first_name or user.username, user.auth_date)


async def admin_caller(caller: Caller = Depends(current_caller)) -> Caller:
    """Владелец. Грант-кодом не открывается: пустые permissions легаси-ключа
    означают «всё», и новый код открыл бы раздел каждому старому ключу."""
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="Только владелец")
    return caller


def require_fresh(caller: Caller, now: Optional[int] = None) -> None:
    age = (now if now is not None else int(time.time())) - caller.auth_date
    if age > FRESH_SECONDS:
        raise HTTPException(status_code=401, detail=f"{REOPEN} — для действий нужна свежая сессия")


async def fresh_admin(caller: Caller = Depends(admin_caller)) -> Caller:
    require_fresh(caller)
    return caller


async def fresh_caller(caller: Caller = Depends(current_caller)) -> Caller:
    require_fresh(caller)
    return caller
