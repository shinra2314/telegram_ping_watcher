"""Ping rows: insert/update, filters, tags, search."""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Optional

import aiosqlite

from ._core import (
    _add_where,
    _connect,
    _fts_query,
    _json_loads,
    _now_iso,
    _parse_mentions,
    _search_tokens,
    _sync_ping_indexes,
)


async def add_ping_tag(ping_id: int, tag: str) -> list[str]:
    tag = tag.strip()
    tag = tag.replace('"', '')
    if not tag:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        if tag not in tags:
            tags.append(tag)
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def remove_ping_tag(ping_id: int, tag: str) -> list[str]:
    tag = tag.strip().replace('"', '')
    if not tag:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT tags FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return []
        tags: list[str] = _json_loads(row["tags"], [])
        tags = [t for t in tags if t != tag]
        await db.execute("UPDATE pings SET tags = ? WHERE id = ?", (json.dumps(tags, ensure_ascii=False), ping_id))
        await db.commit()
        return tags


async def get_all_tags() -> list[str]:
    async with _connect() as db:
        rows = await (await db.execute("SELECT DISTINCT tags FROM pings WHERE tags IS NOT NULL AND tags != '[]'")).fetchall()
    seen: set[str] = set()
    for (raw,) in rows:
        for t in _json_loads(raw, []):
            if t:
                seen.add(t)
    return sorted(seen)


