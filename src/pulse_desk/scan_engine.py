"""History scanning engine: full sweeps, fast-channel mode, mention backfill."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from telegram_ping_watcher import chat_type_from_entity

from . import watch_settings as ws
from .app_ctx import logger, state
from .common import flood_wait_seconds, now_iso, record_app_event
from .ping_pipeline import process_ping_message, resolve_ping_user_ids
from .scan import channel_sweep_start_id, normalize_scan_history_limit
from .telegram_accounts import mark_account_cooldown, mark_auth_key_duplicated
from .telegram_errors import (
    auth_key_duplicated_message,
    is_auth_key_duplicated,
    is_channel_inaccessible,
    iter_messages_resilient,
)

scan_status = state.scan_status


def channel_checkpoint_key(username: str, chat_id: Any) -> str:
    return f"{username}|channel:{chat_id}"


async def list_broadcast_channel_dialogs(client: TelegramClient) -> list[Any]:
    dialogs: list[Any] = []
    async for dialog in client.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if chat_type_from_entity(entity) == "channel":
            dialogs.append(dialog)
    return dialogs


async def load_channel_username_checkpoints(session_name: str, chat_id: Any) -> dict[str, int]:
    from database import get_checkpoints, get_latest_checkpoints

    checkpoint_keys = {username: channel_checkpoint_key(username, chat_id) for username in state.ping_usernames}
    saved = await get_checkpoints(session_name, list(checkpoint_keys.values()))
    missing_keys = [key for key in checkpoint_keys.values() if not saved.get(key)]
    seeded = await get_latest_checkpoints(missing_keys) if missing_keys else {}
    return {username: int(saved.get(key) or seeded.get(key) or 0) for username, key in checkpoint_keys.items()}


async def save_channel_username_checkpoints(
    session_name: str,
    chat_id: Any,
    checkpoint_by_username: dict[str, int],
    last_message_id: int,
) -> None:
    from database import save_checkpoints

    if last_message_id <= 0:
        return
    updates = {
        channel_checkpoint_key(username, chat_id): int(last_message_id)
        for username, current_id in checkpoint_by_username.items()
        if int(last_message_id) > int(current_id or 0)
    }
    await save_checkpoints(session_name, updates)


async def scan_single_account(client: TelegramClient, limit: Optional[int] = None) -> int:
    from database import get_ping_by_message_ref, save_checkpoint, update_scan_run

    session_name = getattr(client, "_session_name_custom", "unknown")
    found = 0
    history_limit = ws.SCAN_HISTORY_LIMIT if limit is None else normalize_scan_history_limit(limit)
    iter_limit = None if history_limit <= 0 else history_limit

    async def mark_processed_units(units: int = 1) -> None:
        scan_status["processed_usernames"] += units
        if scan_status.get("scan_run_id"):
            await update_scan_run(
                int(scan_status["scan_run_id"]),
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
            )

    async def scan_recent_channel_window(entity: Any, chat_id: Any, user_label: str) -> int:
        if ws.EDIT_SCAN_RECENT_MESSAGES <= 0:
            return 0
        recent_found = 0
        scanned_messages = 0
        try:
            async for message in iter_messages_resilient(client, entity, limit=ws.EDIT_SCAN_RECENT_MESSAGES, logger=logger):
                if state.scan_cancel_event.is_set():
                    break
                scanned_messages += 1
                if await get_ping_by_message_ref(chat_id, getattr(message, "id", None)):
                    continue
                ping_id = await process_ping_message(
                    client,
                    message,
                    account_label=user_label,
                    notify=True,
                    source="recent-edit-sweep",
                )
                if ping_id:
                    recent_found += 1
        except FloodWaitError as exc:
            logger.warning("Flood wait during recent edit sweep in %s: %s seconds", chat_id, exc.seconds)
            scan_status["last_error"] = f"Flood wait {exc.seconds}s in recent edit sweep"
            await record_app_event("WARNING", "scan", "Telegram flood wait during recent edit sweep", {"chat_id": chat_id, "seconds": exc.seconds})
            wait = flood_wait_seconds(exc.seconds)
            mark_account_cooldown(session_name, wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            if is_auth_key_duplicated(exc):
                scan_status["last_error"] = auth_key_duplicated_message(session_name)
                state.scan_cancel_event.set()
                await mark_auth_key_duplicated(session_name, client, exc)
                return recent_found
            if is_channel_inaccessible(exc):
                logger.debug("Recent edit sweep skipped inaccessible channel %s: %s", chat_id, exc)
                return recent_found
            scan_status["last_error"] = str(exc)
            await record_app_event("WARNING", "scan", "Recent edit sweep failed", {"chat_id": chat_id, "error": str(exc)})
            logger.warning("Recent edit sweep failed for %s: %s", chat_id, exc)
        finally:
            scan_status["edit_sweep_messages"] = int(scan_status.get("edit_sweep_messages") or 0) + scanned_messages
        return recent_found

    try:
        me = await client.get_me()
        user_label = me.username or str(me.id)
        scan_status["current_account"] = user_label
        dialogs = await list_broadcast_channel_dialogs(client)
        account_state = state.accounts_state.setdefault(session_name, {"session_name": session_name})
        account_state.update({
            "channels_total": len(dialogs),
            "last_channel_scan_at": now_iso(),
        })
        scan_status["total_channels"] = int(scan_status.get("total_channels") or 0) + len(dialogs)
        expected_units = len(dialogs) * len(state.ping_usernames)
        scan_status["total_usernames"] = int(scan_status.get("total_usernames") or 0) + expected_units
        if scan_status.get("scan_run_id"):
            await update_scan_run(
                int(scan_status["scan_run_id"]),
                total_usernames=scan_status["total_usernames"],
                last_error=scan_status.get("last_error"),
            )
        for dialog in dialogs:
            if state.scan_cancel_event.is_set():
                break
            entity = dialog.entity
            chat_id = getattr(entity, "id", None)
            if chat_id is None:
                continue
            latest_message_id = int(getattr(getattr(dialog, "message", None), "id", 0) or 0)
            scan_status["current_channel"] = getattr(dialog, "name", None) or str(chat_id)
            checkpoint_by_username = await load_channel_username_checkpoints(session_name, chat_id)
            sweep_start_id = channel_sweep_start_id(checkpoint_by_username)
            if sweep_start_id is not None:
                scan_status["fast_channels"] = int(scan_status.get("fast_channels") or 0) + 1
                scan_status["current_username"] = "all"
                new_last_id = max(int(sweep_start_id or 0), latest_message_id)
                try:
                    async for message in iter_messages_resilient(client, entity, min_id=sweep_start_id, limit=iter_limit, logger=logger):
                        if state.scan_cancel_event.is_set():
                            break
                        new_last_id = max(new_last_id, int(getattr(message, "id", 0) or 0))
                        ping_id = await process_ping_message(client, message, account_label=user_label, notify=True)
                        if ping_id:
                            found += 1
                            scan_status["found"] += 1
                    if not state.scan_cancel_event.is_set():
                        await save_channel_username_checkpoints(session_name, chat_id, checkpoint_by_username, new_last_id)
                except FloodWaitError as exc:
                    logger.warning("Flood wait during fast channel sweep in %s: %s seconds", chat_id, exc.seconds)
                    scan_status["last_error"] = f"Flood wait {exc.seconds}s in fast channel sweep"
                    await record_app_event("WARNING", "scan", "Telegram flood wait during fast channel sweep", {"chat_id": chat_id, "seconds": exc.seconds})
                    wait = flood_wait_seconds(exc.seconds)
                    mark_account_cooldown(session_name, wait)
                    await asyncio.sleep(wait)
                except Exception as exc:
                    if is_auth_key_duplicated(exc):
                        scan_status["last_error"] = auth_key_duplicated_message(session_name)
                        state.scan_cancel_event.set()
                        await mark_auth_key_duplicated(session_name, client, exc)
                        break
                    if is_channel_inaccessible(exc):
                        # Permanent: skip this channel and its recent-window sweep.
                        # The finally block still records processed units.
                        logger.debug("Fast channel sweep skipped inaccessible channel %s in %s: %s", chat_id, session_name, exc)
                        continue
                    scan_status["last_error"] = str(exc)
                    await record_app_event("WARNING", "scan", "Fast channel sweep failed", {"session": session_name, "chat_id": chat_id, "error": str(exc)})
                    logger.warning("Fast channel sweep failed for %s in %s: %s", chat_id, session_name, exc)
                finally:
                    await mark_processed_units(len(state.ping_usernames))
                recent_found = await scan_recent_channel_window(entity, chat_id, user_label)
                if recent_found:
                    found += recent_found
                    scan_status["found"] += recent_found
                continue

            for username in state.ping_usernames:
                if state.scan_cancel_event.is_set():
                    break
                checkpoint_key = channel_checkpoint_key(username, chat_id)
                last_id = checkpoint_by_username.get(username, 0)
                scan_status["targeted_channels"] = int(scan_status.get("targeted_channels") or 0) + 1
                scan_status["current_username"] = username
                new_last_id = max(int(last_id or 0), latest_message_id)
                try:
                    async for message in iter_messages_resilient(client, entity, search=f"@{username}", min_id=last_id, limit=iter_limit, logger=logger):
                        if state.scan_cancel_event.is_set():
                            break
                        new_last_id = max(new_last_id, int(getattr(message, "id", 0) or 0))
                        ping_id = await process_ping_message(client, message, account_label=user_label, notify=True)
                        if ping_id:
                            found += 1
                            scan_status["found"] += 1
                    if not state.scan_cancel_event.is_set() and new_last_id > last_id:
                        await save_checkpoint(session_name, checkpoint_key, new_last_id)
                        checkpoint_by_username[username] = new_last_id
                except FloodWaitError as exc:
                    logger.warning("Flood wait during targeted scan in %s for @%s: %s seconds", chat_id, username, exc.seconds)
                    scan_status["last_error"] = f"Flood wait {exc.seconds}s for @{username}"
                    await record_app_event("WARNING", "scan", "Telegram flood wait during targeted scan", {"chat_id": chat_id, "username": username, "seconds": exc.seconds})
                    wait = flood_wait_seconds(exc.seconds)
                    mark_account_cooldown(session_name, wait)
                    await asyncio.sleep(wait)
                except Exception as exc:
                    if is_auth_key_duplicated(exc):
                        scan_status["last_error"] = auth_key_duplicated_message(session_name)
                        state.scan_cancel_event.set()
                        await mark_auth_key_duplicated(session_name, client, exc)
                        break
                    if is_channel_inaccessible(exc):
                        # Permanent for the whole channel — stop trying other usernames here.
                        logger.debug("Targeted scan skipped inaccessible channel %s in %s: %s", chat_id, session_name, exc)
                        break
                    scan_status["last_error"] = str(exc)
                    await record_app_event("WARNING", "scan", "Targeted username scan failed", {"session": session_name, "chat_id": chat_id, "username": username, "error": str(exc)})
                    logger.warning("Targeted username scan failed for %s/%s in %s: %s", chat_id, username, session_name, exc)
                finally:
                    await mark_processed_units()
            if state.scan_cancel_event.is_set():
                break
            recent_found = await scan_recent_channel_window(entity, chat_id, user_label)
            if recent_found:
                found += recent_found
                scan_status["found"] += recent_found
        logger.info("Channel scan finished for %s, found %s", user_label, found)
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            scan_status["last_error"] = auth_key_duplicated_message(session_name)
            state.scan_cancel_event.set()
            await mark_auth_key_duplicated(session_name, client, exc)
            return found
        scan_status["last_error"] = str(exc)
        await record_app_event("ERROR", "scan", "Account scan failed", {"session": session_name, "error": str(exc)})
        logger.exception("Account scan failed: %s", session_name)
    return found


async def full_history_scan() -> None:
    from database import start_scan_run, update_scan_run

    if state.scan_lock.locked():
        logger.info("History scan skipped: already running.")
        return
    async with state.scan_lock:
        state.scan_cancel_event.clear()
        scan_run_id = await start_scan_run(len(state.clients), 0)
        scan_status.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "current_account": None,
            "current_username": None,
            "current_channel": None,
            "total_accounts": len(state.clients),
            "processed_accounts": 0,
            "total_channels": 0,
            "total_usernames": 0,
            "processed_usernames": 0,
            "found": 0,
            "fast_channels": 0,
            "targeted_channels": 0,
            "edit_sweep_messages": 0,
            "scan_strategy": "adaptive-fast-channel-sweep",
            "history_limit": ws.SCAN_HISTORY_LIMIT,
            "last_error": None,
            "scan_run_id": scan_run_id,
            "cancel_requested": False,
        })
        await record_app_event("INFO", "scan", "History scan started", {"scan_run_id": scan_run_id, "accounts": len(state.clients)})
        try:
            semaphore = asyncio.Semaphore(max(1, min(ws.SCAN_ACCOUNT_CONCURRENCY, len(state.clients))))

            async def scan_with_limit(client: TelegramClient) -> int:
                async with semaphore:
                    if state.scan_cancel_event.is_set():
                        return 0
                    return await scan_single_account(client)

            tasks = [asyncio.create_task(scan_with_limit(client)) for client in list(state.clients)]
            for task in asyncio.as_completed(tasks):
                if state.scan_cancel_event.is_set():
                    break
                found = await task
                scan_status["processed_accounts"] += 1
                await update_scan_run(
                    scan_run_id,
                    processed_accounts=scan_status["processed_accounts"],
                    processed_usernames=scan_status["processed_usernames"],
                    found=scan_status["found"],
                    last_error=scan_status.get("last_error"),
                )
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            status_value = "cancelled" if state.scan_cancel_event.is_set() else "finished"
            scan_status.update({
                "running": False,
                "finished_at": now_iso(),
                "current_account": None,
                "current_username": None,
                "current_channel": None,
                "cancel_requested": state.scan_cancel_event.is_set(),
            })
            await update_scan_run(
                scan_run_id,
                status=status_value,
                finished_at=scan_status["finished_at"],
                processed_accounts=scan_status["processed_accounts"],
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
                cancel_requested=1 if state.scan_cancel_event.is_set() else 0,
            )
            await record_app_event("INFO", "scan", f"History scan {status_value}", {"scan_run_id": scan_run_id, "found": scan_status["found"]})
            state.scan_cancel_event.clear()


async def backfill_account_name_mentions(client: TelegramClient, per_channel_limit: int) -> int:
    """Re-read one account's channel history without checkpoints or @username
    search, so pings delivered as text-mentions (name links) are re-evaluated.
    Messages already stored are skipped; historical hits raise no notifications.
    """
    from database import get_ping_by_message_ref, update_scan_run

    session_name = getattr(client, "_session_name_custom", "unknown")
    found = 0
    iter_limit = None if per_channel_limit <= 0 else per_channel_limit
    try:
        me = await client.get_me()
        user_label = me.username or str(me.id)
        scan_status["current_account"] = user_label
        await resolve_ping_user_ids(client)
        dialogs = await list_broadcast_channel_dialogs(client)
        scan_status["total_channels"] = int(scan_status.get("total_channels") or 0) + len(dialogs)
        scan_status["total_usernames"] = int(scan_status.get("total_usernames") or 0) + len(dialogs)
        for dialog in dialogs:
            if state.scan_cancel_event.is_set():
                break
            entity = dialog.entity
            chat_id = getattr(entity, "id", None)
            if chat_id is None:
                continue
            scan_status["current_channel"] = getattr(dialog, "name", None) or str(chat_id)
            try:
                async for message in iter_messages_resilient(client, entity, limit=iter_limit, logger=logger):
                    if state.scan_cancel_event.is_set():
                        break
                    if await get_ping_by_message_ref(chat_id, getattr(message, "id", None)):
                        continue
                    ping_id = await process_ping_message(
                        client,
                        message,
                        account_label=user_label,
                        notify=False,
                        source="mention-backfill",
                    )
                    if ping_id:
                        found += 1
                        scan_status["found"] += 1
            except FloodWaitError as exc:
                logger.warning("Flood wait during mention backfill in %s: %s seconds", chat_id, exc.seconds)
                scan_status["last_error"] = f"Flood wait {exc.seconds}s in mention backfill"
                await record_app_event("WARNING", "scan", "Telegram flood wait during mention backfill", {"chat_id": chat_id, "seconds": exc.seconds})
                wait = flood_wait_seconds(exc.seconds)
                mark_account_cooldown(session_name, wait)
                await asyncio.sleep(wait)
            except Exception as exc:
                if is_auth_key_duplicated(exc):
                    scan_status["last_error"] = auth_key_duplicated_message(session_name)
                    state.scan_cancel_event.set()
                    await mark_auth_key_duplicated(session_name, client, exc)
                    break
                if is_channel_inaccessible(exc):
                    logger.debug("Mention backfill skipped inaccessible channel %s in %s: %s", chat_id, session_name, exc)
                    continue
                scan_status["last_error"] = str(exc)
                await record_app_event("WARNING", "scan", "Mention backfill failed", {"session": session_name, "chat_id": chat_id, "error": str(exc)})
                logger.warning("Mention backfill failed for %s in %s: %s", chat_id, session_name, exc)
            finally:
                scan_status["processed_usernames"] += 1
                if scan_status.get("scan_run_id"):
                    await update_scan_run(
                        int(scan_status["scan_run_id"]),
                        processed_usernames=scan_status["processed_usernames"],
                        found=scan_status["found"],
                        last_error=scan_status.get("last_error"),
                    )
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            scan_status["last_error"] = auth_key_duplicated_message(session_name)
            await mark_auth_key_duplicated(session_name, client, exc)
        else:
            scan_status["last_error"] = str(exc)
            await record_app_event("ERROR", "scan", "Mention backfill account failed", {"session": session_name, "error": str(exc)})
            logger.exception("Mention backfill account failed: %s", session_name)
    return found


async def backfill_name_mention_scan(per_channel_limit: int = 1000) -> None:
    """Surface pings missed before text-mention detection existed by re-reading
    channel history across all accounts. Reuses the scan lock and status so the
    dashboard shows progress and the existing cancel button works.
    """
    from database import start_scan_run, update_scan_run

    if state.scan_lock.locked():
        logger.info("Mention backfill skipped: a scan is already running.")
        return
    async with state.scan_lock:
        state.scan_cancel_event.clear()
        scan_run_id = await start_scan_run(len(state.clients), 0)
        scan_status.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "current_account": None,
            "current_username": None,
            "current_channel": None,
            "total_accounts": len(state.clients),
            "processed_accounts": 0,
            "total_channels": 0,
            "total_usernames": 0,
            "processed_usernames": 0,
            "found": 0,
            "fast_channels": 0,
            "targeted_channels": 0,
            "edit_sweep_messages": 0,
            "scan_strategy": "name-mention-backfill",
            "history_limit": per_channel_limit,
            "last_error": None,
            "scan_run_id": scan_run_id,
            "cancel_requested": False,
        })
        await record_app_event("INFO", "scan", "Name-mention backfill started", {"scan_run_id": scan_run_id, "per_channel_limit": per_channel_limit})
        try:
            for client in list(state.clients):
                if state.scan_cancel_event.is_set():
                    break
                await backfill_account_name_mentions(client, per_channel_limit)
                scan_status["processed_accounts"] += 1
                await update_scan_run(
                    scan_run_id,
                    processed_accounts=scan_status["processed_accounts"],
                    processed_usernames=scan_status["processed_usernames"],
                    found=scan_status["found"],
                    last_error=scan_status.get("last_error"),
                )
        finally:
            status_value = "cancelled" if state.scan_cancel_event.is_set() else "finished"
            scan_status.update({
                "running": False,
                "finished_at": now_iso(),
                "current_account": None,
                "current_username": None,
                "current_channel": None,
                "cancel_requested": state.scan_cancel_event.is_set(),
            })
            await update_scan_run(
                scan_run_id,
                status=status_value,
                finished_at=scan_status["finished_at"],
                processed_accounts=scan_status["processed_accounts"],
                processed_usernames=scan_status["processed_usernames"],
                found=scan_status["found"],
                last_error=scan_status.get("last_error"),
                cancel_requested=1 if state.scan_cancel_event.is_set() else 0,
            )
            await record_app_event("INFO", "scan", f"Name-mention backfill {status_value}", {"scan_run_id": scan_run_id, "found": scan_status["found"]})
            state.scan_cancel_event.clear()
