"""Safe giveaway participation: analysis, join-button detection, confirmation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException
from telethon import TelegramClient, types
from telethon.errors import FloodWaitError

from . import watch_settings as ws
from .app_ctx import logger, state
from .giveaways import analyze_giveaway
from .live import publish_live_event
from .telegram_errors import call_rpc_resilient


def _button_is_safe_join(button: Any) -> bool:
    if not isinstance(button, types.KeyboardButtonCallback):
        return False
    button_text = (getattr(button, "text", "") or "").lower()
    return any(keyword.lower() in button_text for keyword in state.join_button_keywords)


def _find_safe_join_button(message: Any) -> Optional[tuple[int, int, str]]:
    markup = getattr(message, "reply_markup", None)
    if not isinstance(markup, types.ReplyInlineMarkup):
        return None
    for row_index, row in enumerate(markup.rows):
        for button_index, button in enumerate(row.buttons):
            if _button_is_safe_join(button):
                return row_index, button_index, getattr(button, "text", "") or "join"
    return None


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


async def confirm_safe_giveaway_join(ping_id: int, actor: str = "admin") -> dict[str, Any]:
    from database import (
        get_giveaway_candidate,
        get_ping_by_id,
        record_giveaway_action,
        update_giveaway_candidate_status,
        update_ping_meta,
    )

    candidate = await get_giveaway_candidate(ping_id)
    if not candidate:
        raise HTTPException(404, "Giveaway candidate not found")
    if candidate.get("status") in {"manual_required", "blocked"} or candidate.get("blocked_reason"):
        await record_giveaway_action(ping_id, "confirm", "blocked", actor, candidate.get("blocked_reason") or "Manual review required")
        raise HTTPException(409, candidate.get("blocked_reason") or "Manual review required")
    client = await find_giveaway_action_client()
    if not client:
        await record_giveaway_action(ping_id, "confirm", "failed", actor, f"Action account @{ws.GIVEAWAY_ACTION_ACCOUNT} is not online")
        raise HTTPException(400, f"Action account @{ws.GIVEAWAY_ACTION_ACCOUNT} is not online")
    ping = await get_ping_by_id(ping_id)
    if not ping:
        raise HTTPException(404, "Ping not found")
    try:
        message = await load_giveaway_message(client, ping)
        button = _find_safe_join_button(message)
        if not button:
            await update_giveaway_candidate_status(ping_id, "manual_required", "No safe Telegram callback join button found")
            await record_giveaway_action(ping_id, "confirm", "manual_required", actor, "No safe Telegram callback join button found")
            raise HTTPException(409, "No safe Telegram callback join button found")
        row_index, button_index, button_text = button
        if ws.DRY_RUN_GIVEAWAYS:
            dry_run_message = f"DRY_RUN_GIVEAWAYS is enabled; would click button: {button_text}"
            await record_giveaway_action(ping_id, "confirm", "dry_run", actor, dry_run_message, {"account": ws.GIVEAWAY_ACTION_ACCOUNT})
            await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "dry_run"})
            return {"status": "dry_run", "message": dry_run_message}
        now = datetime.now()
        if state.last_giveaway_action_at:
            elapsed = (now - state.last_giveaway_action_at).total_seconds()
            remaining = ws.GIVEAWAY_MIN_ACTION_DELAY_SECONDS - elapsed
            if remaining > 0:
                message = f"Safe delay is active; retry in {int(remaining) + 1}s"
                await record_giveaway_action(ping_id, "confirm", "delayed", actor, message)
                raise HTTPException(429, message)
        await update_giveaway_candidate_status(ping_id, "confirmed")
        await record_giveaway_action(ping_id, "confirm", "confirmed", actor, f"Clicked button: {button_text}", {"account": ws.GIVEAWAY_ACTION_ACCOUNT})
        await message.click(row_index, button_index)
        state.last_giveaway_action_at = datetime.now()
        await update_giveaway_candidate_status(ping_id, "joined")
        await update_ping_meta(ping_id, giveaway_status="pending", action_status="waiting_result")
        await record_giveaway_action(ping_id, "join_button", "joined", actor, f"Clicked button: {button_text}", {"account": ws.GIVEAWAY_ACTION_ACCOUNT})
        await publish_live_event("giveaway-candidate", {"ping_id": ping_id, "status": "joined"})
        return {"status": "joined", "message": f"Clicked safe Telegram join button as @{ws.GIVEAWAY_ACTION_ACCOUNT}"}
    except FloodWaitError as exc:
        reason = f"Telegram FloodWait {exc.seconds}s; manual retry required"
        await update_giveaway_candidate_status(ping_id, "manual_required", reason)
        await record_giveaway_action(ping_id, "confirm", "manual_required", actor, reason, {"seconds": exc.seconds})
        raise HTTPException(429, reason) from exc
    except HTTPException:
        raise
    except Exception as exc:
        await update_giveaway_candidate_status(ping_id, "failed", str(exc))
        await record_giveaway_action(ping_id, "confirm", "failed", actor, str(exc))
        raise HTTPException(500, str(exc)) from exc
