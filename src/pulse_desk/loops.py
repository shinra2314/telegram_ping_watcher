"""Long-running background loops: market, digest, scores, auto-scan."""
from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

from . import salary
from . import watch_settings as ws
from .app_ctx import (
    ARCHIVE_RETENTION_DAYS,
    BASE_DIR,
    AUDIT_RETENTION_DAYS,
    DB_ARCHIVE_ENABLED,
    DB_MAX_SIZE_MB,
    DISK_FREE_ALERT_MB,
    FLOOD_WAIT_MAX_SECONDS,
    MARKET_RETENTION_DAYS,
    PINGS_RETENTION_DAYS,
    SCAN_RUNS_RETENTION,
    STARTUP_SCAN_WAIT_SECONDS,
    VACUUM_INTERVAL_HOURS,
    logger,
    settings,
    state,
)
from .bot_notify import (
    broadcast_member_notification,
    edit_pending_admin_card,
    execute_pending_broadcast,
    send_admin_bot_message,
    send_member_bot_message,
)
from . import housekeeping
from .common import flood_wait_seconds, now_iso, record_app_event, start_supervised
from .converter import BRIDGE_IDS, FIAT_CODES, fiat_block
from .digest import (
    DIGEST_POLL_SECONDS,
    digest_due,
    digest_seed_slot,
    digest_slot,
    format_digest,
    format_digest_caption,
)
from .digest_cards import build_digest_cards
from .jobs import feature_job_polls
from .scan import next_scan_delay
from .scan_engine import full_history_scan
from .watchdog import JobHealth, classify_job, default_thresholds, diff_health, format_age


COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


async def fetch_fiat_rates(http_client: httpx.AsyncClient) -> dict[str, float]:
    """Fiat units per USD for the bot converter — best effort, never raises.

    A second, small request on purpose: asking the main call for a dozen
    `vs_currencies` would multiply every stored snapshot (prices *and* their
    24 h changes, on all eight coins) for data only the converter reads. Here
    only two bridge coins are quoted and the result collapses to one float per
    currency. On failure the converter falls back to the usd/uah quotes the
    snapshot already carries.
    """
    try:
        resp = await http_client.get(COINGECKO_PRICE_URL, params={
            "ids": ",".join(BRIDGE_IDS),
            "vs_currencies": ",".join(code.lower() for code in FIAT_CODES),
        })
        resp.raise_for_status()
        return fiat_block(resp.json())
    except Exception:
        logger.debug("Fiat rate fetch failed", exc_info=True)
        return {}


