"""Панель (Telegram Mini App): страница и JSON, модуль на раздел.

Подключается ТОЛЬКО к отдельному ASGI-приложению ``miniapp_server.py`` — его
порт публикует туннель, и ничего кроме панели на нём быть не должно.
Новый экран панели — новый модуль здесь плюс экран в ``static/app/screens/``.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import accounts, analytics, debts, feed, giveaways, home, market, prefs, pulse, salary, triage, undo, wins

router = APIRouter()
for _module in (home, giveaways, feed, market, analytics, prefs, accounts, debts, salary, triage, undo, wins, pulse):
    router.include_router(_module.router)