async def save_ping(record: dict[str, Any]) -> Optional[int]:
    async with _connect() as db:
        mentions_json = json.dumps(record.get("mentions", []), ensure_ascii=False)
        detected_at = record.get("detected_at") or _now_iso()
        try:
            cursor = await db.execute(
                """
                INSERT INTO pings (
                    date, chat, chat_id, sender, sender_id, message_id, mentions,
                    link, text, chat_type, detected_at, is_win, is_giveaway,
                    giveaway_status, priority_score, priority_label, note, action_status,
                    edited_at, win_detected_at, notified_at, win_notified_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("date"),
                    record.get("chat"),
                    record.get("chat_id"),
                    record.get("sender"),
                    record.get("sender_id"),
                    record.get("message_id"),
                    mentions_json,
                    record.get("link"),
                    record.get("text"),
                    record.get("chat_type"),
                    detected_at,
                    1 if record.get("is_win") else 0,
                    1 if record.get("is_giveaway") else 0,
                    record.get("giveaway_status") or ("pending" if (record.get("is_giveaway") or record.get("is_win")) else ""),
                    int(record.get("priority_score") or 0),
                    record.get("priority_label") or "normal",
                    record.get("note") or "",
                    record.get("action_status") or "new",
                    record.get("edited_at") or None,
                    detected_at if record.get("is_win") else None,
                    # Set only by a pass that deliberately sends no card (backlog);
                    # NULL leaves the card owed until ping_notify delivers it.
                    record.get("notified_at") or None,
                    (record.get("notified_at") or None) if record.get("is_win") else None,
                ),
            )
            ping_id = int(cursor.lastrowid)
            await _sync_ping_indexes(db, ping_id, record)
            await db.commit()
            return ping_id
        except aiosqlite.IntegrityError:
            async with db.execute(
                "SELECT id FROM pings WHERE chat_id = ? AND message_id = ?",
                (record.get("chat_id"), record.get("message_id")),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                ping_id = int(row[0])
                await db.execute(
                    """
                    UPDATE pings
                    SET date = COALESCE(?, date),
                        chat = COALESCE(NULLIF(?, ''), chat),
                        sender = COALESCE(NULLIF(?, ''), sender),
                        sender_id = COALESCE(?, sender_id),
                        mentions = ?,
                        link = COALESCE(NULLIF(?, ''), link),
                        text = COALESCE(?, text),
                        chat_type = COALESCE(NULLIF(?, ''), chat_type),
                        -- A win is sticky here. Re-reads disagree about the same
                        -- post (a mini-app card's win verdict comes from each
                        -- account's own search index, a sweep sees only the
                        -- placeholder), and letting a later pass clear the flag
                        -- made every next pass an "upgrade to win" and re-sent
                        -- the 🏆 card forever. Un-winning is an explicit action
                        -- (database/giveaways.py), never a side effect of a scan.
                        is_win = MAX(COALESCE(is_win, 0), ?),
                        is_giveaway = CASE WHEN COALESCE(is_win, 0) = 1 THEN 1 ELSE ? END,
                        giveaway_status = CASE
                            WHEN ? = 1 AND (giveaway_status IS NULL OR giveaway_status = '') THEN 'pending'
                            WHEN ? = 0 AND COALESCE(is_win, 0) = 0 THEN ''
                            ELSE giveaway_status
                        END,
                        -- So is a win's priority, for the same reason: a pass that
                        -- does not see the win scored it 15 and sank a sticky win
                        -- to the bottom of every list, and a pass without the
                        -- result-post floor fought the startup reconcile 85↔90.
                        priority_score = CASE
                            WHEN COALESCE(is_win, 0) = 1 AND COALESCE(priority_score, 0) > ? THEN priority_score
                            ELSE ?
                        END,
                        priority_label = CASE
                            WHEN COALESCE(is_win, 0) = 1 AND COALESCE(priority_score, 0) > ? THEN priority_label
                            ELSE COALESCE(NULLIF(?, ''), priority_label)
                        END,
                        action_status = CASE
                            WHEN action_status IS NULL OR action_status = '' OR action_status = 'new'
                            THEN COALESCE(NULLIF(?, ''), action_status)
                            ELSE action_status
                        END,
                        edited_at = COALESCE(?, edited_at),
                        win_detected_at = CASE
                            WHEN ? = 1 AND win_detected_at IS NULL AND COALESCE(is_win, 0) = 0 THEN ?
                            ELSE win_detected_at
                        END
                    WHERE id = ?
                    """,
                    (
                        record.get("date"),
                        record.get("chat"),
                        record.get("sender"),
                        record.get("sender_id"),
                        mentions_json,
                        record.get("link"),
                        record.get("text"),
                        record.get("chat_type"),
                        1 if record.get("is_win") else 0,
                        1 if (record.get("is_giveaway") or record.get("is_win")) else 0,
                        1 if (record.get("is_giveaway") or record.get("is_win")) else 0,
                        1 if record.get("is_giveaway") else 0,
                        int(record.get("priority_score") or 0),
                        int(record.get("priority_score") or 0),
                        int(record.get("priority_score") or 0),
                        record.get("priority_label") or "",
                        record.get("action_status") or "",
                        record.get("edited_at") or None,
                        1 if record.get("is_win") else 0,
                        detected_at,
                        ping_id,
                    ),
                )
                await _sync_ping_indexes(db, ping_id, record)
                await db.commit()
                return ping_id


async def toggle_favorite(ping_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE pings SET is_favorite = CASE WHEN is_favorite THEN 0 ELSE 1 END WHERE id = ?", (ping_id,))
        await db.commit()


async def mark_ping_read(ping_id: int) -> None:
    async with _connect() as db:
        await db.execute("UPDATE pings SET status = 'read' WHERE id = ?", (ping_id,))
        await db.commit()


def _build_pings_filters(
    *,
    chat_type: Optional[str] = None,
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    mention_any: Optional[Sequence[str]] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    message_date_from: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    source_score_min: Optional[float] = None,
    tag: Optional[str] = None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if chat_type and chat_type != "all":
        if chat_type == "giveaway":
            _add_where(where, params, "is_giveaway = 1")
        elif chat_type == "win":
            _add_where(where, params, "is_win = 1")
        elif chat_type == "important":
            _add_where(where, params, "priority_score >= 60")
        else:
            _add_where(where, params, "chat_type = ?", chat_type)
    if status:
        _add_where(where, params, "status = ?", status)
    if favorite is not None:
        _add_where(where, params, "is_favorite = ?", 1 if favorite else 0)
    if mention:
        needle = mention.strip().lstrip("@")
        _add_where(
            where,
            params,
            "EXISTS (SELECT 1 FROM ping_mentions pm WHERE pm.ping_id = pings.id AND lower(pm.username) = lower(?))",
            needle,
        )
    if mention_any:
        # An account-scoped bot key may cover several tracked usernames. Filtering
        # them here (indexed, via ping_mentions) rather than in Python means the
        # caller can page normally instead of over-fetching whole pages of rows
        # and JSON-decoding `mentions` on every one of them.
        needles = [str(name).strip().lstrip("@").lower() for name in mention_any]
        needles = [name for name in needles if name]
        if needles:
            placeholders = ", ".join("?" for _ in needles)
            _add_where(
                where,
                params,
                f"EXISTS (SELECT 1 FROM ping_mentions pm WHERE pm.ping_id = pings.id AND lower(pm.username) IN ({placeholders}))",
                *needles,
            )
    if search:
        raw_search = search.strip()
        text = f"%{raw_search}%"
        tokens = _search_tokens(raw_search)
        fts = _fts_query(search)
        token_clauses: list[str] = []
        token_params: list[Any] = []
        for token in tokens:
            like = f"%{token}%"
            token_clauses.append(
                "(text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE)"
            )
            token_params.extend([like, like, like, like])
        token_sql = " AND ".join(token_clauses)
        if fts:
            condition = "(pings.id IN (SELECT rowid FROM pings_fts WHERE pings_fts MATCH ?) OR text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE"
            values: list[Any] = [fts, text, text, text, text]
            if token_sql:
                condition += f" OR ({token_sql})"
                values.extend(token_params)
            condition += ")"
            _add_where(
                where,
                params,
                condition,
                *values,
            )
        else:
            condition = "(text LIKE ? COLLATE NOCASE OR chat LIKE ? COLLATE NOCASE OR sender LIKE ? COLLATE NOCASE OR mentions LIKE ? COLLATE NOCASE"
            values = [text, text, text, text]
            if token_sql:
                condition += f" OR ({token_sql})"
                values.extend(token_params)
            condition += ")"
            _add_where(where, params, condition, *values)
    if date_from:
        _add_where(where, params, "detected_at >= ?", date_from)
    if date_to:
        _add_where(where, params, "detected_at <= ?", date_to)
    if message_date_from:
        # Filter on the Telegram message date (column `date`), not detection time.
        # `datetime()` normalises both sides to UTC so tz-offset suffixes compare right.
        _add_where(where, params, "datetime(date) >= datetime(?)", message_date_from)
    if priority_min is not None:
        _add_where(where, params, "priority_score >= ?", priority_min)
    if action_status:
        _add_where(where, params, "action_status = ?", action_status)
    if source_score_min is not None:
        _add_where(
            where,
            params,
            "chat_id IN (SELECT chat_id FROM source_scores WHERE score >= ?)",
            source_score_min,
        )
    if tag:
        _add_where(where, params, "tags LIKE ?", f'%"{tag}"%')
    return where, params


async def mark_pings_read(
    *,
    chat_type: Optional[str] = None,
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    source_score_min: Optional[float] = None,
    only_new: bool = True,
) -> int:
    where, params = _build_pings_filters(
        chat_type=chat_type,
        status=status,
        favorite=favorite,
        mention=mention,
        search=search,
        date_from=date_from,
        date_to=date_to,
        priority_min=priority_min,
        action_status=action_status,
        source_score_min=source_score_min,
    )
    if only_new:
        _add_where(where, params, "status = 'new'")
    query = "UPDATE pings SET status = 'read'"
    if where:
        query += " WHERE " + " AND ".join(where)
    async with _connect() as db:
        cursor = await db.execute(query, params)
        await db.commit()
        return int(cursor.rowcount or 0)


async def update_ping_meta(
    ping_id: int,
    status: Optional[str] = None,
    note: Optional[str] = None,
    is_favorite: Optional[bool] = None,
    giveaway_status: Optional[str] = None,
    action_status: Optional[str] = None,
) -> None:
    updates: dict[str, Any] = {}
    if status is not None:
        updates["status"] = status
    if note is not None:
        updates["note"] = note
    if is_favorite is not None:
        updates["is_favorite"] = 1 if is_favorite else 0
    if giveaway_status is not None:
        updates["giveaway_status"] = giveaway_status
    if action_status is not None:
        updates["action_status"] = action_status
    if not updates:
        return
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    async with _connect() as db:
        await db.execute(f"UPDATE pings SET {set_clause} WHERE id = ?", (*updates.values(), ping_id))
        await db.commit()


async def delete_ping(chat_id: int, message_id: int) -> None:
    """Win/giveaway pings survive channel deletion as soft-deleted rows
    (deleted_at) so they stay visible on the giveaways board."""
    async with _connect() as db:
        await db.execute(
            "UPDATE pings SET deleted_at = COALESCE(deleted_at, ?) WHERE chat_id = ? AND message_id = ? AND (is_win = 1 OR is_giveaway = 1)",
            (_now_iso(), chat_id, message_id),
        )
        rows = await (await db.execute(
            "SELECT id FROM pings WHERE chat_id = ? AND message_id = ? AND is_win = 0 AND is_giveaway = 0",
            (chat_id, message_id),
        )).fetchall()
        for row in rows:
            await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (row[0],))
        await db.execute(
            "DELETE FROM pings WHERE chat_id = ? AND message_id = ? AND is_win = 0 AND is_giveaway = 0",
            (chat_id, message_id),
        )
        await db.commit()


async def delete_ping_by_message_id(message_id: int) -> None:
    """See delete_ping: win/giveaway rows are soft-deleted, the rest removed.

    Used only for deletion events that carry no chat, which Telegram sends for
    private chats and basic groups — those share one per-account message-id
    sequence, so the bare id identifies the message. Channel posts are numbered
    per channel and collide with it constantly, so they are excluded here: a
    deleted DM must not mark an unrelated channel win as "post removed" or drop
    a channel mention that is still live.
    """
    scope = "COALESCE(chat_type, '') <> 'channel' AND message_id = ?"
    async with _connect() as db:
        await db.execute(
            f"UPDATE pings SET deleted_at = COALESCE(deleted_at, ?) WHERE {scope} AND (is_win = 1 OR is_giveaway = 1)",
            (_now_iso(), message_id),
        )
        rows = await (await db.execute(
            f"SELECT id FROM pings WHERE {scope} AND is_win = 0 AND is_giveaway = 0",
            (message_id,),
        )).fetchall()
        for row in rows:
            await db.execute("DELETE FROM pings_fts WHERE rowid = ?", (row[0],))
        await db.execute(
            f"DELETE FROM pings WHERE {scope} AND is_win = 0 AND is_giveaway = 0",
            (message_id,),
        )
        await db.commit()


async def get_giveaway_pings_with_links(limit: int = 5000) -> list[dict[str, Any]]:
    """Win/giveaway pings that carry a t.me link — match set for Obsidian sync."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, link, giveaway_status, action_status, mentions, detected_at, is_win, text, chat
            FROM pings
            WHERE (is_win = 1 OR is_giveaway = 1) AND link LIKE 'https://t.me/%'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )).fetchall()
        return [dict(row) for row in rows]


