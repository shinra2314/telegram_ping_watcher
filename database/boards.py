"""Aggregated boards: tasks overview, giveaway board, debt board."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

import aiosqlite

from ._core import _connect, _json_loads, _now_iso, _parse_iso_datetime, _parse_mentions
from .outbox import get_outbox_stats
from .pings import get_pings


async def get_task_overview(limit: int = 300) -> dict[str, list[dict[str, Any]]]:
    rows = await get_pings(limit=limit, chat_type="giveaway", sort_by="detected_at", sort_order="DESC")
    buckets: dict[str, list[dict[str, Any]]] = {
        "claim_prize": [],
        "waiting_result": [],
        "all_open": [],
    }
    closed = {"claimed", "scam", "missed", "missed_unsubscribe", "missed_reply", "closed"}
    for row in rows:
        if row.get("action_status") in closed or row.get("giveaway_status") in closed:
            continue
        buckets["all_open"].append(row)
        if row.get("action_status") == "waiting_result":
            buckets["waiting_result"].append(row)
        elif row.get("is_win") or row.get("action_status") == "claim_prize":
            buckets["claim_prize"].append(row)
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
    item["workflow_stage"] = _giveaway_workflow_stage(item)
    item["needs_decision"] = (
        not item["is_final"]
        and (
            action_status in {"new", "to_check", "claim_prize"}
            or candidate_status in {"recommended", "manual_required"}
        )
    )
    item["workflow_hint"] = _giveaway_workflow_hint(item)
    item["sort_rank"] = _giveaway_sort_rank(item)
    return item


def _giveaway_workflow_stage(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "done"
    # A claim outranks the join-time analysis: the prize is owed either way.
    if item.get("is_claim"):
        return "claim"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual"
    return "waiting"


def _giveaway_sort_rank(item: dict[str, Any]) -> int:
    if item.get("is_final"):
        return 90
    if item.get("is_claim"):
        return 1
    if item.get("blocked_reason") or item.get("external_requirements"):
        return 15
    if item.get("candidate_status") == "recommended":
        return 12
    return 50


def _giveaway_workflow_hint(item: dict[str, Any]) -> str:
    if item.get("is_final"):
        return "closed"
    if item.get("is_claim"):
        return "claim_prize"
    if item.get("blocked_reason") or item.get("external_requirements"):
        return "manual_review"
    if item.get("candidate_status") == "recommended":
        return "recommended"
    return "watch"


# Bucket predicates, shared by the full board and the cheap counters below so
# the two can never drift apart. `p` is the `pings` alias, `c` the LEFT JOINed
# `giveaway_candidates` row.
_BASE_WHERE = "(p.is_giveaway = 1 OR p.is_win = 1)"
_STATS_BASE_WHERE = "(is_giveaway = 1 OR is_win = 1)"
_ACTIVE_WHERE = (
    f"{_BASE_WHERE} "
    "AND COALESCE(p.giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') "
    "AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')"
)

BUCKET_WHERE = {
    # Manual-only requirements (captcha, comments) describe how to *enter* a
    # giveaway, so they only ever divert candidates. A win is already won —
    # gating it on them dropped owed prizes out of every bucket at once.
    "need_action": (
        _ACTIVE_WHERE
        + " AND (p.is_win = 1"
        + " OR COALESCE(p.action_status, 'new') IN ('claim_prize', 'to_check')"
        + " OR COALESCE(c.status, '') = 'recommended')"
        + " AND (p.is_win = 1"
        + " OR (COALESCE(c.status, '') != 'manual_required'"
        + " AND COALESCE(c.blocked_reason, '') = ''"
        + " AND COALESCE(c.external_requirements, '[]') IN ('[]', '')))"
    ),
    "waiting_result": (
        _ACTIVE_WHERE
        + " AND p.is_win = 0"
        + " AND COALESCE(p.action_status, 'new') = 'waiting_result'"
    ),
    "suspicious": (
        _ACTIVE_WHERE
        + " AND p.is_win = 0"
        + " AND (COALESCE(c.status, '') = 'manual_required'"
        + " OR COALESCE(c.blocked_reason, '') <> ''"
        + " OR COALESCE(c.external_requirements, '[]') NOT IN ('[]', ''))"
    ),
    "done": (
        _BASE_WHERE
        + " AND (COALESCE(p.giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed')"
        + " OR COALESCE(p.action_status, '') IN ('claimed', 'missed', 'scam', 'closed'))"
    ),
}


async def giveaway_bucket_total(bucket: str = "need_action") -> int:
    """Rows in one board bucket, and nothing else.

    The home screen needs a single number ("к действию"). Calling
    ``get_giveaway_board`` for it cost ~17 queries — four bucket fetches, four
    counts, board stats and the outbox — of which it used one.
    """
    where_sql = BUCKET_WHERE.get(bucket) or BUCKET_WHERE["need_action"]
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM pings p
            LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
            WHERE {where_sql}
            """
        )).fetchone()
    return int(row["total"] or 0)


