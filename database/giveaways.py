"""Giveaway candidates, actions, and flag reconciliation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

import aiosqlite

from ._core import (
    _connect,
    _json_loads,
    _now_iso,
    _parse_mentions,
    giveaway_outcome_resolution,
    is_giveaway_outcome_text,
    is_win_text,
    matches_strict_giveaway_rule,
)


def _matches_strict_giveaway_rule(text: str, chat_type: str, keywords: Sequence[str]) -> bool:
    return matches_strict_giveaway_rule(text, chat_type, keywords)


async def reconcile_giveaway_outcomes(limit: int = 10000) -> dict[str, int]:
    """Promote giveaway result posts to prize claims.

    Wins require a tracked-username mention (same invariant as
    ping_pipeline.classify_record): a channel announcing someone else's
    win must not become a claim_prize task.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, is_win, action_status, giveaway_status, priority_score, deadline_source, mentions
            FROM pings
            WHERE is_giveaway = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

    marked = 0
    for row in rows:
        if not _parse_mentions(row["mentions"]):
            continue
        if not is_giveaway_outcome_text(row["text"] or ""):
            continue
        resolution = giveaway_outcome_resolution(row["text"] or "")
        is_final = (row["giveaway_status"] or "") in {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"} or (row["action_status"] or "") in {"claimed", "missed", "scam", "closed"}
        next_action = row["action_status"] or "new"
        if not is_final and next_action in {"", "new", "waiting_result", "to_check"}:
            next_action = "missed" if resolution == "missed" else "claim_prize"
        next_giveaway_status = resolution if not is_final and resolution == "missed" else "pending"
        async with _connect() as db:
            await db.execute(
                """
                UPDATE pings
                SET is_win = 1,
                    giveaway_status = CASE
                        WHEN COALESCE(giveaway_status, '') = '' THEN ?
                        WHEN COALESCE(giveaway_status, '') = 'pending' AND ? = 'missed' THEN 'missed'
                        ELSE giveaway_status
                    END,
                    action_status = ?,
                    priority_score = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 90 ELSE priority_score END,
                    priority_label = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 'critical' ELSE priority_label END,
                    deadline_at = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN NULL
                        ELSE deadline_at
                    END,
                    deadline_source = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN ''
                        ELSE deadline_source
                    END,
                    deadline_text = CASE
                        WHEN COALESCE(deadline_source, '') IN ('', 'channel_description_missing') THEN ''
                        ELSE deadline_text
                    END
                WHERE id = ?
                """,
                (next_giveaway_status, next_giveaway_status, next_action, int(row["id"])),
            )
            if (row["deadline_source"] or "") in {"", "channel_description_missing"}:
                await db.execute("DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL", (int(row["id"]),))
            await db.commit()
        marked += 1
    return {"marked": marked}


async def reconcile_win_flags(win_keywords: Sequence[str], limit: int = 10000) -> dict[str, int]:
    """Align stored win flags with the win-keyword rule.

    Wins require a tracked-username mention (same invariant as
    ping_pipeline.classify_record); mention-less rows flagged by older
    builds are disabled here.
    """
    enabled = 0
    disabled = 0
    final_statuses = {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"}
    enable_updates: list[tuple[Any, ...]] = []
    disable_updates: list[tuple[Any, ...]] = []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, chat_type, is_win, is_giveaway, action_status, giveaway_status, priority_score, mentions
            FROM pings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

        for row in rows:
            should_be_win = bool(_parse_mentions(row["mentions"])) and is_win_text(row["text"] or "", win_keywords)
            current = bool(row["is_win"])
            if should_be_win == current:
                continue
            current_action = row["action_status"] or "new"
            current_giveaway_status = row["giveaway_status"] or ""
            is_final = current_action in {"claimed", "missed", "scam", "closed"} or current_giveaway_status in final_statuses
            if should_be_win:
                next_action = "claim_prize" if not is_final and current_action in {"", "new", "to_check", "waiting_result"} else current_action
                enable_updates.append((next_action, int(row["id"])))
                enabled += 1
            else:
                if is_final:
                    next_action = current_action
                elif row["is_giveaway"]:
                    next_action = "waiting_result" if current_action in {"claim_prize", "to_check", "new", ""} else current_action
                else:
                    next_action = "to_check" if int(row["priority_score"] or 0) >= 60 else "new"
                disable_updates.append((next_action, int(row["id"])))
                disabled += 1

        if enable_updates:
            await db.executemany(
                """
                UPDATE pings
                SET is_win = 1,
                    action_status = ?,
                    priority_score = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 90 ELSE priority_score END,
                    priority_label = CASE WHEN COALESCE(priority_score, 0) < 90 THEN 'critical' ELSE priority_label END
                WHERE id = ?
                """,
                enable_updates,
            )
        if disable_updates:
            await db.executemany(
                """
                UPDATE pings
                SET is_win = 0,
                    action_status = ?,
                    priority_score = CASE
                        WHEN is_giveaway = 1 THEN MIN(COALESCE(priority_score, 0), 65)
                        ELSE MIN(COALESCE(priority_score, 0), 55)
                    END,
                    priority_label = CASE
                        WHEN is_giveaway = 1 THEN 'high'
                        ELSE 'medium'
                    END
                WHERE id = ?
                """,
                disable_updates,
            )
        await db.commit()
    return {"enabled": enabled, "disabled": disabled}


async def reconcile_giveaway_flags(keywords: Sequence[str], limit: int = 10000) -> dict[str, int]:
    """Align stored rows with the channel+keyword giveaway rule used for new scans.

    Giveaways require a tracked-username mention (same invariant as
    ping_pipeline.classify_record); mention-less rows flagged by older
    builds are disabled here.
    """
    enabled = 0
    disabled = 0
    enable_updates: list[tuple[Any, ...]] = []
    disable_keep_deadline: list[tuple[Any, ...]] = []
    disable_clear_deadline: list[tuple[Any, ...]] = []
    delete_reminders_for: list[tuple[int]] = []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, text, chat_type, is_giveaway, is_win, priority_score, action_status, deadline_source, mentions
            FROM pings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()

        for row in rows:
            should_be_giveaway = bool(_parse_mentions(row["mentions"])) and _matches_strict_giveaway_rule(row["text"] or "", row["chat_type"] or "", keywords)
            is_giveaway = bool(row["is_giveaway"])
            if should_be_giveaway == is_giveaway:
                continue
            ping_id = int(row["id"])
            current_action = row["action_status"] or "new"
            if should_be_giveaway:
                next_action = "waiting_result" if current_action in {"", "new"} else current_action
                enable_updates.append((next_action, ping_id))
                enabled += 1
                continue

            if row["is_win"]:
                next_action = "claim_prize" if current_action in {"", "new", "waiting_result"} else current_action
            elif current_action in {"", "new", "waiting_result"}:
                next_action = "to_check" if int(row["priority_score"] or 0) >= 60 else "new"
            else:
                next_action = current_action
            keep_manual_deadline = (row["deadline_source"] or "") == "manual"
            if keep_manual_deadline:
                disable_keep_deadline.append((next_action, ping_id))
            else:
                disable_clear_deadline.append((next_action, ping_id))
                delete_reminders_for.append((ping_id,))
            disabled += 1

        if enable_updates:
            await db.executemany(
                """
                UPDATE pings
                SET is_giveaway = 1,
                    giveaway_status = CASE
                        WHEN COALESCE(giveaway_status, '') = '' THEN 'pending'
                        ELSE giveaway_status
                    END,
                    action_status = ?
                WHERE id = ?
                """,
                enable_updates,
            )
        if disable_keep_deadline:
            await db.executemany(
                """
                UPDATE pings
                SET is_giveaway = 0,
                    giveaway_status = '',
                    action_status = ?
                WHERE id = ?
                """,
                disable_keep_deadline,
            )
        if disable_clear_deadline:
            await db.executemany(
                """
                UPDATE pings
                SET is_giveaway = 0,
                    giveaway_status = '',
                    action_status = ?,
                    deadline_at = NULL,
                    deadline_source = '',
                    deadline_text = '',
                    reminder_at = NULL,
                    reminder_sent_at = NULL
                WHERE id = ?
                """,
                disable_clear_deadline,
            )
        if delete_reminders_for:
            await db.executemany(
                "DELETE FROM reminders WHERE ping_id = ? AND sent_at IS NULL",
                delete_reminders_for,
            )
        await db.commit()
    return {"enabled": enabled, "disabled": disabled}


def _candidate_from_row(row: aiosqlite.Row) -> dict[str, Any]:
    item = dict(row)
    for key, default in (
        ("reasons", []),
        ("required_channels", []),
        ("join_buttons", []),
        ("external_requirements", []),
    ):
        item[key] = _json_loads(item.get(key), default)
    if "mentions" in item:
        item["mentions"] = _parse_mentions(item.get("mentions"))
    return item


async def upsert_giveaway_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    now = _now_iso()
    ping_id = int(candidate["ping_id"])
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO giveaway_candidates (
                ping_id, status, score, reasons, required_channels, join_buttons,
                external_requirements, blocked_reason, estimated_value, analyzed_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ping_id) DO UPDATE SET
                status = excluded.status,
                score = excluded.score,
                reasons = excluded.reasons,
                required_channels = excluded.required_channels,
                join_buttons = excluded.join_buttons,
                external_requirements = excluded.external_requirements,
                blocked_reason = excluded.blocked_reason,
                estimated_value = excluded.estimated_value,
                analyzed_at = excluded.analyzed_at,
                updated_at = excluded.updated_at
            """,
            (
                ping_id,
                candidate.get("status") or "pending_review",
                int(candidate.get("score") or 0),
                json.dumps(candidate.get("reasons") or [], ensure_ascii=False),
                json.dumps(candidate.get("required_channels") or [], ensure_ascii=False),
                json.dumps(candidate.get("join_buttons") or [], ensure_ascii=False),
                json.dumps(candidate.get("external_requirements") or [], ensure_ascii=False),
                candidate.get("blocked_reason") or "",
                candidate.get("estimated_value"),
                now,
                now,
            ),
        )
        await db.commit()
    saved = await get_giveaway_candidate(ping_id)
    return saved or candidate


async def update_giveaway_candidate_status(ping_id: int, status: str, blocked_reason: Optional[str] = None) -> None:
    updates = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, _now_iso()]
    if blocked_reason is not None:
        updates.append("blocked_reason = ?")
        params.append(blocked_reason)
    params.append(ping_id)
    async with _connect() as db:
        await db.execute(f"UPDATE giveaway_candidates SET {', '.join(updates)} WHERE ping_id = ?", params)
        await db.commit()


async def get_giveaway_candidate(ping_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """
            SELECT c.*, p.chat, p.chat_id, p.message_id, p.link, p.text, p.deadline_at,
                   p.giveaway_status, p.action_status, p.mentions
            FROM giveaway_candidates c
            JOIN pings p ON p.id = c.ping_id
            WHERE c.ping_id = ?
            """,
            (ping_id,),
        )).fetchone()
        return _candidate_from_row(row) if row else None


async def get_giveaway_candidates(
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if status:
        where = "WHERE c.status = ?"
        params.append(status)
    params.append(limit)
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""
            SELECT c.*, p.chat, p.chat_id, p.message_id, p.link, p.text, p.deadline_at,
                   p.giveaway_status, p.action_status, p.mentions
            FROM giveaway_candidates c
            JOIN pings p ON p.id = c.ping_id
            {where}
            ORDER BY
                CASE c.status WHEN 'recommended' THEN 0 WHEN 'pending_review' THEN 1 WHEN 'manual_required' THEN 2 ELSE 3 END,
                c.score DESC,
                c.updated_at DESC
            LIMIT ?
            """,
            params,
        )).fetchall()
        return [_candidate_from_row(row) for row in rows]


