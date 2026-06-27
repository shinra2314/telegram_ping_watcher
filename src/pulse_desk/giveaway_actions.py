"""Safe giveaway participation: analysis, join-button detection, confirmation."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from telethon import TelegramClient

from . import watch_settings as ws
from .app_ctx import logger, state
from .giveaways import analyze_giveaway
from .live import publish_live_event
from .telegram_errors import call_rpc_resilient


async def find_giveaway_action_client() -> Optional[TelegramClient]:
    target = ws.GIVEAWAY_ACTION_ACCOUNT.lower()
    fallback: Optional[TelegramClient] = None
    for client in list(state.clients):
        session_name = str(getattr(client, "_session_name_custom", "") or "").lower()
        if session_name == target:
            fallback = client
        try:
            me = await client.get_me()
        except Exception:
            continue
        username = (getattr(me, "username", "") or "").lower()
        if username == target:
            return client
        if session_name == target:
            fallback = client
    return fallback


async def analyze_and_store_giveaway(client: TelegramClient, ping_id: Optional[int], record: dict[str, Any], message: Any = None) -> Optional[dict[str, Any]]:
    from database import record_giveaway_action, upsert_giveaway_candidate

    if not ping_id or not record.get("is_giveaway"):
        return None
    try:
        candidate = await analyze_giveaway(
            client=client,
            ping_id=int(ping_id),
            text=record.get("text") or "",
            message=message,
            join_keywords=state.join_button_keywords,
            recent_limit=ws.GIVEAWAY_ANALYZE_RECENT_MESSAGES,
        )
        saved = await upsert_giveaway_candidate(candidate)
        await record_giveaway_action(int(ping_id), "analyze", saved.get("status", "pending_review"), "system", context={"score": saved.get("score")})
        await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": saved.get("status"), "score": saved.get("score")})
        return saved
    except Exception as exc:
        logger.exception("Giveaway analysis failed")
        await record_giveaway_action(int(ping_id), "analyze", "failed", "system", str(exc))
        return None


async def load_giveaway_message(client: TelegramClient, ping: dict[str, Any]):
    chat_id = ping.get("chat_id")
    message_id = ping.get("message_id")
    if not chat_id or not message_id:
        raise HTTPException(400, "Ping does not have Telegram chat/message identifiers")
    entity = await call_rpc_resilient(lambda: client.get_entity(int(chat_id)), logger=logger, label="get_entity")
    message = await call_rpc_resilient(lambda: client.get_messages(entity, ids=int(message_id)), logger=logger, label="get_messages")
    if not message:
        raise HTTPException(404, "Telegram message not found or not accessible")
    return message