def _keyset_after(field: str, direction: str, after: tuple[Any, int]) -> tuple[str, list[Any]]:
    """WHERE clause for "rows after ``(value, id)``" in ``ORDER BY field, id``.

    SQLite puts NULLs first ascending and last descending, and a comparison
    with NULL is never true — so a NULL on either side gets its own branch
    instead of a row-value ``(field, id) < (?, ?)``. ``field`` is already
    checked against the whitelist by the caller.
    """
    value, last_id = after
    if field == "id":
        return ("id < ?" if direction == "DESC" else "id > ?"), [last_id]
    if direction == "DESC":
        if value is None:
            return f"({field} IS NULL AND id < ?)", [last_id]
        return f"({field} IS NULL OR {field} < ? OR ({field} = ? AND id < ?))", [value, value, last_id]
    if value is None:
        return f"(({field} IS NULL AND id > ?) OR {field} IS NOT NULL)", [last_id]
    return f"({field} > ? OR ({field} = ? AND id > ?))", [value, value, last_id]


async def get_pings(
    limit: int = 100,
    chat_type: Optional[str] = None,
    sort_order: str = "DESC",
    offset: int = 0,
    sort_by: str = "detected_at",
    status: Optional[str] = None,
    favorite: Optional[bool] = None,
    mention: Optional[str] = None,
    mention_any: Optional[Sequence[str]] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    message_date_from: Optional[str] = None,
    priority_min: Optional[int] = None,
    action_status: Optional[str] = None,
    source_score_min: Optional[float] = None,
    tag: Optional[str] = None,
    after: Optional[tuple[Any, int]] = None,
) -> list[dict[str, Any]]:
    """Filtered, ordered pings.

    ``after`` is a keyset cursor — the ``(sort value, id)`` of the last row the
    caller already has — and returns the rows that follow it in this order.
    Unlike ``offset`` it neither repeats nor skips a row when new ones arrive
    or old ones leave the filter between two pages. ``id`` breaks ties in the
    ordering either way, so both paths see the same sequence.
    """
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM pings"
        where, params = _build_pings_filters(
            chat_type=chat_type,
            status=status,
            favorite=favorite,
            mention=mention,
            mention_any=mention_any,
            search=search,
            date_from=date_from,
            date_to=date_to,
            message_date_from=message_date_from,
            priority_min=priority_min,
            action_status=action_status,
            source_score_min=source_score_min,
            tag=tag,
        )

        direction = "DESC" if sort_order.upper() == "DESC" else "ASC"
        valid_sort_fields = {"detected_at", "date", "chat", "sender", "status", "id", "priority_score", "action_status"}
        field = sort_by if sort_by in valid_sort_fields else "detected_at"
        if after is not None:
            clause, values = _keyset_after(field, direction, after)
            where.append(clause)
            params.extend(values)

        if where:
            query += " WHERE " + " AND ".join(where)

        query += f" ORDER BY {field} {direction}" + ("" if field == "id" else f", id {direction}")
        if limit and limit > 0:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset > 0:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        async with db.execute(query, params) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def get_pings_grouped(
    limit: int = 50,
    chat_type: str = "all",
    search: Optional[str] = None,
    mention: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows = await get_pings(limit=0, chat_type=chat_type, search=search, mention=mention, sort_by="priority_score")
    grouped: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row.get("chat_id") or row.get("chat")
        if key not in grouped:
            grouped[key] = dict(row)
            grouped[key]["group_count"] = 0
        grouped[key]["group_count"] += 1
    values = list(grouped.values())
    return values[:limit] if limit and limit > 0 else values


async def rebuild_search_indexes() -> dict[str, int]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT id, chat, sender, mentions, text FROM pings")).fetchall()
        await db.execute("DELETE FROM ping_mentions")
        await db.execute("DELETE FROM pings_fts")
        for row in rows:
            await _sync_ping_indexes(db, int(row["id"]), dict(row))
        ping_count = len(rows)
        fts_count = (await (await db.execute("SELECT COUNT(*) AS count FROM pings_fts")).fetchone())["count"]
        mention_count = (await (await db.execute("SELECT COUNT(*) AS count FROM ping_mentions")).fetchone())["count"]
        await db.commit()
        return {"pings": ping_count, "fts": int(fts_count or 0), "mentions": int(mention_count or 0)}


async def get_ping_by_id(ping_id: int) -> Optional[dict[str, Any]]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM pings WHERE id = ?", (ping_id,))).fetchone()
        if not row:
            return None
        item = dict(row)
        item["mentions"] = _parse_mentions(item.get("mentions"))
        return item


async def get_ping_by_message_ref(chat_id: Any, message_id: Any) -> Optional[dict[str, Any]]:
    if chat_id is None or message_id is None:
        return None
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT * FROM pings WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            )
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["mentions"] = _parse_mentions(item.get("mentions"))
        return item


async def search_pings_fts(
    query: str,
    *,
    limit: int = 50,
    offset: int = 0,
    snippet_chars: int = 30,
) -> list[dict[str, Any]]:
    """Full-text search across pings using FTS5 with bm25 ranking.

    Returns rows with a `snippet` field highlighting matches via <mark>.
    Falls back to empty list if the query has no tokens.
    """
    fts_query = _fts_query(query)
    if not fts_query:
        return []
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        sql = """
            SELECT pings.*,
                   bm25(pings_fts) AS _rank,
                   snippet(pings_fts, 3, '<mark>', '</mark>', '…', ?) AS snippet
            FROM pings_fts
            JOIN pings ON pings.id = pings_fts.rowid
            WHERE pings_fts MATCH ?
            ORDER BY _rank
            LIMIT ? OFFSET ?
        """
        async with db.execute(sql, (snippet_chars, fts_query, limit, offset)) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
