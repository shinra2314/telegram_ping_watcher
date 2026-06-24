"""Periodic data retention cleanup."""
from __future__ import annotations

from datetime import datetime, timedelta

from ._core import _connect


async def cleanup_old_data(
    days: int = 7,
    *,
    pings_retention_days: int = 0,
    vacuum: bool = False,
) -> dict[str, int]:
    """Remove old market history, app events, and (optionally) old pings.

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
            # Delete in batches to keep WAL small
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
            stats["pings"] = total
        await db.commit()
        if vacuum:
            try:
                await db.execute("VACUUM")
                stats["vacuumed"] = 1
            except Exception:
                pass
    return stats


async def purge_stale_checks(minutes: int = 60) -> int:
    """Delete redeemable-check pings older than ``minutes``.

    CryptoBot чеки are grabbed within minutes; a stale one is dead weight. Only
    *pure* ephemeral checks are removed — checks the owner favourited or that
    turned out to be wins are spared (they carry value beyond the check). The
    ``pings_fts`` virtual table has no delete trigger, so its rows are removed
    first, mirroring ``database.delete_ping``. Returns the number deleted.
    """
    cutoff = (datetime.now() - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()
    selector = "is_check = 1 AND is_favorite = 0 AND is_win = 0 AND detected_at < ?"
    async with _connect() as db:
        await db.execute(
            f"DELETE FROM pings_fts WHERE rowid IN (SELECT id FROM pings WHERE {selector})",
            (cutoff,),
        )
        cur = await db.execute(f"DELETE FROM pings WHERE {selector}", (cutoff,))
        await db.commit()
        return int(cur.rowcount or 0)
