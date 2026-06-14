"""Deadline reminders and deadline backfill from giveaway text."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite

from ._core import (
    _connect,
    _now_iso,
    _parse_iso_datetime,
    is_giveaway_outcome_text,
    iso_or_none,
    parse_claim_deadline,
    parse_deadline,
    parse_participation_deadline,
)
from .pings import update_ping_deadline


async def replace_ping_reminders(ping_id: int, deadline_at: Optional[str], reminder_at: Optional[str] = None) -> None:
    deadline = _parse_iso_datetime(deadline_at)
    now = datetime.now()
    reminders: list[tuple[str, str]] = []
    if reminder_at:
        reminders.append((reminder_at, "manual"))
    elif deadline and deadline > now:
        for hours in (24, 2):
            remind_at = deadline - timedelta(hours=hours)
            if remind_at > now:
                reminders.append((remind_at.replace(microsecond=0).isoformat(), f"deadline-{hours}h"))
    async with _connect() as db:
        await db.execute("DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL", (ping_id,))
        for remind_at, kind in reminders:
            await db.execute(
                """
                INSERT INTO reminders (ping_id, remind_at, sent_at, kind, message, created_at)
                VALUES (?, ?, NULL, ?, '', ?)
                """,
                (ping_id, remind_at, kind, _now_iso()),
            )
        await db.execute(
            "UPDATE pings SET reminder_at = ?, reminder_sent_at = NULL WHERE id = ?",
            ((reminders[0][0] if reminders else reminder_at), ping_id),
        )
        await db.commit()


async def backfill_deadlines_from_text(limit: int = 5000) -> int:
    if parse_deadline is None:
        return 0
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, date, detected_at, chat_type, text, deadline_at, deadline_source
            FROM pings
            WHERE is_giveaway = 1
              AND COALESCE(deadline_source, '') != 'manual'
              AND COALESCE(deadline_source, '') != 'channel_description'
              AND COALESCE(text, '') != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    changed = 0
    for row in rows:
        reference = _parse_iso_datetime(row["date"]) or _parse_iso_datetime(row["detected_at"]) or datetime.now()
        if reference.tzinfo is not None:
            reference = reference.replace(tzinfo=None)
        is_outcome = is_giveaway_outcome_text(row["text"] or "")
        match = parse_claim_deadline(row["text"], now=reference) if is_outcome and parse_claim_deadline else parse_participation_deadline(row["text"], now=reference)
        if not match:
            if is_outcome and row["deadline_source"] == "claim_window_text":
                await update_ping_deadline(int(row["id"]), None, "", "", "claim_prize")
                await replace_ping_reminders(int(row["id"]), None)
                changed += 1
            continue
        deadline_at = iso_or_none(match.deadline_at)
        source = "claim_window_text" if is_outcome else ("channel_post_text" if row["chat_type"] == "channel" else "message_text")
        if row["deadline_at"] == deadline_at and row["deadline_source"] == source:
            continue
        next_action = "claim_prize" if source == "claim_window_text" else "waiting_result"
        await update_ping_deadline(int(row["id"]), deadline_at, source, match.matched_text, next_action)
        await replace_ping_reminders(int(row["id"]), deadline_at)
        changed += 1
    return changed


async def get_due_reminders(now_iso: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
    now_iso = now_iso or _now_iso()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT r.*, p.chat, p.text, p.link, p.deadline_at, p.deadline_source, p.action_status, p.giveaway_status
            FROM reminders r
            JOIN pings p ON p.id = r.ping_id
            WHERE r.sent_at IS NULL
              AND r.remind_at <= ?
              AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'scam', 'missed', 'closed')
              AND COALESCE(p.giveaway_status, '') NOT IN ('claimed', 'scam', 'missed_unsubscribe')
            ORDER BY r.remind_at ASC
            LIMIT ?
            """,
            (now_iso, limit),
        )).fetchall()
        return [dict(row) for row in rows]


async def mark_reminder_sent(reminder_id: int) -> None:
    sent_at = _now_iso()
    async with _connect() as db:
        row = await (await db.execute("SELECT ping_id FROM reminders WHERE id = ?", (reminder_id,))).fetchone()
        await db.execute("UPDATE reminders SET sent_at = ? WHERE id = ?", (sent_at, reminder_id))
        if row:
            await db.execute("UPDATE pings SET reminder_sent_at = ? WHERE id = ?", (sent_at, int(row[0])))
        await db.commit()