async def get_giveaway_board(
    limit: int = 80, sort: str = "detected", include_outbox: bool = True
) -> dict[str, Any]:
    """Bucketed giveaway board.

    ``sort`` picks the tiebreak inside every bucket: ``detected`` (when the scan
    found the post, the default) or ``posted`` (the message's own date). Bucket
    rank always wins — a claimable prize stays on top either way.

    ``include_outbox`` adds the live-outbox stats block, which costs a second
    connection and six queries. The bot never renders it, so it asks for False.
    """
    sort = "posted" if str(sort or "").lower() == "posted" else "detected"
    now = _now_iso()
    now_dt = _parse_iso_datetime(now) or datetime.now()
    stats_base_where = _STATS_BASE_WHERE
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

    async def count_bucket(db: aiosqlite.Connection, where_sql: str) -> int:
        """Rows matching a bucket, ignoring the page limit (header counters)."""
        row = await (await db.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM pings p
            LEFT JOIN giveaway_candidates c ON c.ping_id = p.id
            WHERE {where_sql}
            """
        )).fetchone()
        return int(row["total"] or 0)

    order_sql = "COALESCE(p.date, p.detected_at) DESC" if sort == "posted" else "p.detected_at DESC"

    async def fetch_bucket(db: aiosqlite.Connection, where_sql: str) -> list[dict[str, Any]]:
        rows = await (await db.execute(
            f"""
            {select_sql}
            WHERE {where_sql}
            ORDER BY
                {order_sql}
            LIMIT ?
            """,
            (min(limit * 4, 500),),
        )).fetchall()
        items = [_giveaway_board_row(row, now_dt) for row in rows]
        # Two stable passes: newest first by the chosen date (id breaks ties),
        # then bucket rank on top — a claimable prize never sinks below a
        # fresher candidate.
        if sort == "posted":
            items.sort(key=lambda item: (str(item.get("date") or item.get("detected_at") or ""),
                                         int(item.get("id") or 0)), reverse=True)
        else:
            items.sort(key=lambda item: (str(item.get("detected_at") or ""),
                                         int(item.get("id") or 0)), reverse=True)
        items.sort(key=lambda item: int(item.get("sort_rank") or 99))
        return items[:limit]

    bucket_where = BUCKET_WHERE

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        buckets = {key: await fetch_bucket(db, where) for key, where in bucket_where.items()}
        bucket_totals = {key: await count_bucket(db, where) for key, where in bucket_where.items()}
        stats_row = await (await db.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN (COALESCE(action_status, 'new') = 'claim_prize' OR is_win = 1) AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') AND COALESCE(action_status, '') NOT IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS claim_prize,
                SUM(CASE WHEN is_win = 0 AND COALESCE(action_status, 'new') = 'waiting_result' AND COALESCE(giveaway_status, '') NOT IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') THEN 1 ELSE 0 END) AS waiting_result,
                SUM(CASE WHEN COALESCE(giveaway_status, '') IN ('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed') OR COALESCE(action_status, '') IN ('claimed', 'missed', 'scam', 'closed') THEN 1 ELSE 0 END) AS done,
                SUM(CASE WHEN COALESCE(giveaway_status, '') = 'missed_reply' THEN 1 ELSE 0 END) AS missed_reply
            FROM pings
            WHERE {stats_base_where}
            """
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
            "sort": sort,
            "stats": {key: int(stats_row[key] or 0) for key in stats_row.keys()},
            "candidate_statuses": [dict(row) for row in candidate_rows],
            "action_statuses": [dict(row) for row in action_rows],
            "outbox": await get_outbox_stats() if include_outbox else {},
            "buckets": buckets,
            "bucket_counts": {key: len(value) for key, value in buckets.items()},
            # Uncapped per-bucket totals: `bucket_counts` stops at `limit`, so a
            # paged view needs these to show how much is really queued.
            "bucket_totals": bucket_totals,
        }


async def giveaway_account_counts(open_only: bool = True) -> dict[str, dict[str, int]]:
    """Per mentioned account: how many wins / giveaways sit on the board.

    Grouped in SQL over ``ping_mentions`` — the normalised sidecar table that
    ``_sync_ping_indexes`` keeps in step with the JSON ``mentions`` column, and
    which carries an index on ``username``. Reading the JSON column instead
    meant an unbounded ``SELECT`` plus a ``json.loads`` per row on the event
    loop, every time the account picker was opened.

    `open_only` drops rows already closed out (claimed, missed, scam, …).
    """
    where = "(p.is_giveaway = 1 OR p.is_win = 1)"
    if open_only:
        where += (
            " AND COALESCE(p.giveaway_status, '') NOT IN "
            "('claimed', 'missed', 'missed_unsubscribe', 'missed_reply', 'scam', 'closed')"
            " AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')"
        )
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"""
            SELECT
                LOWER(m.username) AS username,
                SUM(CASE WHEN p.is_win = 1 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN p.is_win = 1 THEN 0 ELSE 1 END) AS giveaways,
                COUNT(*) AS total
            FROM ping_mentions m
            JOIN pings p ON p.id = m.ping_id
            WHERE {where}
            GROUP BY LOWER(m.username)
            """
        )).fetchall()
    return {
        str(row["username"]): {
            "wins": int(row["wins"] or 0),
            "giveaways": int(row["giveaways"] or 0),
            "total": int(row["total"] or 0),
        }
        for row in rows
        if str(row["username"] or "").strip()
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
          -- Channels and group chats both announce real results. Private chats
          -- are excluded: they only ever hold forwarded copies of a post that is
          -- already tracked at its source.
          AND COALESCE(p.chat_type, '') IN ('channel', 'group')
          AND COALESCE(NULLIF(p.giveaway_status, ''), 'pending') = 'pending'
          AND COALESCE(p.action_status, 'new') NOT IN ('claimed', 'missed', 'scam', 'closed')
          -- Copies of the same winners post (dedupe.py) are listed once, under the primary.
          AND p.duplicate_of IS NULL
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
