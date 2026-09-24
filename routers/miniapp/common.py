"""Кто зовёт панель: проверка initData, роль, гранты.

Каждый JSON-запрос несёт ``initData`` в заголовке ``X-Telegram-Init-Data`` и
проверяется заново — сессии нет, истекать под открытой на телефоне панелью нечему.
Роль и гранты решает ``bot_membership`` — те же правила, что у хендлеров бота,
так что ключ, которому в боте закрыт раздел, не откроет его и здесь.

Порт панели опубликован в интернет, поэтому у изменяющих запросов строже срок:
initData старше ``FRESH_SECONDS`` годится для чтения, но не для действий.
Перехваченная строка не должна сутки управлять аккаунтами.

Там же — частота. Проверенный пользователь получает бюджет запросов в минуту
(``LIMITS``), считается он после подписи: до неё ``tg_id`` ничего не стоит.
Кто позвал — кладётся в ``request.state.caller``, чтобы журнал действий в
``miniapp_server`` знал, чьё это было действие.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Hashable, Optional

from fastapi import Depends, Header, HTTPException, Request

from pulse_desk.app_ctx import BOT_TOKEN, logger
from pulse_desk.bot_membership import resolve_member_access
from pulse_desk.bot_permissions import has_feature
from pulse_desk.miniapp_auth import InitDataError, verify

# Сколько живёт initData для действий. Telegram выдаёт свежую строку при каждом
# открытии панели, так что владельцу это стоит одного переоткрытия в час.
FRESH_SECONDS = 60 * 60

REOPEN = "Откройте панель заново из бота"
TOO_FAST = "Слишком часто — подождите минуту"

# Запросов в минуту на человека: (чтение, действия). Владельцу действий больше —
# разбор тридцати побед подряд — это тридцать POST за пару минут.
LIMITS = {"admin": (120, 60), "guest": (60, 10)}
# Неподписанных запросов в минуту на всех, после чего они получают 429, а в
# журнал уходит одно предупреждение за окно. Чужих не пускает и 401 — это
# сигнал «кто-то перебирает», а не замок.
AUTH_FAILURES_PER_MINUTE = 120


class RateLimiter:
    """Сколько раз ключ стучался за последние ``window`` секунд. Чистый, часы — аргумент."""

    def __init__(self, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.window = window
        self.clock = clock
        self.hits: dict[Hashable, deque[float]] = {}
        self._swept_at = clock()

    def allow(self, key: Hashable, limit: int) -> bool:
        now = self.clock()
        self._forget_idle(now)
        hits = self.hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self.hits.clear()

    def _forget_idle(self, now: float) -> None:
        """Once a window, drop the keys nobody used within it: the process runs
        for days, and every person who ever opened the panel kept an entry."""
        if now - self._swept_at < self.window:
            return
        self._swept_at = now
        for key in [key for key, hits in self.hits.items() if not hits or now - hits[-1] >= self.window]:
            del self.hits[key]


LIMITER = RateLimiter()
_failure_warned_at: dict[str, float] = {"at": -1e9}


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
    request: Request,
    x_telegram_init_data: Optional[str] = Header(default=None),
) -> Caller:
    try:
        user = verify(x_telegram_init_data or "", BOT_TOKEN)
    except InitDataError as exc:
        if not LIMITER.allow("auth-failure", AUTH_FAILURES_PER_MINUTE):
            now = LIMITER.clock()
            if now - _failure_warned_at["at"] >= LIMITER.window:
                _failure_warned_at["at"] = now
                logger.warning("Mini App: more than %s unsigned requests a minute", AUTH_FAILURES_PER_MINUTE)
            raise HTTPException(status_code=429, detail=TOO_FAST) from exc
        raise HTTPException(status_code=401, detail=REOPEN) from exc
    role, perms = await resolve_member_access(user.tg_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Доступ к боту закрыт")
    reads, writes = LIMITS["admin" if role == "admin" else "guest"]
    acting = request.method not in ("GET", "HEAD")
    if not LIMITER.allow((user.tg_id, acting), writes if acting else reads):
        raise HTTPException(status_code=429, detail=TOO_FAST)
    caller = Caller(user.tg_id, role, perms, user.first_name or user.username, user.auth_date)
    request.state.caller = caller
    return caller


def audit(request: Request, **details: Any) -> None:
    """Что именно сделало действие — для журнала (``miniapp_server``).

    Только идентификаторы: ни номера, ни кода, ни пароля, ни секрета ключа.
    Путь запроса журнал пишет и сам; сюда — то, что лежит в теле (список id).
    """
    extra = getattr(request.state, "audit", None) or {}
    extra.update(details)
    request.state.audit = extra


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
