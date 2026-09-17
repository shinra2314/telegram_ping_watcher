"""«↩️ Отменить» — a 30-second undo for status changes made from the bot.

A mis-tap on «забрал» moved a win off the debts board, and the mass «забрать
отмеченное» could close ten rows at once; the only way back was finding each
row again and setting the old status by hand. Before such an action the
section takes a snapshot of the rows it is about to change; the refreshed
screen carries an undo button for ``UNDO_TTL_SECONDS``.

One undo per person — the newest action replaces the previous one. Snapshots
live in ``state.bot_undo`` (in-memory on purpose: an undo that survives a
restart would restore a state the owner has long since moved past).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional

from telethon import Button

from ..app_ctx import logger, settings, state

UNDO_TTL_SECONDS = 30
FIELDS = ("status", "giveaway_status", "action_status")
CLAIMED = "claimed"


def _is_claimed(row: dict) -> bool:
    return CLAIMED in (row.get("giveaway_status"), row.get("action_status"))


async def snapshot(ping_ids: Iterable[int]) -> dict[int, dict]:
    from database import get_ping_by_id

    rows: dict[int, dict] = {}
    for ping_id in ping_ids:
        ping = await get_ping_by_id(int(ping_id))
        if ping:
            rows[int(ping_id)] = {field: ping.get(field) for field in FIELDS} | {"link": ping.get("link")}
    return rows


def remember(sender_id: int, before: dict[int, dict], label: str, back: str = "",
             now: Optional[datetime] = None) -> Optional[str]:
    """Store a snapshot; returns the token the undo button carries.

    ``back`` is the callback that redraws the screen the action was taken on,
    so after an undo the owner sees the restored state, not a stale card.
    """
    if not before:
        return None
    token = secrets.token_hex(3)
    state.bot_undo[sender_id] = {
        "token": token, "rows": before, "label": label, "back": back, "at": now or datetime.now(),
    }
    return token


def pending_undo(sender_id: int, token: str, now: Optional[datetime] = None) -> Optional[dict]:
    """The stored snapshot if the token is current and not expired."""
    entry = state.bot_undo.get(sender_id)
    if not entry or entry.get("token") != token:
        return None
    if (now or datetime.now()) - entry["at"] > timedelta(seconds=UNDO_TTL_SECONDS):
        state.bot_undo.pop(sender_id, None)
        return None
    return entry


def restore_values(row: dict) -> dict[str, str]:
    """Stored values, with NULLs mapped to what every filter already treats them as."""
    return {
        "status": row.get("status") or "new",
        "giveaway_status": row.get("giveaway_status") or "",
        "action_status": row.get("action_status") or "new",
    }


async def undo(sender_id: int, token: str) -> Optional[tuple[int, str]]:
    """Put the rows back. Returns (restored, back callback), None when expired."""
    from database import get_ping_by_id, propagate_status_to_duplicates, update_ping_meta

    entry = pending_undo(sender_id, token)
    if entry is None:
        return None
    state.bot_undo.pop(sender_id, None)
    restored = 0
    for ping_id, row in entry["rows"].items():
        current = await get_ping_by_id(int(ping_id))
        # Raw write: the values came out of the table a few seconds ago, so they
        # do not need the vocabulary check a typed status gets.
        values = restore_values(row)
        await update_ping_meta(int(ping_id), **values)
        # Copies of the same win followed the change, so they follow it back.
        await propagate_status_to_duplicates(int(ping_id), values["giveaway_status"], values["action_status"])
        restored += 1
        if current and _is_claimed(current) and not _is_claimed(row):
            await _unmirror_claim(row.get("link"))
    return restored, str(entry.get("back") or "")


async def _unmirror_claim(link: Optional[str]) -> None:
    """Clear the Obsidian checkbox «забрал» had stamped — best effort."""
    if not link or not settings.obsidian_sync_write:
        return
    try:
        from ..obsidian_debts import mark_link_done

        await mark_link_done(state, settings, link, False)
    except Exception:
        logger.debug("Obsidian note update on undo failed", exc_info=True)


def undo_row(token: Optional[str], label: str = "") -> list[list[Button]]:
    """The keyboard row to prepend to the refreshed screen."""
    if not token:
        return []
    text = f"↩️ Отменить: {label}" if label else "↩️ Отменить"
    return [[Button.inline(f"{text} ({UNDO_TTL_SECONDS} с)"[:60], f"ud:{token}".encode())]]


def sweep(now: Optional[datetime] = None) -> int:
    moment = now or datetime.now()
    stale = [sender for sender, entry in state.bot_undo.items()
             if moment - entry["at"] > timedelta(seconds=UNDO_TTL_SECONDS)]
    for sender in stale:
        state.bot_undo.pop(sender, None)
    return len(stale)
