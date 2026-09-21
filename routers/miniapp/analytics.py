"""Статистика: сводка, дни, часы, чаты, авторы, аккаунты, задержка.

В боте гость с грантом ``stats``/``analytics`` видит цифры по всей базе. Здесь
отчёт считается только по аккаунтам ключа (``build_panel_report``): ключ,
выданный на один аккаунт, не узнаёт из статистики, что и где выигрывают другие.
Ключ без белого списка покрывает все аккаунты — ему и отчёт общий.

Грант ``stats`` открывает сводку и график по дням, ``analytics`` — всё остальное.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse_desk.analytics import build_panel_report
from pulse_desk.bot.sections.giveaways import visible_accounts

from .common import Caller, current_caller

router = APIRouter()

SUMMARY_KEYS = ("summary", "daily")


@router.get("/api/app/analytics")
async def analytics(caller: Caller = Depends(current_caller), days: int = Query(30)) -> dict:
    full = caller.may("analytics")
    if not (full or caller.may("stats")):
        raise HTTPException(status_code=403, detail="Этот раздел вам не открыт")
    scope = list(caller.perms.get("accounts") or [])
    report = await build_panel_report(scope, visible_accounts(caller.perms), days)
    body = report if full else {key: report[key] for key in SUMMARY_KEYS}
    return {**body, "scope": scope, "full": full}
