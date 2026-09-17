"""Зарплаты из книги «Учет розыгрышей». Только чтение.

Кто видит — решает ``sections/salary.visible``: владелец видит всех, держатель
ключа — только аккаунт, совпадающий с меткой его ключа. Отказ — 404, как
«Неизвестная команда» в боте: подобранный запрос не должен узнать, что раздел есть.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse_desk import salary as book_api
from pulse_desk.app_ctx import state
from pulse_desk.bot.sections.salary import account_of, month_of, visible

from .common import Caller, current_caller

router = APIRouter()


def plain(value: Any) -> Any:
    """Датаклассы книги и даты — в JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


@router.get("/api/app/salary")
async def salary(caller: Caller = Depends(current_caller),
                 month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
                 account: Optional[str] = Query(None, max_length=64)) -> dict:
    if not await visible(caller.tg_id, caller.role):
        raise HTTPException(status_code=404, detail="Не найдено")
    book = state.salary_book
    key = month_of(book, month)
    months = [{"key": m, "label": book_api.month_label(m)} for m in book_api.month_keys(book)]
    base = {"month": key, "month_label": book_api.month_label(key), "months": months,
            "is_owner": caller.is_admin}
    if caller.is_admin and not account:
        return {**base, "overview": plain(book_api.owner_overview(book, key)),
                "accounts": book.names}
    own = account if caller.is_admin else await account_of(caller.tg_id)
    matched = book_api.match_account(own, book.names) if own else None
    if not matched:
        raise HTTPException(status_code=404, detail="Не найдено")
    return {**base, "account": plain(book_api.account_analytics(book, matched, key))}
