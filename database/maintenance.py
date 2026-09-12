"""Periodic data retention cleanup."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from ._core import _connect, db_path


async def cleanup_old_data(
    days: int = 7,
    *,
    pings_retention_days: int = 0,
    vacuum: bool = False,
) -> dict[str, int]:
    """Remove old market history, app events, and (optionally) old pings.

    Ping retention only ages out low-value rows: wins, giveaways and favourites
    are kept forever, matching ``enforce_db_size_cap``.
    Without that guard the sweep silently erased won prizes (and giveaway posts
    soft-deleted by their channel) from the debts board once they crossed the
    retention line. Volume is still bounded — the size cap evicts giveaways to
    the archive DB when the file grows past ``DB_MAX_SIZE_MB``.

    Returns a small stats dict with deletion counts.
    """
    stats = {"market_history": 0, "app_events": 0, "pings": 0, "vacuumed": 0}
    async with _connect() as db:
        cur = await db.execute(
            "DELETE FROM market_history WHERE fetched_at_iso < datetime('now', '-' || ? || ' days')",
            (days,),
        )
        stats["market_history"] = cur.rowcount or 0
        events_cutoff = (datetime.now() - timedelta(days=max(days, 7))).replace(microsecond=0).isoformat()
        cur = await db.execute("DELETE FROM app_events WHERE created_at < ?", (events_cutoff,))
        stats["app_events"] = cur.rowcount or 0
        if pings_retention_days and pings_retention_days > 0:
            cutoff = (datetime.now() - timedelta(days=pings_retention_days)).replace(microsecond=0).isoformat()
            selector = "detected_at < ? AND is_win = 0 AND is_giveaway = 0 AND is_favorite = 0"
            # Delete in batches to keep WAL small. ``pings_fts`` has no delete
            # trigger, so its rows go first (mirrors ``database.delete_ping``).
            total = 0
            while True:
                ids = [
                    int(row[0])
                    for row in await (await db.execute(
                        f"SELECT id FROM pings WHERE {selector} LIMIT 1000", (cutoff,)
                    )).fetchall()
                ]
                if not ids:
                    break
                placeholders = ",".join("?" * len(ids))
                await db.execute(f"DELETE FROM pings_fts WHERE rowid IN ({placeholders})", ids)
                cur = await db.execute(f"DELETE FROM pings WHERE id IN ({placeholders})", ids)
                total += cur.rowcount or 0
                if len(ids) < 1000:
                    break
            stats["pings"] = total
        await db.commit()
        if vacuum:
            try:
                await db.execute("VACUUM")
                stats["vacuumed"] = 1
            except Exception:
                pass
    return stats


# --------------------------------------------------------------------------- #
# Size-cap enforcement + archival + unbounded-table retention                 #
# --------------------------------------------------------------------------- #

_ARCHIVE_BATCH = 1000


def db_size_bytes() -> int:
    """On-disk size of the main DB, including the WAL/SHM sidecar files."""
    path = db_path()
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += Path(f"{path}{suffix}").stat().st_size
        except OSError:
            pass
    return total


def archive_db_path() -> Path:
    """Sibling archive DB that receives pings evicted by the size cap."""
    path = db_path()
    return path.parent / f"{path.stem}_archive.db"


async def _table_columns(db: aiosqlite.Connection, schema: str, table: str) -> list[str]:
    """Ordered column names for a (possibly attached) table."""
    cols: list[str] = []
    async with db.execute(f"PRAGMA {schema}.table_info({table})") as cursor:
        async for row in cursor:
            cols.append(row[1])
    return cols


# Newest history rows kept per settings key, on top of the age cap. An age cap
# alone bounds nothing when a key is rewritten on a timer: `obsidian_sync` was
# audited every 30 s and reached 141 666 rows / 111 MB — 82 % of the database —
# while still sitting inside the 90-day window.
SETTINGS_HISTORY_PER_KEY = 200

# Checkpoints are keyed (session_name, username|channel:id), so the table is the
# cross product of accounts x tracked usernames x channels and never shrinks on
# its own — a third of the rows here were for channels last seen months ago.
CHECKPOINT_STALE_DAYS = 90


async def cleanup_unbounded_tables(
    *,
    scan_runs_keep: int = 500,
    audit_days: int = 90,
    history_per_key: int = SETTINGS_HISTORY_PER_KEY,
    checkpoint_days: int = CHECKPOINT_STALE_DAYS,
) -> dict[str, int]:
    """Trim the tables that otherwise grow forever.

    ``scan_runs`` is capped by count (newest N kept) but rows still ``running``
    are always spared. ``settings_history``/``access_audit``/``giveaway_actions``
    are capped by age, and ``settings_history`` additionally by count per key.
    ``scan_checkpoints`` drops rows for channels not seen in ``checkpoint_days``;
    a dropped checkpoint only means that channel is re-read from its window on
    the next sweep, never a missed message. Returns per-table deletion counts.
    """
    stats = {
        "scan_runs": 0,
        "settings_history": 0,
        "access_audit": 0,
        "giveaway_actions": 0,
        "scan_checkpoints": 0,
    }
    async with _connect() as db:
        if scan_runs_keep and scan_runs_keep > 0:
            cur = await db.execute(
                """
                DELETE FROM scan_runs
                WHERE status != 'running'
                  AND id NOT IN (SELECT id FROM scan_runs ORDER BY id DESC LIMIT ?)
                """,
                (scan_runs_keep,),
            )
            stats["scan_runs"] = cur.rowcount or 0
        if audit_days and audit_days > 0:
            cutoff = (datetime.now() - timedelta(days=audit_days)).replace(microsecond=0).isoformat()
            cur = await db.execute("DELETE FROM settings_history WHERE changed_at < ?", (cutoff,))
            stats["settings_history"] = cur.rowcount or 0
            cur = await db.execute("DELETE FROM access_audit WHERE created_at < ?", (cutoff,))
            stats["access_audit"] = cur.rowcount or 0
            cur = await db.execute("DELETE FROM giveaway_actions WHERE created_at < ?", (cutoff,))
            stats["giveaway_actions"] = cur.rowcount or 0
        if history_per_key and history_per_key > 0:
            cur = await db.execute(
                """
                DELETE FROM settings_history
                WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (PARTITION BY key ORDER BY id DESC) AS rn
                        FROM settings_history
                    ) WHERE rn <= ?
                )
                """,
                (history_per_key,),
            )
            stats["settings_history"] += cur.rowcount or 0
        if checkpoint_days and checkpoint_days > 0:
            stale = (datetime.now() - timedelta(days=checkpoint_days)).replace(microsecond=0).isoformat()
            cur = await db.execute(
                "DELETE FROM scan_checkpoints WHERE COALESCE(updated_at, '') < ?", (stale,)
            )
            stats["scan_checkpoints"] = cur.rowcount or 0
        await db.commit()
    return stats


async def cleanup_archive_db(retention_days: int = 30, *, vacuum: bool = False) -> dict[str, int]:
    """Prune records older than ``retention_days`` from the archive DB.

    The ``pulse_desk_archive.db`` *file* is kept (it holds evicted-ping history),
    but its rows are aged out so it cannot grow forever. ``detected_at`` is the
    record's creation timestamp. The archive ``pings`` table is a bare column
    copy with no FTS/triggers, so a plain batched ``DELETE`` is enough. No-op
    (zeros) when the file or table is absent, or ``retention_days <= 0``.
    ``VACUUM`` runs only when rows were deleted and ``vacuum`` is set.
    """
    stats = {"archive_pings": 0, "archive_vacuumed": 0}
    if not retention_days or retention_days <= 0:
        return stats
    arch_path = archive_db_path()
    if not arch_path.exists():
        return stats
    cutoff = (datetime.now() - timedelta(days=retention_days)).replace(microsecond=0).isoformat()
    async with aiosqlite.connect(str(arch_path)) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        has_table = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pings'"
        )).fetchone()
        if not has_table:
            return stats
        total = 0
        while True:
            cur = await db.execute(
                "DELETE FROM pings WHERE id IN (SELECT id FROM pings WHERE detected_at < ? LIMIT 1000)",
                (cutoff,),
            )
            deleted = cur.rowcount or 0
            total += deleted
            if deleted < 1000:
                break
        await db.commit()
        stats["archive_pings"] = total
        if total and vacuum:
            try:
                await db.execute("VACUUM")
                stats["archive_vacuumed"] = 1
            except Exception:
                pass
    return stats


async def enforce_db_size_cap(max_mb: int, *, archive: bool = True) -> dict[str, int]:
    """Keep the main DB under ``max_mb`` by evicting the oldest low-value pings.

    Favourites and wins are never touched. Evicted pings are first copied to
    ``archive_db_path()`` when ``archive`` is set, then deleted (their ``pings_fts``
    rows are removed manually; ``ping_mentions``/``reminders``/``giveaway_candidates``
    cascade). A single estimate-based pass runs per call — if the estimate falls
    short the next scheduled pass trims further. ``VACUUM`` reclaims the freed
    pages so the file actually shrinks. ``max_mb <= 0`` disables the cap.
    """
    stats = {"size_before": 0, "size_after": 0, "pings_archived": 0, "pings_deleted": 0, "vacuumed": 0}
    if not max_mb or max_mb <= 0:
        return stats
    cap_bytes = max_mb * 1024 * 1024
    size_before = db_size_bytes()
    stats["size_before"] = size_before
    stats["size_after"] = size_before
    if size_before <= cap_bytes:
        return stats

    target_bytes = int(cap_bytes * 0.9)  # trim a little below the cap for hysteresis
    selector = "is_favorite = 0 AND is_win = 0"
    archived = 0
    deleted = 0
    async with _connect() as db:
        page_count = int((await (await db.execute("PRAGMA page_count")).fetchone())[0])
        page_size = int((await (await db.execute("PRAGMA page_size")).fetchone())[0])
        total_pings = int((await (await db.execute("SELECT COUNT(*) FROM pings")).fetchone())[0])
        deletable = int((await (await db.execute(f"SELECT COUNT(*) FROM pings WHERE {selector}")).fetchone())[0])
        if not deletable:
            return stats
        avg_bytes = (page_count * page_size) / max(total_pings, 1)
        overflow = size_before - target_bytes
        rows = math.ceil(overflow / max(avg_bytes, 1.0) * 1.3)
        rows = max(1, min(deletable, rows))
        ids = [
            int(r[0])
            for r in await (await db.execute(
                f"SELECT id FROM pings WHERE {selector} ORDER BY detected_at ASC LIMIT ?",
                (rows,),
            )).fetchall()
        ]
        if not ids:
            return stats

        col_list = ""
        if archive:
            await db.execute("ATTACH DATABASE ? AS arch", (str(archive_db_path()),))
            await db.execute("CREATE TABLE IF NOT EXISTS arch.pings AS SELECT * FROM pings WHERE 0")
            main_cols = await _table_columns(db, "main", "pings")
            arch_cols = set(await _table_columns(db, "arch", "pings"))
            col_list = ", ".join(col for col in main_cols if col in arch_cols)
        try:
            for start in range(0, len(ids), _ARCHIVE_BATCH):
                batch = ids[start:start + _ARCHIVE_BATCH]
                placeholders = ",".join("?" * len(batch))
                if archive and col_list:
                    await db.execute(
                        f"INSERT INTO arch.pings ({col_list}) SELECT {col_list} FROM pings WHERE id IN ({placeholders})",
                        batch,
                    )
                    archived += len(batch)
                await db.execute(f"DELETE FROM pings_fts WHERE rowid IN ({placeholders})", batch)
                cur = await db.execute(f"DELETE FROM pings WHERE id IN ({placeholders})", batch)
                deleted += cur.rowcount or 0
                await db.commit()
        finally:
            if archive:
                try:
                    await db.commit()
                    await db.execute("DETACH DATABASE arch")
                except Exception:
                    pass
        if deleted:
            try:
                await db.execute("VACUUM")
                stats["vacuumed"] = 1
            except Exception:
                pass
    stats["pings_archived"] = archived
    stats["pings_deleted"] = deleted
    stats["size_after"] = db_size_bytes()
    return stats
