"""Действия по розыгрышам, у которых есть последствия в Telegram.

Разбор объявления, отказ от участия, обновление профиля канала и выход из
мёртвого канала жили в ``routers/giveaways.py`` — то есть были доступны только
из веба. Логика здесь одна на обе поверхности: ошибки — доменные
(``GiveawayActionError``), а роутер и бот переводят их каждый в своё (HTTP-код
или alert над кнопкой).

Выход из канала необратим: вернуться в приватный канал без новой ссылки нельзя.
Поэтому функция ничего не решает сама — подтверждение обязано случиться выше.
"""
from __future__ import annotations

from typing import Any, Optional

from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import LeaveChannelRequest

from database import (
    get_giveaway_candidate, get_ping_by_id, record_giveaway_action,
    update_giveaway_candidate_status, update_ping_meta,
)

from . import watch_settings as ws
from .app_ctx import state
from .common import record_app_event
from .giveaway_actions import analyze_and_store_giveaway, find_giveaway_action_client, load_giveaway_message
from .giveaways import inactive_channel_candidate
from .ping_pipeline import refresh_channel_profile


class GiveawayActionError(Exception):
    """Действие не выполнено. `code` — что именно пошло не так."""

    def __init__(self, message: str, code: str = "failed", retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


async def _action_client():
    client = await find_giveaway_action_client()
    if not client:
        raise GiveawayActionError(
            f"Аккаунт действий @{ws.GIVEAWAY_ACTION_ACCOUNT} не в сети", "offline")
    return client


async def analyze(ping_id: int) -> dict[str, Any]:
    """Разобрать объявление: условия, кнопки, оценка. Пишет кандидата в базу."""
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise GiveawayActionError("Запись не найдена", "not_found")
    client = await _action_client()
    try:
        message = await load_giveaway_message(client, ping)
    except Exception:
        # Сообщение могло быть удалено или недоступно этому аккаунту — разбор
        # всё равно имеет смысл по тексту, который уже лежит в базе.
        message = None
    candidate = await analyze_and_store_giveaway(client, ping_id, ping, message)
    if not candidate:
        raise GiveawayActionError("Разбор не удался", "failed")
    return candidate


async def skip(ping_id: int) -> None:
    """Отказаться от участия: кандидат в skipped, запись — в «не отписался»."""
    if not await get_giveaway_candidate(ping_id):
        raise GiveawayActionError("Кандидат не найден", "not_found")
    await update_giveaway_candidate_status(ping_id, "skipped")
    await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
    await record_giveaway_action(ping_id, "skip", "skipped", "admin")


async def cleanup_candidates(limit: int = 100) -> dict[str, Any]:
    """Каналы, которые давно молчат, — кандидаты на выход.

    Проход по диалогам стоит запросов, поэтому лимит здесь не для красоты.
    """
    client = await find_giveaway_action_client()
    if not client:
        return {"action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}", "candidates": [],
                "warning": "Аккаунт действий не в сети"}
    candidates = []
    async for dialog in client.iter_dialogs(limit=limit):
        item = inactive_channel_candidate(dialog, ws.GIVEAWAY_INACTIVE_CHANNEL_DAYS)
        if item:
            candidates.append(item)
    return {
        "action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}",
        "inactive_days": ws.GIVEAWAY_INACTIVE_CHANNEL_DAYS,
        "candidates": candidates,
    }


async def leave_channel(chat_id: int) -> None:
    """Выйти из канала. Необратимо — подтверждение должно быть выше по стеку."""
    client = await _action_client()
    try:
        entity = await client.get_entity(chat_id)
        await client(LeaveChannelRequest(entity))
    except FloodWaitError as exc:
        await record_giveaway_action(None, "leave_channel", "manual_required", "admin",
                                     f"FloodWait {exc.seconds}s", {"chat_id": chat_id})
        raise GiveawayActionError(f"Telegram просит подождать {exc.seconds} с",
                                  "flood_wait", int(exc.seconds)) from exc
    except Exception as exc:
        await record_giveaway_action(None, "leave_channel", "failed", "admin",
                                     str(exc), {"chat_id": chat_id})
        raise GiveawayActionError(str(exc), "failed") from exc
    await record_giveaway_action(None, "leave_channel", "left", "admin",
                                 f"Left channel {chat_id}", {"chat_id": chat_id})
    await record_app_event("WARNING", "giveaway",
                           "Left inactive channel after admin confirmation", {"chat_id": chat_id})


async def refresh_profile(chat_id: int) -> Optional[dict[str, Any]]:
    """Перечитать профиль канала (подписчики, описание) — один GetFullChannel."""
    if not state.clients:
        raise GiveawayActionError("Нет подключённых аккаунтов", "offline")
    profile = await refresh_channel_profile(state.clients[0], chat_id, force=True)
    await record_app_event("INFO", "channels", "Channel profile refreshed manually",
                           {"chat_id": chat_id})
    return profile