async def fetch_market_data() -> None:
    from database import save_market_snapshot

    ids = "tether,the-open-network,bitcoin,ethereum,solana,binancecoin,notcoin,dogs-2"
    url = COINGECKO_PRICE_URL
    # Market cap rides along on the same request (no extra call): it sizes the
    # tiles of the digest's crypto treemap.
    params = {
        "ids": ids,
        "vs_currencies": "usd,uah",
        "include_24hr_change": "true",
        "include_market_cap": "true",
    }

    while True:
        try:
            # Retry logic for transient network issues
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        resp = await http_client.get(url, params=params)
                        resp.raise_for_status()
                        data = resp.json()
                        data["fetched_at_iso"] = now_iso()
                        data["_fiat"] = await fetch_fiat_rates(http_client)
                        await save_market_snapshot(data)
                        break  # Success
                except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 5
                        logger.warning(f"Market fetch timeout (attempt {attempt+1}/3), retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:  # Rate limit
                        wait = 60
                        logger.warning(f"Market fetch rate limited, retrying in {wait}s...")
                        await asyncio.sleep(wait)
                    else:
                        raise e
        except Exception:
            logger.exception("Market data fetch failed after retries")

        state.heartbeat("market-fetch")
        await asyncio.sleep(ws.MARKET_POLL_SECONDS)


async def monitor_market_volatility() -> None:
    from database import get_market_history

    while True:
        try:
            rows = await get_market_history(limit=2)
            if len(rows) >= 2:
                current, previous = rows[0], rows[1]
                for asset in ("bitcoin", "the-open-network", "solana"):
                    curr_price = current.get(asset, {}).get("usd") or 0
                    prev_price = previous.get(asset, {}).get("usd") or 0
                    if prev_price:
                        change = abs((curr_price - prev_price) / prev_price) * 100
                        if change >= ws.MARKET_ALERT_CHANGE_PCT:
                            await send_admin_bot_message(
                                f"**Резкое движение {asset.upper()}**\nИзменение: `{change:.2f}%`\nЦена: `${curr_price:,.2f}`",
                                kind="market",
                            )
        except Exception:
            logger.exception("Market volatility monitor failed")
        await asyncio.sleep(3600)


async def process_due_broadcasts() -> int:
    """Single pass of the moderation queue: auto-send pending rows past expires_at."""
    from database import claim_pending_broadcast, get_due_pending_broadcasts, release_pending_broadcast

    if not state.bot_client:
        # Bot is down: leave rows pending, retry next tick.
        return 0
    handled = 0
    for row in await get_due_pending_broadcasts(limit=10):
        claimed = await claim_pending_broadcast(int(row["id"]), "auto_sent")
        if not claimed:
            continue
        # The claim is consumed before the send, so a failure here would leave the
        # row marked sent and never delivered. Put it back and let the next tick
        # retry — and keep one bad row from aborting the rest of the batch.
        try:
            count, token = await execute_pending_broadcast(claimed)
            await edit_pending_admin_card(claimed, f"📤 Отправлено автоматически ({count})", token=token, delivered_count=count)
        except Exception as exc:
            await release_pending_broadcast(int(row["id"]))
            logger.exception("Pending broadcast %s failed to auto-send", row["id"])
            await record_app_event(
                "ERROR", "broadcast", "Pending broadcast auto-send failed, returned to queue",
                {"id": row["id"], "error": str(exc)},
            )
            continue
        await record_app_event("INFO", "broadcast", "Pending broadcast auto-sent", {"id": row["id"], "delivered": count})
        handled += 1
    return handled


async def broadcast_approval_loop() -> None:
    while True:
        try:
            await process_due_broadcasts()
            state.heartbeat("broadcast-approval")
        except Exception:
            logger.exception("Broadcast approval loop failed")
        await asyncio.sleep(20)


async def collect_digest_market(hours: int = 24) -> list[dict]:
    """Market snapshots for the digest window; never fails the digest itself."""
    from database import get_market_history

    try:
        since = (datetime.now() - timedelta(hours=hours + 1)).isoformat()
        return await get_market_history(limit=1000, since_iso=since)
    except Exception:
        logger.exception("Digest market history failed")
        return []


DIGEST_CARD_DIR = BASE_DIR / "data" / "digest"


async def render_digest(
    pings: list[dict],
    prev_pings: list[dict] | None,
    market: list[dict] | None,
    period_label: str = "за последние 24 ч",
) -> tuple[str, list[str]]:
    """The digest as (message, card paths).

    Normally two Aperture cards — pings and crypto — with a short caption that
    carries only the win links a picture cannot. If rendering is unavailable
    (no Pillow, unwritable dir), this degrades to the old plain-text digest.
    """
    # Pillow work — gradients, blurs, a LANCZOS downscale — runs off the loop
    # the bot shares with the API, which it used to freeze for the whole render.
    cards = await asyncio.to_thread(
        build_digest_cards,
        pings, DIGEST_CARD_DIR, period_label=period_label,
        prev_pings=prev_pings, market=market,
    )
    if cards:
        return format_digest_caption(pings, period_label=period_label, prev_pings=prev_pings), cards
    return format_digest(pings, period_label=period_label, prev_pings=prev_pings, market=market), []


DIGEST_STATE_KEY = "digest_state"


async def send_digest() -> None:
    from database import get_pings

    now = datetime.now()
    since = (now - timedelta(hours=24)).isoformat()
    pings = await get_pings(limit=200, date_from=since)
    prev_pings = await get_pings(
        limit=200,
        date_from=(now - timedelta(hours=48)).isoformat(),
        date_to=since,
    )
    market = await collect_digest_market(24)
    message, cards = await render_digest(pings, prev_pings, market)
    await send_admin_bot_message(message, file=cards or None)
    await broadcast_member_notification(message, None, file=cards or None, notif_type="digest")
    await record_app_event("INFO", "digest", "Daily digest sent",
                           {"pings": len(pings), "cards": len(cards)})


async def digest_loop() -> None:
    """Send the daily digest once per slot, catching up a slot the PC slept through.

    It used to sleep until HH:MM, which lost the day whenever the machine was
    off at that moment and gave the watchdog nothing to watch. It now ticks
    like the roulette reminder; the handled slot date lives in ``digest_state``.
    """
    from database import get_setting, set_setting

    while True:
        try:
            cfg = await ws.load_digest_settings()
            now = datetime.now()
            stored = await get_setting(DIGEST_STATE_KEY, None)
            if not isinstance(stored, dict) or not stored.get("last_slot"):
                stored = {"last_slot": digest_seed_slot(now, cfg)}
                await set_setting(DIGEST_STATE_KEY, stored, audit=False)
            if digest_due(now, cfg, str(stored.get("last_slot") or "")):
                slot = digest_slot(now, cfg["time"])
                # The slot is closed before sending: a digest that fails half-way
                # (bot offline, render error) is logged, not re-sent every 30 s.
                await set_setting(DIGEST_STATE_KEY, {"last_slot": slot.date().isoformat(),
                                                     "sent_at": now_iso()}, audit=False)
                await send_digest()
            state.heartbeat("daily-digest")
        except Exception:
            logger.exception("Digest loop failed")
        await asyncio.sleep(DIGEST_POLL_SECONDS)


async def roulette_loop() -> None:
    """Daily nudge to spin the yobo roulette from every account.

    Ticks instead of sleeping to the target: a time reported mid-day applies at
    once, and a slot missed while the PC was off still fires on the next tick.
    """
    from database import get_setting, set_setting

    from .bot.cards import roulette_reminder_card
    from .bot.keyboards import roulette_reminder_keyboard
    from .roulette import ROULETTE_POLL_SECONDS, mark_sent, roulette_due, seed_cfg

    if await get_setting("roulette", None) is None:
        await set_setting("roulette", seed_cfg())

    while True:
        try:
            cfg = await ws.load_roulette_settings()
            now = datetime.now()
            # The day is only closed once the nudge actually left, so a bot that
            # was still connecting retries on the next tick instead of losing it.
            if roulette_due(now, cfg) and await send_admin_bot_message(
                roulette_reminder_card(cfg, now), buttons=roulette_reminder_keyboard()
            ):
                await set_setting("roulette", mark_sent(cfg, now))
                await record_app_event("INFO", "roulette", "Roulette reminder sent", {"time": cfg["time"]})
            state.heartbeat("roulette-reminder")
        except Exception:
            logger.exception("Roulette loop failed")
        await asyncio.sleep(ROULETTE_POLL_SECONDS)


PENDING_SEND_POLL_SECONDS = 20
PENDING_SEND_MAX_ATTEMPTS = 3
PENDING_SEND_STALE_HOURS = 24


async def pending_send_loop() -> None:
    """Drain ``bot_pending_sends``: deliver copies whose per-key delay elapsed."""
    # Monotonic deadline set by a FloodWait: until it passes the loop idles
    # instead of sleeping inside a batch, where one throttled recipient used to
    # stall every other queued copy for up to half an hour.
    flood_until = 0.0

    while True:
        try:
            state.heartbeat("pending-sends")
            if time.monotonic() >= flood_until:
                flood_until = await drain_pending_sends()
        except Exception:
            logger.exception("Pending send loop failed")
        await asyncio.sleep(PENDING_SEND_POLL_SECONDS)


async def drain_pending_sends() -> float:
    """One batch of the outbox. Returns a monotonic deadline to idle until.

    Split out of the loop so the failure handling — which decides whether a
    delayed notification is retried, dropped or left untouched — is reachable
    from a test without a clock or a running bot.
    """
    from database import cancel_pending_send, get_due_pending_sends, mark_pending_send_result, save_broadcast_messages

    from .bot_notify import BotOffline, RecipientUnreachable, deliver_pending_send

    try:
        from telethon.errors import FloodWaitError
    except ImportError:  # pragma: no cover
        FloodWaitError = Exception  # type: ignore[assignment, misc]

    for row in await get_due_pending_sends(limit=25):
        row_id = int(row["id"])
        # A long outage must not dump a backlog of stale wins on members.
        created_at = str(row.get("created_at") or "")
        stale_before = (datetime.now() - timedelta(hours=PENDING_SEND_STALE_HOURS)).isoformat()
        if created_at and created_at < stale_before:
            await cancel_pending_send(row_id)
            await record_app_event(
                "WARNING", "notifications", "Delayed notification dropped as stale",
                {"tg_id": row.get("tg_id"), "created_at": created_at},
            )
            continue
        if int(row.get("attempts") or 0) >= PENDING_SEND_MAX_ATTEMPTS:
            await cancel_pending_send(row_id)
            await record_app_event(
                "ERROR", "notifications", "Delayed notification gave up after retries",
                {"tg_id": row.get("tg_id"), "attempts": row.get("attempts")},
            )
            continue
        try:
            message_id = await deliver_pending_send(row)
        except BotOffline:
            # Nothing was attempted, so nothing is owed to this row. A minute of
            # downtime used to burn all three attempts and drop every due copy
            # for good.
            break
        except FloodWaitError as exc:
            # Our throttle, not this recipient's fault — no attempt spent.
            return time.monotonic() + flood_wait_seconds(exc.seconds)
        except RecipientUnreachable as exc:
            # Blocked the bot / never pressed start: retries cannot help.
            await cancel_pending_send(row_id)
            await record_app_event(
                "ERROR", "notifications", "Delayed notification dropped, recipient unreachable",
                {"tg_id": row.get("tg_id"), "error": str(exc)},
            )
            continue
        except Exception as exc:
            logger.warning("Delayed notification to %s failed: %s", row.get("tg_id"), exc)
            await mark_pending_send_result(row_id, sent=False)
            continue
        if message_id is None:
            await mark_pending_send_result(row_id, sent=False)
            continue
        await mark_pending_send_result(row_id, sent=True)
        token = str(row.get("token") or "")
        if token:
            # Same token as the immediate copies, so "hide" still reaches it.
            await save_broadcast_messages(token, [(int(row["tg_id"]), message_id)])
        await _autoclean_delayed_copy(row, message_id)
    return 0.0


async def _autoclean_delayed_copy(row: dict, message_id: int) -> None:
    """A delayed copy obeys the member's auto-delete choice like an immediate one."""
    from .autoclean import CLEANABLE_KINDS
    from .bot_notify import member_autoclean_hours, schedule_autoclean

    kind = str(row.get("notif_type") or "")
    if kind not in CLEANABLE_KINDS:
        return
    try:
        from database import get_bot_member

        member = await get_bot_member(int(row["tg_id"]))
        await schedule_autoclean(int(row["tg_id"]), message_id, kind, member_autoclean_hours(member))
    except Exception:
        logger.debug("Auto-delete scheduling for a delayed copy failed", exc_info=True)


async def _notify_access_flip(tg_id: int, allowed: bool, until) -> None:
    """Send a member a heads-up when their scheduled access opens/closes."""
    if not state.bot_client:
        return
    if allowed:
        msg = "🟢 **Доступ к боту открыт.**\nМожно пользоваться командами — /menu."
    else:
        phrase = f" до {until.astimezone().strftime('%d.%m %H:%M')}" if until else ""
        msg = f"🔴 **Доступ к боту закрыт по расписанию**{phrase}.\nОткроется автоматически."
    try:
        await state.bot_client.send_message(int(tg_id), msg, link_preview=False)
    except Exception as exc:
        logger.warning("Failed to notify member %s of access flip: %s", tg_id, exc)


async def access_scheduler_loop() -> None:
    """Keep the scheduled-access cache warm and notify members on boundary flips.

    NOT the source of truth — ``bot_role`` recomputes access on demand, so a
    missed tick only delays a notification, never grants/denies wrongly. On a
    fresh start ``last_state`` is empty, so the first pass primes the cache
    silently (no spurious "access changed" messages after a restart)."""
    from database import list_all_access_windows, list_bot_members, record_access_audit

    from .access_control import resolve_access, window_from_row

    last_state: dict[int, bool] = {}
    while True:
        try:
            now = datetime.now(timezone.utc)
            members = await list_bot_members()
            windows_by_user = await list_all_access_windows()
            seen: set[int] = set()
            for m in members:
                if m.get("blocked"):
                    continue
                tg = int(m["tg_id"])
                seen.add(tg)
                windows = [window_from_row(r) for r in windows_by_user.get(tg, [])]
                decision = resolve_access(m, windows, now)
                until = decision.until or (now + timedelta(minutes=1))
                state.access_cache[tg] = (decision.allowed, until, decision.reason)
                prev = last_state.get(tg)
                if prev is not None and prev != decision.allowed:
                    await record_access_audit(
                        tg, None, "flip", "scheduler",
                        {"allowed": prev}, {"allowed": decision.allowed, "reason": decision.reason},
                    )
                    await _notify_access_flip(tg, decision.allowed, until)
                    await record_app_event(
                        "INFO", "access", "Scheduled access flipped",
                        {"tg_id": tg, "allowed": decision.allowed, "reason": decision.reason},
                    )
                last_state[tg] = decision.allowed
            # Drop state for members that disappeared so they don't leak. The
            # shared cache needs the same treatment — pruning only the local dict
            # left deleted members cached in state.access_cache forever.
            for gone in set(last_state) - seen:
                last_state.pop(gone, None)
            for gone in set(state.access_cache) - seen:
                state.access_cache.pop(gone, None)
            state.heartbeat("access-scheduler")
        except Exception:
            logger.exception("Access scheduler loop failed")
        await asyncio.sleep(45)


async def obsidian_sync_loop() -> None:
    """Poll the Obsidian Долги note and reconcile it with the debt board."""
    from .obsidian_debts import sync_once

    interval = max(5, int(settings.obsidian_sync_poll_seconds or 30))
    while True:
        try:
            if settings.obsidian_sync_enabled and (settings.obsidian_debts_path or "").strip():
                await sync_once(state, settings)
            # Every pass, switched on or not: the job always runs, so the
            # watchdog can tell a live loop from a dead one either way.
            state.heartbeat("obsidian-sync")
        except Exception:
            logger.exception("Obsidian sync loop failed")
        await asyncio.sleep(interval)


SALARY_SETTINGS_KEY = "salary_sync"


def salary_path() -> Optional[Path]:
    """Путь к книге зарплат, или None когда раздел выключен."""
    raw = (settings.salary_xlsx_path or "").strip()
    return Path(raw) if raw else None


async def sync_salary_once(*, force: bool = False) -> bool:
    """Один проход синхронизации книги. True — снимок обновился.

    Книга перечитывается только когда изменились mtime или размер: Excel
    переписывает файл целиком при каждом сохранении, так что этой пары хватает,
    а разбор ~110 КБ XML уходит в поток (правило про блокирующие вызовы —
    handlers бота и uvicorn делят один event loop).
    """
    from database import get_setting, set_setting

    path = salary_path()
    if path is None:
        return False
    signature = salary.file_signature(path)
    if signature is None:
        state.salary_meta = {"path": str(path), "reason": "missing_file", "checked_at": now_iso()}
        return False
    stored = list(signature)
    if not force and state.salary_book is not None and state.salary_meta.get("signature") == stored:
        return False

    book = await asyncio.to_thread(salary.read_book, path)
    state.salary_book = book
    state.salary_meta = {
        "path": str(path),
        "signature": stored,
        "synced_at": now_iso(),
        "accounts": len(book.accounts),
        "months": len(book.months),
        "journal": len(book.journal),
        "issues": list(book.issues),
    }

    # Что уже выплачено — в настройках, а не в памяти: иначе перезапуск между
    # двумя сохранениями книги либо потеряет уведомление, либо пошлёт его снова.
    meta = await get_setting(SALARY_SETTINGS_KEY, None) or {}
    known = set(meta.get("paid") or [])
    pairs = salary.paid_pairs(book)
    fresh = [pair for pair in pairs if pair not in known] if meta.get("paid") is not None else []
    for pair in fresh:
        month, _, account = pair.partition("|")
        row = salary.salary_for(book, account, month)
        if row is None or row.total <= 0:
            continue
        await notify_salary_paid(row)
    await set_setting(SALARY_SETTINGS_KEY, {"paid": pairs, "synced_at": now_iso()}, audit=False)
    return True


async def notify_salary_paid(row) -> None:
    """Сказать человеку, что его зарплата за месяц отмечена выплаченной."""
    from database import list_bot_members

    from .bot.cards import salary_paid_notice

    text = salary_paid_notice(row)
    for member in await list_bot_members():
        if member.get("blocked"):
            continue
        if (member.get("key_label") or "").strip().casefold() != row.account.casefold():
            continue
        await send_member_bot_message(int(member["tg_id"]), text)
    await record_app_event(
        "INFO", "salary", "Salary marked paid",
        {"account": row.account, "month": row.month, "total": round(row.total, 2)},
    )


async def salary_sync_loop() -> None:
    """Держать снимок книги зарплат свежим — по одному опросу файла на тик."""
    interval = max(10, int(settings.salary_sync_poll_seconds or 60))
    while True:
        try:
            if salary_path() is not None:
                await sync_salary_once()
            state.heartbeat("salary-sync")
        except Exception:
            logger.exception("Salary sync loop failed")
        await asyncio.sleep(interval)


async def source_score_loop() -> None:
    from database import recalculate_source_scores

    while True:
        try:
            await recalculate_source_scores()
            state.heartbeat("source-scores")
        except Exception:
            logger.exception("Source score recalculation failed")
        await asyncio.sleep(300)


async def auto_scan_loop() -> None:
    start_supervised("market-volatility", monitor_market_volatility, backoff_base=60.0, backoff_max=1800.0)
    if ws.STARTUP_SCAN_DELAY_SECONDS:
        await asyncio.sleep(ws.STARTUP_SCAN_DELAY_SECONDS)
    waited = 0.0
    while not state.clients and waited < STARTUP_SCAN_WAIT_SECONDS:
        await asyncio.sleep(1)
        waited += 1
    # Retention, VACUUM and backups moved to the `maintenance` job: this loop
    # only scans, so a slow cleanup can no longer stretch the gap between sweeps.
    while True:
        cycle_started = time.monotonic()
        try:
            if state.clients:
                await full_history_scan()
                state.last_scan_finished_at = datetime.now()
                state.last_scan_status = "ok"
                await report_downtime_catchup()
            state.heartbeat("auto-scan")
        except Exception:
            logger.exception("Automatic scan loop failed")
            state.last_scan_status = "error"
        # Sleep the remainder of the cycle, not a full interval on top of the
        # sweep: otherwise a 20-minute sweep on a 15-minute interval left a
        # 35-minute blind spot between passes.
        await asyncio.sleep(next_scan_delay(ws.SCAN_INTERVAL_SECONDS, time.monotonic() - cycle_started))


# --------------------------------------------------------------------------- #
# Maintenance: retention, VACUUM, backups, stray files, disk space            #
# --------------------------------------------------------------------------- #

MAINTENANCE_KEY = "maintenance"
MAINTENANCE_TICK_SECONDS = 300
MAINTENANCE_FIRST_PASS_SECONDS = 600
MAINTENANCE_INTERVAL_SECONDS = 3600
DAILY_TASKS_INTERVAL = timedelta(hours=24)
BACKUP_EVERY = timedelta(hours=24)

_maintenance_lock = asyncio.Lock()
_maintenance_meta_lock = asyncio.Lock()
_disk_alerted = False


def _prune_files(directory: Path, pattern: str, keep_days: int) -> tuple[int, int]:
    """Remove files older than ``keep_days``; returns (count, bytes)."""
    cutoff = time.time() - keep_days * 86400
    removed = freed = 0
    if not directory.is_dir():
        return 0, 0
    for path in directory.glob(pattern):
        try:
            stat = path.stat()
            if path.is_file() and stat.st_mtime < cutoff:
                path.unlink()
                removed += 1
                freed += stat.st_size
        except OSError:
            continue
    return removed, freed


def _parse_stamp(raw) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


async def _load_maintenance_meta() -> dict:
    from database import get_setting

    meta = await get_setting(MAINTENANCE_KEY, None)
    return dict(meta) if isinstance(meta, dict) else {}


async def _save_maintenance_meta(**changes) -> None:
    """Merge into the ``maintenance`` key. Bookkeeping, so never audited.

    Read-modify-write under a lock: the liveness tick and a pass started from
    the bot would otherwise overwrite each other's fields.
    """
    from database import set_setting

    async with _maintenance_meta_lock:
        meta = await _load_maintenance_meta()
        meta.update(changes)
        await set_setting(MAINTENANCE_KEY, meta, audit=False)


async def run_maintenance_once(*, force_daily: bool = False) -> dict:
    """One maintenance pass. Safe to call from the bot while the loop runs.

    Order matters: rows are deleted first, VACUUM then returns their pages to
    the disk, and only then is a backup taken — so the copy is of the compact
    file, not of the free pages.
    """
    from database import (
        backups_total_bytes,
        cleanup_archive_db,
        cleanup_old_data,
        cleanup_outbox,
        cleanup_unbounded_tables,
        create_db_backup,
        db_page_stats,
        db_size_bytes,
        enforce_db_size_cap,
        newest_backup_at,
        prune_broadcast_messages,
        prune_db_backups,
        prune_pending_broadcasts,
        prune_pending_sends,
        vacuum_main_db,
    )

    async with _maintenance_lock:
        now = datetime.now()
        meta = await _load_maintenance_meta()
        db_before = db_size_bytes()
        backups_before = await asyncio.to_thread(backups_total_bytes)
        removed: dict = {}

        rows = await cleanup_old_data(days=MARKET_RETENTION_DAYS, pings_retention_days=PINGS_RETENTION_DAYS)
        rows.update(await cleanup_unbounded_tables(
            scan_runs_keep=SCAN_RUNS_RETENTION,
            audit_days=AUDIT_RETENTION_DAYS,
            live_sessions=state.session_names,
            tracked_usernames=state.ping_usernames,
        ))
        removed.update({key: value for key, value in rows.items() if value and key != "vacuumed"})
        cap = await enforce_db_size_cap(DB_MAX_SIZE_MB, archive=DB_ARCHIVE_ENABLED)
        if cap.get("pings_deleted"):
            removed["size_cap_pings"] = cap["pings_deleted"]

        daily_due = force_daily or (now - (_parse_stamp(meta.get("last_daily_at")) or datetime.min)) >= DAILY_TASKS_INTERVAL
        if daily_due:
            await cleanup_outbox(days=2, max_events=2500)
            for label, prune in (
                ("broadcast_messages", prune_broadcast_messages),
                ("pending_broadcasts", prune_pending_broadcasts),
                ("pending_sends", prune_pending_sends),
            ):
                count = await prune(days=7)
                if count:
                    removed[label] = count

        pages = await db_page_stats()
        vacuumed = housekeeping.vacuum_due(
            page_count=pages["page_count"],
            freelist_count=pages["freelist_count"],
            page_size=pages["page_size"],
            last_vacuum_at=_parse_stamp(meta.get("last_vacuum_at")),
            now=now,
            interval_hours=VACUUM_INTERVAL_HOURS,
        )
        if vacuumed:
            await vacuum_main_db()
            meta_vacuum = now_iso()
            pages = await db_page_stats()
        archive = await cleanup_archive_db(ARCHIVE_RETENTION_DAYS, vacuum=vacuumed)
        if archive.get("archive_pings"):
            removed["archive_pings"] = archive["archive_pings"]

        newest = await asyncio.to_thread(newest_backup_at)
        backup_name = None
        if newest is None or now - newest >= BACKUP_EVERY:
            created = await asyncio.to_thread(create_db_backup)
            backup_name = (created or {}).get("name")
        else:
            await asyncio.to_thread(prune_db_backups)

        files_removed = files_freed = 0
        for directory, pattern, keep_days in housekeeping.FILE_RULES:
            count, freed = await asyncio.to_thread(_prune_files, BASE_DIR / directory, pattern, keep_days)
            files_removed += count
            files_freed += freed
        if files_removed:
            removed["files"] = files_removed

        backups_after = await asyncio.to_thread(backups_total_bytes)
        db_after = db_size_bytes()
        disk = await asyncio.to_thread(shutil.disk_usage, BASE_DIR)
        await _check_disk(disk.free)

        stats = {
            "finished_at": now_iso(),
            "db_bytes": db_after,
            "freelist_pct": round(housekeeping.freelist_ratio(pages["page_count"], pages["freelist_count"]) * 100, 1),
            "backups_bytes": backups_after,
            "disk_free_bytes": disk.free,
            "vacuumed": vacuumed,
            "backup": backup_name,
            "removed": removed,
            "freed_bytes": max(0, db_before - db_after) + max(0, backups_before - backups_after) + files_freed,
        }
        state.maintenance_stats = stats
        changes = {"last_run_at": stats["finished_at"]}
        if vacuumed:
            changes["last_vacuum_at"] = meta_vacuum
        if daily_due:
            changes["last_daily_at"] = stats["finished_at"]
        await _save_maintenance_meta(**changes)
        if removed or vacuumed or backup_name:
            await record_app_event("INFO", "maintenance", "Maintenance pass completed", {
                "removed": removed, "vacuumed": vacuumed, "backup": backup_name,
                "freed_mb": round(stats["freed_bytes"] / housekeeping.MB, 1),
            })
        return stats


async def _check_disk(free_bytes: int) -> None:
    """Page the owner once when the disk runs low, and once more when it recovers."""
    global _disk_alerted
    low = housekeeping.disk_low(free_bytes, DISK_FREE_ALERT_MB)
    free_mb = free_bytes // housekeeping.MB
    if low and not _disk_alerted:
        _disk_alerted = True
        await send_admin_bot_message(
            "⚠️ **Заканчивается место на диске**\n"
            "━━━━━━━━━━━━━━━\n"
            f"Свободно: `{free_mb} МБ` (порог `{DISK_FREE_ALERT_MB} МБ`).\n"
            "База, бэкапы и логи могут перестать записываться. Освободите место на диске."
        )
        await record_app_event("WARNING", "maintenance", "Low disk space", {"free_mb": free_mb})
    elif not low and _disk_alerted:
        _disk_alerted = False
        await send_admin_bot_message(f"✅ **Место на диске освободилось**: свободно `{free_mb} МБ`.", kind="system")


async def maintenance_loop() -> None:
    """Hourly housekeeping, plus a liveness stamp every tick.

    ``last_alive_at`` is what the next start compares against to tell a routine
    restart from the nightly shutdown (see ``detect_downtime``).
    """
    # The first pass waits a little: startup already runs a backup, the search
    # reindex and the first sweep, and VACUUM would compete with all of them.
    next_pass = time.monotonic() + MAINTENANCE_FIRST_PASS_SECONDS
    while True:
        try:
            await _save_maintenance_meta(last_alive_at=now_iso())
            if time.monotonic() >= next_pass:
                # Advanced before the pass, so a failing pass is retried next
                # hour rather than on every tick.
                next_pass = time.monotonic() + MAINTENANCE_INTERVAL_SECONDS
                await run_maintenance_once()
            state.heartbeat("maintenance")
        except Exception:
            logger.exception("Maintenance loop failed")
        await asyncio.sleep(max(1.0, min(MAINTENANCE_TICK_SECONDS, next_pass - time.monotonic())))


async def detect_downtime() -> None:
    """Remember a long gap before this start; call before ``maintenance`` starts.

    The previous process stamped ``last_alive_at`` every few minutes, so the
    gap between that stamp and now is how long nothing was watching Telegram.
    """
    try:
        meta = await _load_maintenance_meta()
        last_alive = _parse_stamp(meta.get("last_alive_at"))
        now = datetime.now()
        if housekeeping.gap_worth_reporting(last_alive, now):
            state.downtime_gap = (last_alive, now.replace(microsecond=0))
            await record_app_event("INFO", "app", "Downtime before start detected", {
                "from": last_alive.isoformat(timespec="minutes"),
                "to": now.isoformat(timespec="minutes"),
            })
    except Exception:
        logger.warning("Could not read the previous liveness stamp", exc_info=True)


async def report_downtime_catchup() -> None:
    """After the first sweep that follows a long downtime, say what it found."""
    from database import get_pings

    from .bot.cards import downtime_catchup_card

    gap = state.downtime_gap
    if gap is None:
        return
    state.downtime_gap = None
    gap_from, gap_to = gap
    try:
        pings = await get_pings(limit=500, date_from=gap_to.isoformat())
        await send_admin_bot_message(downtime_catchup_card(gap_from, gap_to, pings))
    except Exception:
        logger.exception("Downtime catch-up report failed")


# --------------------------------------------------------------------------- #
# Weekly / monthly report                                                       #
# --------------------------------------------------------------------------- #

REPORT_TICK_SECONDS = 60


async def run_reports_once(now: Optional[datetime] = None) -> list[str]:
    """Send whichever scheduled report is due (Monday: the week, the 1st: the month)."""
    from database import get_setting, set_setting

    from .bot.sections.report import render
    from .report import STATE_KEY, due_reports, week_key

    now = now or datetime.now()
    stored = await get_setting(STATE_KEY, None)
    sent_state = dict(stored) if isinstance(stored, dict) else {}
    sent: list[str] = []
    for kind in due_reports(now, sent_state):
        # Closed before sending, like the digest: a failing render is logged,
        # not retried every minute.
        sent_state[kind] = week_key(now) if kind == "week" else f"{now:%Y-%m}"
        await set_setting(STATE_KEY, sent_state, audit=False)
        caption, card = await render(kind, previous=True)
        await send_admin_bot_message(caption, file=card)
        await record_app_event("INFO", "report", "Scheduled report sent", {"kind": kind})
        sent.append(kind)
    return sent


async def report_loop() -> None:
    while True:
        try:
            await run_reports_once()
            state.heartbeat("weekly-report")
        except Exception:
            logger.exception("Report loop failed")
        await asyncio.sleep(REPORT_TICK_SECONDS)


# --------------------------------------------------------------------------- #
# Account health: logged out, banned, stuck, deaf                               #
# --------------------------------------------------------------------------- #

ACCOUNT_HEALTH_TICK_SECONDS = 300
ACCOUNT_PROBE_INTERVAL_SECONDS = 3600
ACCOUNT_PROBE_TIMEOUT_SECONDS = 20

ACCOUNT_ALERTS_KEY = "account_alerts"

# Account → problem kind already reported. Persisted (audit=False) so a restart
# every morning does not re-page about the same logged-out junk session.
_account_alerts: dict[str, str] = {}
_account_alerts_loaded = False


async def probe_accounts() -> dict[str, str]:
    """Ask every online account «who am I?» — the cheapest request that fails when
    the session was revoked or the account banned while the socket stayed up.
    Returns session → new status for the accounts whose probe proved a problem."""
    from .account_health import classify_auth_error

    found: dict[str, str] = {}
    for client in list(state.clients):
        name = str(getattr(client, "_session_name_custom", "") or "")
        if not name:
            continue
        try:
            await asyncio.wait_for(client.get_me(), timeout=ACCOUNT_PROBE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            status = classify_auth_error(exc)
            if not status:
                continue
            found[name] = status
            account = state.accounts_state.setdefault(name, {"session_name": name})
            account.update({"status": status, "last_error": type(exc).__name__, "status_since": now_iso()})
            await record_app_event("ERROR", "telegram", "Account probe failed",
                                   {"session_name": name, "status": status, "error": type(exc).__name__})
    return found


async def run_account_health_once(now: Optional[datetime] = None) -> tuple[dict[str, str], list[str]]:
    """Evaluate every account and page the owner about what changed."""
    from database import get_setting, set_setting

    from .account_health import account_problem, diff_problems

    global _account_alerts_loaded
    if not _account_alerts_loaded:
        stored = await get_setting(ACCOUNT_ALERTS_KEY, None)
        _account_alerts.update({str(k): str(v) for k, v in (stored or {}).items()} if isinstance(stored, dict) else {})
        _account_alerts_loaded = True
    now = now or datetime.now()
    current = {
        name: problem
        for name, account in list(state.accounts_state.items())
        if (problem := account_problem(account, now, app_started_at=state.started_at))
    }
    fresh, recovered = diff_problems(_account_alerts, current)
    # «Recovered» means back online, not «not evaluated yet»: right after a start
    # every account is still connecting, which is no reason to announce anything.
    recovered = [name for name in recovered
                 if (state.accounts_state.get(name) or {}).get("status") == "online"]
    for name in list(_account_alerts):
        if name not in current and name not in recovered:
            current[name] = (_account_alerts[name], "")
    for name, text in fresh.items():
        await send_admin_bot_message(
            "🛰 **Проблема с аккаунтом**\n━━━━━━━━━━━━━━━\n"
            f"Аккаунт: `{name}`\n{text}\n\n"
            "Пока это так, аккаунт не видит свои каналы. Подробности — 🛰 Аккаунты."
        )
        await record_app_event("WARNING", "telegram", "Account problem", {"session_name": name, "problem": text})
    for name in recovered:
        await send_admin_bot_message(f"✅ **Аккаунт снова в строю:** `{name}`", kind="system")
    reported = {name: kind for name, (kind, _text) in current.items()}
    if reported != _account_alerts:
        _account_alerts.clear()
        _account_alerts.update(reported)
        await set_setting(ACCOUNT_ALERTS_KEY, reported, audit=False)
    return fresh, recovered


async def account_health_loop() -> None:
    # Accounts need a few minutes to connect after a start; judging them sooner
    # would page about «подключается».
    await asyncio.sleep(180)
    last_probe = time.monotonic()
    while True:
        try:
            if time.monotonic() - last_probe >= ACCOUNT_PROBE_INTERVAL_SECONDS:
                last_probe = time.monotonic()
                await probe_accounts()
            await run_account_health_once()
            state.heartbeat("account-health")
        except Exception:
            logger.exception("Account health check failed")
        await asyncio.sleep(ACCOUNT_HEALTH_TICK_SECONDS)


# --------------------------------------------------------------------------- #
# Bot janitor: stale prompts, abandoned logins, per-user leftovers, auto-delete #
# --------------------------------------------------------------------------- #

JANITOR_TICK_SECONDS = 60
DEBT_MARKS_TTL = timedelta(hours=1)


async def run_janitor_once(now: Optional[datetime] = None) -> dict[str, int]:
    """One sweep of the bot's short-lived state and scheduled deletions."""
    from database import delete_ephemeral_rows, get_due_ephemeral_messages, prune_ephemeral_messages

    from .account_login import sweep_expired
    from .bot.pending import delete_quietly, sweep_pending
    from .bot.sections.feed import QUERY_TTL

    now = now or datetime.now()
    stats = {"prompts": 0, "logins": 0, "queries": 0, "marks": 0, "deleted": 0}

    # Armed prompts nobody answered: the entry goes, and so does its «✍️» message.
    for entry in sweep_pending():
        stats["prompts"] += 1
        await delete_quietly(entry.get("chat_id"), entry.get("cleanup") or [])

    # A login abandoned half-way holds a connected Telegram client.
    stats["logins"] = await sweep_expired()

    for sender, (_text, armed) in list(state.bot_feed_queries.items()):
        if now - armed > QUERY_TTL:
            state.bot_feed_queries.pop(sender, None)
            stats["queries"] += 1
    from .bot.sections.vacation import check_expiry
    from .bot.undo import sweep as sweep_undo

    sweep_undo(now)
    from .check_claimer import sweep_relays

    sweep_relays(now)
    try:
        await check_expiry(now)
    except Exception:
        logger.exception("Vacation expiry check failed")
    for sender, touched in list(state.bot_debt_marks_touched.items()):
        if now - touched > DEBT_MARKS_TTL:
            state.bot_debt_marks_touched.pop(sender, None)
            if state.bot_debt_marks.pop(sender, None) is not None:
                stats["marks"] += 1

    # Minor notifications whose auto-delete moment came.
    due = await get_due_ephemeral_messages(now)
    by_chat: dict[int, list[int]] = {}
    for row in due:
        by_chat.setdefault(int(row["chat_id"]), []).append(int(row["message_id"]))
    for chat_id, message_ids in by_chat.items():
        for start in range(0, len(message_ids), 100):
            stats["deleted"] += await delete_quietly(chat_id, message_ids[start:start + 100])
    if due:
        # Whatever happened to the delete (already gone, too old), the row is done.
        await delete_ephemeral_rows(int(row["id"]) for row in due)
    await prune_ephemeral_messages(now)
    return stats


async def bot_janitor_loop() -> None:
    while True:
        try:
            if state.bot_client is not None:
                await run_janitor_once()
            state.heartbeat("bot-janitor")
        except Exception:
            logger.exception("Bot janitor failed")
        await asyncio.sleep(JANITOR_TICK_SECONDS)


def _collect_job_health() -> list[JobHealth]:
    """Snapshot the health of every monitored background job right now."""
    thresholds = default_thresholds(
        scan_interval_seconds=ws.SCAN_INTERVAL_SECONDS,
        market_poll_seconds=ws.MARKET_POLL_SECONDS,
        flood_wait_max_seconds=FLOOD_WAIT_MAX_SECONDS,
        bot_configured=state.bot_client is not None,
        enabled_features=feature_job_polls(settings),
    )
    now = datetime.now()
    healths: list[JobHealth] = []
    for name, threshold in thresholds.items():
        task = state.background_tasks.get(name)
        running = bool(task) and not task.done()
        ref = state.job_last_ok_at.get(name) or state.job_started_at.get(name)
        age = int((now - ref).total_seconds()) if ref else None
        healths.append(classify_job(name, running=running, age_seconds=age, threshold_seconds=threshold))
    return healths


async def _alert_job_unhealthy(health: JobHealth) -> None:
    if health.reason == "missing":
        detail = "не запущена — упала и не перезапустилась"
        level = "ERROR"
    else:
        detail = f"молчит {format_age(health.age_seconds)} (порог {format_age(health.threshold_seconds)})"
        level = "ERROR" if health.name == "auto-scan" else "WARNING"
    msg = (
        "🛑 **Сбой фоновой задачи**\n"
        "━━━━━━━━━━━━━━━\n"
        f"Задача: `{health.name}`\n"
        f"Статус: {detail}\n\n"
        "Движок мог перестать ловить события. Проверьте /logs или перезапустите приложение."
    )
    await send_admin_bot_message(msg)
    await record_app_event(
        level, "watchdog", f"Background job unhealthy: {health.name}",
        {"reason": health.reason, "age_seconds": health.age_seconds},
    )


async def _alert_job_recovered(health: JobHealth) -> None:
    await send_admin_bot_message(f"✅ **Задача восстановилась**: `{health.name}` снова отвечает.", kind="system")
    await record_app_event("INFO", "watchdog", f"Background job recovered: {health.name}", None)


async def watchdog_loop() -> None:
    """Page the admin when a critical background job dies or stops reporting.

    Pure decision logic lives in ``watchdog.py``; this loop only gathers ages,
    sends Telegram alerts, and remembers which jobs are already flagged so the
    admin is notified once per outage (plus one recovery message). It is itself
    supervised, so if it crashes the supervisor restarts it."""
    poll = max(20, int(settings.watchdog_poll_seconds or 60))
    unhealthy: set[str] = set()
    while not state.shutting_down:
        try:
            healths = _collect_job_health()
            new_alerts, recoveries = diff_health(unhealthy, healths)
            for health in new_alerts:
                unhealthy.add(health.name)
                await _alert_job_unhealthy(health)
            for health in recoveries:
                unhealthy.discard(health.name)
                await _alert_job_recovered(health)
        except Exception:
            logger.exception("Watchdog loop failed")
        state.heartbeat("watchdog")
        await asyncio.sleep(poll)


async def startup_maintenance() -> None:
    from database import (
        rebuild_search_indexes,
        reconcile_giveaway_flags,
        reconcile_giveaway_outcomes,
        reconcile_win_flags,
        unglue_same_chat_copies,
    )

    # Pruning of broadcast/outbox bookkeeping moved to the daily part of the
    # `maintenance` job: here it only ever ran on a restart.

    try:
        search_reindex = await rebuild_search_indexes()
        await record_app_event("INFO", "search", "Search indexes rebuilt on startup", search_reindex)
        # Wins first: the giveaway pass keeps every win paired with its giveaway
        # flag, so it has to see the win flags as they will stay.
        win_reconcile = await reconcile_win_flags(state.win_keywords)
        if win_reconcile["enabled"] or win_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored win/result flags", win_reconcile)
        giveaway_reconcile = await reconcile_giveaway_flags(state.giveaway_keywords)
        if giveaway_reconcile["enabled"] or giveaway_reconcile["disabled"]:
            await record_app_event("INFO", "giveaway", "Reconciled stored giveaways with channel keyword rule", giveaway_reconcile)
        outcome_reconcile = await reconcile_giveaway_outcomes()
        if outcome_reconcile["marked"]:
            await record_app_event("INFO", "giveaway", "Marked giveaway result posts as prize claims", outcome_reconcile)
        from .ping_pipeline import dedupe_existing_wins

        # Before the glue pass: it re-points copies of copies, and would carry
        # a post glued in its own chat along to the new primary.
        released = await unglue_same_chat_copies()
        if released:
            await record_app_event("INFO", "giveaway", "Released wins glued to a post of their own chat",
                                   {"released": released})
        copies = await dedupe_existing_wins()
        if copies:
            await record_app_event("INFO", "giveaway", "Glued copies of the same winners post", {"copies": copies})
    except Exception as exc:
        logger.exception("Startup maintenance failed")
        await record_app_event("ERROR", "app", "Startup maintenance failed", {"error": str(exc)})
