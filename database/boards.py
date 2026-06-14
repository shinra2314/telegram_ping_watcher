"""Aggregated boards: tasks overview, giveaway board, debt board."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

import aiosqlite

from ._core import _connect, _json_loads, _now_iso, _parse_iso_datetime, _parse_mentions
from .outbox import get_outbox_stats
from .pings import get_pings


async def get_task_overview(limit: int = 300) -> dict[str, list[dict[str, Any]]]:
    rows = await get_pings(limit=limit, chat_type="giveaway", sort_by="deadline_at", sort_order="ASC")
    now = datetime.now()
    today = now.date()
    tomorrow = today + timedelta(days=1)
    buckets: dict[str, list[dict[str, Any]]] = {
        "overdue": [],
        "today": [],
        "tomorrow": [],
        "waiting_result": [],
        "no_deadline": [],
        "all_open": [],
    }
    closed = {"claimed", "scam", "missed", "missed_unsubscribe", "missed_reply", "closed"}
    for row in rows:
        if row.get("action_status") in closed or row.get("giveaway_status") in closed:
            continue
        buckets["all_open"].append(row)
        if row.get("action_status") == "waiting_result":
            buckets["waiting_result"].append(row)
        deadline = _parse_iso_datetime(row.get("deadline_at"))
        if not deadline:
            if not row.get("is_win") and row.get("action_status") != "claim_prize":
                buckets["no_deadline"].append(row)
            continue
        if deadline < now:
            buckets["overdue"].append(row)
        elif deadline.date() == today:
            buckets["today"].append(row)
        elif deadline.date() == tomorrow:
            buckets["tomorrow"].append(row)
    return buckets


def _giveaway_board_row(row: aiosqlite.Row, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now()
    item = dict(row)
    item["mentions"] = _parse_mentions(item.get("mentions"))
    for key, default in (
        ("candidate_reasons", []),
        ("required_channels", []),
        ("join_buttons", []),
        ("external_requirements", []),
    ):
        item[key] = _json_loads(item.get(key), default)
    giveaway_status = item.get("giveaway_status") or "pending"
    action_status = item.get("action_status") or "new"
    candidate_status = item.get("candidate_status") or ""
    item["is_final"] = giveaway_status in {"claimed", "missed", "missed_unsubscribe", "missed_reply", "scam", "closed"} or action_status in {"claimed", "missed", "scam", "closed"}
    item["is_claim"] = bool(item.get("is_win")) or action_status == "claim_prize"
    deadline = _parse_iso_datetime(item.get("deadline_at"))
    item["deadline_state"] = "missing"
    item["deadline_seconds"] = None
    item["deadline_badge_class"] = "bad"
    if deadline:
        seconds = int((deadline - now).total_seconds())
        item["deadline_seconds"] = seconds
        if seconds < 0:
            item["deadline_state"] = "overdue"
            item["deadline_badge_class"] = "bad"
        elif deadline.date() == now.date():
            item["deadline_state"] = "today"
            item["deadline_badge_class"] = "warn"
        elif deadline.date() == (now + timedelta(days=1)).date():
            item["deadline_state"] = "tomorrow"
            item["deadline_badge_class"] = "info"
        else:
            item["deadline_state"] = "upcoming"
            item["deadline_badge_class"] = "good"
    elif item["is_claim"]:
        item["deadline_state"] = "claim_unknown"
        item["deadline_badge_class"] = "warn"
    item["workflow_stage"] = _giveaway_workflow_stage(item)
    item["needs_decision"] = (
        not item["is_final"]
        and (
            action_status in {"new", "to_check", "claim_prize"}
            or candidate_status in {"recommended", "manual_required"}
            or (not item.get("deadline_at") and not item["is_claim"])
        )
    )
    item["deadline_label"] = item.get("deadline_at") or "deadline_missing"
    item["workflow_hint"] = _giveaway_workflow_hint(item)
    item["sort_rank"] = _giveaway_sort_rank(item)
    return item


def _giveaway_workflow_stage(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "done"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual"
    if item.get("is_claim"):
        return "claim"
    if not item.get("deadline_at"):
        return "missing_deadline"
    if item.get("deadline_state") == "overdue":
        return "overdue"
    if item.get("deadline_state") in {"today", "tomorrow"}:
        return "soon"
    return "waiting"


def _giveaway_sort_rank(item: dict[str, Any]) -> int:
    if item.get("is_final"):
        return 90
    if item.get("blocked_reason") or item.get("external_requirements"):
        return 15
    if item.get("is_claim"):
        return 0 if item.get("deadline_state") == "overdue" else 1
    if item.get("deadline_state") == "overdue":
        return 5
    if item.get("deadline_state") == "today":
        return 10
    if item.get("candidate_status") == "recommended":
        return 12
    if item.get("deadline_state") == "tomorrow":
        return 20
    if item.get("workflow_stage") == "missing_deadline":
        return 40
    return 50


def _giveaway_workflow_hint(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "closed"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual_review"
    if item.get("is_claim"):
        return "claim_prize"
    if not item.get("deadline_at"):
        return "set_deadline"
    if item.get("candidate_status") == "recommended":
        return "recommended"
    return "watch"


async def get_giveaway_board(limit: int = 80) -> dict[str, Any]:
    now = _now_iso()
    now_dt = _parse_iso_datetime(now) or datetime.now()
    base_where = "(p.is_giveaway = 1 OR p.is_win = 1)"
    stats_base_where = "(is_giveaway = 1 OR is_win = 1)"
    active_where = (
        f"{base_where} "
        "AND COALESCE(p.giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') "
        "AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')"
    )
    select_sql = """
        SELECT
            p.*,
            c.status AS candidate_status,
            c.score AS candidate_score,
            c.reasons AS candidate_reasons,
            c.required_channels,
            c.join_buttons,
            c.external_requirements,
            c.blocked_reason,
            c.estimated_value,
            c.analyzed_at,
            c.updated_at AS candidate_updated_at
        FROM pings p
        LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
    """

    async def fetch_bucket(db: aiosqlite.Connection, where_sql: str) -> list[dict[str, Any]]:
        rows = await (await db.execute(
            f"""
            {select_sql}
            WHERE {where_sql}
            ORDER BY
                CASE WHEN p.deadline_at IS NULL OR p.deadline_at = '' THEN 1 ELSE 0 END,
                p.deadline_at ASC,
                p.detected_at DESC
            LIMIT ?
            """,
            (min(limit * 4, 500),),
        )).fetchall()
        items = [_giveaway_board_row(row, now_dt) for row in rows]
        items.sort(key=lambda item: (int(item.get("sort_rank") or 99), item.get("deadline_at") or "9999-12-31", -int(item.get("id") or 0)))
        return items[:limit]

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        buckets = {
            "need_action": await fetch_bucket(
                db,
                active_where
                + " AND (p.is_win = 1"
                + " OR COALESCE(p.action_status, 'new') IN ('claim_prize', 'to_check')"
                + " OR COALESCE(c.status, '') = 'recommended')"
                + " AND COALESCE(c.status, '') != 'manual_required'"
                + " AND COALESCE(c.blocked_reason, '') = ''"
                + " AND COALESCE(c.external_requirements, '[]') IN ('[]', '')",
            ),
            "waiting_result": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND COALESCE(p.action_status, 'new') = 'waiting_result'"
                + " AND p.deadline_at IS NOT NULL AND p.deadline_at <> ''",
            ),
            "no_deadline": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND COALESCE(p.action_status, 'new') != 'claim_prize'"
                + " AND (p.deadline_at IS NULL OR p.deadline_at = '')",
            ),
            "suspicious": await fetch_bucket(
                db,
                active_where
                + " AND p.is_win = 0"
                + " AND (COALESCE(c.status, '') = 'manual_required'"
                + " OR COALESCE(c.blocked_reason, '') <> ''"
                + " OR COALESCE(c.external_requirements, '[]') NOT IN ('[]', ''))",
            ),
            "done": await fetch_bucket(
                db,
                base_where
                + " AND (COALESCE(p.giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed')"
                + " OR COALESCE(p.action_status, '') IN ('claimed', 'missed', 'scam', 'closed'))",
            ),
        }
        stats_row = await (await db.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN (COALESCE(action_status, 'new') = 'claim_prize' OR is_win = 1) AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') AND COALESCE(action_status, '') NOT IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS claim_prize,
                SUM(CASE WHEN is_win = 0 AND COALESCE(action_status, 'new') = 'waiting_result' AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') THEN 1 ELSE 0 END) AS waiting_result,
                SUM(CASE WHEN is_win = 0 AND (deadline_at IS NULL OR deadline_at = '') AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') THEN 1 ELSE 0 END) AS no_deadline,
                SUM(CASE WHEN deadline_at IS NOT NULL AND deadline_at <> '' AND deadline_at < ? AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') AND COALESCE(action_status, '') NOT IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS overdue,
                SUM(CASE WHEN COALESCE(giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') OR COALESCE(action_status, '') IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'missed_reply' THEN 1 ELSE 0 END) AS missed_reply
            FROM pings
            WHERE {stats_base_where}
            """,
            (now,),
        )).fetchone()
        candidate_rows = await (await db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM giveaway_candidates
            GROUP BY status
            ORDER BY count DESC
            """
        )).fetchall()
        action_rows = await (await db.execute(
            """
            SELECT action, status, COUNT(*) AS count
            FROM giveaway_actions
            GROUP BY action, status
            ORDER BY count DESC
            LIMIT 8
            """
        )).fetchall()
        return {
            "generated_at": now,
            "stats": {key: int(stats_row[key] or 0) for key in stats_row.keys()},
            "candidate_statuses": [dict(row) for row in candidate_rows],
            "action_statuses": [dict(row) for row in action_rows],
            "outbox": await get_outbox_stats(),
            "buckets": buckets,
            "bucket_counts": {key: len(value) for key, value in buckets.items()},
        }


async def get_debt_board(tracked_usernames: Sequence[str], limit: int = 160) -> dict[str, Any]:
    now = _now_iso()
    now_dt = _parse_iso_datetime(now) or datetime.now()
    safe_limit = max(10, min(int(limit or 160), 500))
    select_sql = """
        SELECT
            p.*,
            c.status AS candidate_status,
            c.score AS candidate_score,
            c.reasons AS candidate_reasons,
            c.required_channels,
            c.join_buttons,
            c.external_requirements,
            c.blocked_reason,
            c.estimated_value,
            c.analyzed_at,
            c.updated_at AS candidate_updated_at
        FROM pings p
        LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
        WHERE (p.is_win = 1 OR COALESCE(p.action_status, '') = 'claim_prize')
          AND COALESCE(p.chat_type, '') = 'channel'
          AND COALESCE(NULLIF(p.giveaway_status, ''), 'pending') = 'pending'
          AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')
        ORDER BY
            CASE WHEN COALESCE(p.status, '') = 'new' THEN 0 ELSE 1 END,
            COALESCE(p.priority_score, 0) DESC,
            p.detected_at DESC,
            p.id DESC
        LIMIT ?
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(select_sql, (safe_limit,))).fetchall()
    debts = [_giveaway_board_row(row, now_dt) for row in rows]
    for item in debts:
        item["debt_status"] = "pending_prize"
        item["giveaway_status"] = item.get("giveaway_status") or "pending"
        item["action_status"] = item.get("action_status") or "claim_prize"

    ordered_usernames: list[str] = []
    seen: set[str] = set()
    for username in tracked_usernames:
        normalized = str(username or "").strip().lstrip("@")
        key = normalized.lower()
        if key and key not in seen:
            seen.add(key)
            ordered_usernames.append(normalized)

    profile_rows: dict[str, list[dict[str, Any]]] = {username.lower(): [] for username in ordered_usernames}
    unassigned: list[dict[str, Any]] = []
    for item in debts:
        mentions = [str(value).strip().lstrip("@") for value in item.get("mentions") or []]
        mention_keys = {value.lower() for value in mentions if value}
        matched = False
        for username in ordered_usernames:
            key = username.lower()
            if key in mention_keys:
                profile_rows[key].append(item)
                matched = True
        if not matched:
            unassigned.append(item)

    profiles = []
    for username in ordered_usernames:
        rows_for_user = profile_rows.get(username.lower(), [])
        profiles.append({
            "key": username.lower(),
            "username": username,
            "count": len(rows_for_user),
            "new_count": sum(1 for row in rows_for_user if row.get("status") == "new"),
            "critical_count": sum(1 for row in rows_for_user if int(row.get("priority_score") or 0) >= 90),
            "max_priority": max((int(row.get("priority_score") or 0) for row in rows_for_user), default=0),
            "last_detected_at": max((row.get("detected_at") or "" for row in rows_for_user), default=""),
            "rows": rows_for_user[:safe_limit],
        })
    if unassigned:
        profiles.append({
            "key": "_unassigned",
            "username": "без username",
            "count": len(unassigned),
            "new_count": sum(1 for row in unassigned if row.get("status") == "new"),
            "critical_count": sum(1 for row in unassigned if int(row.get("priority_score") or 0) >= 90),
            "max_priority": max((int(row.get("priority_score") or 0) for row in unassigned), default=0),
            "last_detected_at": max((row.get("detected_at") or "" for row in unassigned), default=""),
            "rows": unassigned[:safe_limit],
        })

    return {
        "generated_at": now,
        "stats": {
            "total": len(debts),
            "new": sum(1 for row in debts if row.get("status") == "new"),
            "critical": sum(1 for row in debts if int(row.get("priority_score") or 0) >= 90),
            "profiles_with_debt": sum(1 for profile in profiles if int(profile.get("count") or 0) > 0),
            "last_detected_at": max((row.get("detected_at") or "" for row in debts), default=""),
        },
        "profiles": profiles,
        "rows": debts,
    }