async def seed_giveaway_candidates_from_pings(limit: int = 500) -> int:
    now = _now_iso()
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO giveaway_candidates (
                ping_id, status, score, reasons, required_channels, join_buttons,
                external_requirements, blocked_reason, estimated_value, analyzed_at, updated_at
            )
            SELECT
                p.id,
                'pending_review',
                COALESCE(p.priority_score, 0),
                ?,
                '[]',
                '[]',
                '[]',
                '',
                NULL,
                ?,
                ?
            FROM pings p
            LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
            WHERE p.is_giveaway = 1
              AND c.ping_id IS NULL
            ORDER BY p.detected_at DESC
            LIMIT ?
            """,
            (json.dumps(["Pending safe analysis."], ensure_ascii=False), now, now, limit),
        )
        await db.commit()
        return int(cursor.rowcount or 0)


async def record_giveaway_action(
    ping_id: Optional[int],
    action: str,
    status: str,
    actor: str = "system",
    message: str = "",
    context: Optional[dict[str, Any]] = None,
) -> int:
    context_json = json.dumps(context or {}, ensure_ascii=False, sort_keys=True)
    async with _connect() as db:
        if action == "analyze" and ping_id is not None:
            cutoff = (datetime.now() - timedelta(hours=6)).replace(microsecond=0).isoformat()
            existing = await (await db.execute(
                """
                SELECT id
                FROM giveaway_actions
                WHERE ping_id = ?
                  AND action = ?
                  AND status = ?
                  AND actor = ?
                  AND message = ?
                  AND context = ?
                  AND created_at >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (ping_id, action, status, actor, message, context_json, cutoff),
            )).fetchone()
            if existing:
                await db.execute("UPDATE giveaway_actions SET created_at = ? WHERE id = ?", (_now_iso(), int(existing[0])))
                await db.commit()
                return int(existing[0])
        cursor = await db.execute(
            """
            INSERT INTO giveaway_actions (ping_id, action, status, actor, message, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ping_id,
                action,
                status,
                actor,
                message,
                context_json,
                _now_iso(),
            ),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def get_recent_giveaway_actions(limit: int = 15) -> list[dict[str, Any]]:
    """Recent giveaway actions across all pings, with chat context for display."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT ga.*, p.chat AS chat
            FROM giveaway_actions ga
            LEFT JOIN pings p ON p.id = ga.ping_id
            ORDER BY ga.id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = _json_loads(item.get("context"), {})
            result.append(item)
        return result


async def get_giveaway_actions(ping_id: int, limit: int = 50) -> list[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT * FROM giveaway_actions
            WHERE ping_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ping_id, limit),
        )).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["context"] = _json_loads(item.get("context"), {})
            result.append(item)
        return result
