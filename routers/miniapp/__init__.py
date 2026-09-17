"""Панель (Telegram Mini App): страница и JSON, модуль на раздел.

Подключается ТОЛЬКО к отдельному ASGI-приложению ``miniapp_server.py`` — его
порт публикует туннель, и ничего кроме панели на нём быть не должно.
Новый экран панели — новый модуль здесь плюс экран в ``static/app/screens/``.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import accounts, debts, giveaways, home, market, salary

router = APIRouter()
for _module in (home, giveaways, market, accounts, debts, salary):
    router.include_router(_module.router)
