"""Курсы и конвертер.

Конвертер считает **на странице**: сюда отдаётся цена каждого кода в долларах
(``usd``), и любая пара — это ``amount * usd[src] / usd[dst]``. Так ввод в поле
пересчитывается на каждое нажатие без запроса. ``/convert?q=`` — для свободной
строки («100 usd в грн»): её разбирает тот же ``parse_query``, что и бот.
Всё читается из последнего снимка ``market_history`` — сети на запросе нет.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from database import get_market_history

from pulse_desk.bot.sections.market import latest_snapshot
from pulse_desk.converter import (
    CRYPTO, FIAT, convert, fmt_amount, fmt_money, parse_query, snapshot_time, usd_value,
)

from .common import Caller, current_caller

router = APIRouter()

FEATURE = "market"


def rate_table(snapshot: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Всё, что нужно экрану курсов и клиентскому конвертеру. Чистая функция."""
    if not snapshot:
        return {"updated": "", "usd": {}, "coins": [], "fiat": []}
    usd: dict[str, float] = {}
    for code in [*CRYPTO, *FIAT]:
        value = usd_value(snapshot, code)
        if value:
            usd[code] = value
    coins = []
    for code, (coingecko_id, emoji) in CRYPTO.items():
        quote = snapshot.get(coingecko_id) or {}
        if code not in usd:
            continue
        coins.append({
            "code": code,
            "emoji": emoji,
            "usd": usd[code],
            "usd_text": fmt_money(usd[code], "USD"),
            "change": quote.get("usd_24h_change"),
        })
    fiat = [
        {"code": code, "sign": sign, "flag": flag, "per_usd": 1 / usd[code]}
        for code, (sign, flag) in FIAT.items()
        if code in usd and code != "USD"
    ]
    return {"updated": snapshot_time(snapshot), "usd": usd, "coins": coins, "fiat": fiat,
            "signs": {code: sign for code, (sign, _flag) in FIAT.items()},
            "flags": {**{c: f for c, (_s, f) in FIAT.items()},
                      **{c: e for c, (_id, e) in CRYPTO.items()}}}


@router.get("/api/app/market")
async def market(caller: Caller = Depends(current_caller)) -> dict:
    caller.require(FEATURE)
    return rate_table(await latest_snapshot())


# Longer would parse thousands of snapshots on the shared event loop.
HISTORY_DAYS = (1, 7)
HISTORY_POINTS = 84
HISTORY_CACHE_SECONDS = 300.0
_history_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}


def history_points(snapshots: list[dict[str, Any]], src: str, dst: str,
                   points: int = HISTORY_POINTS) -> list[dict[str, Any]]:
    """Oldest-first ``[{t, v}]`` of 1 ``src`` in ``dst``, thinned to ``points``.

    Snapshots come newest first from ``get_market_history``. One that cannot
    price the pair (a coin missing, a snapshot from before ``_fiat``) is
    skipped rather than drawn as zero. Pure — the endpoint only fetches.
    """
    series = []
    for snap in reversed(snapshots):
        value = convert(snap, 1.0, src, dst)
        if value:
            series.append({"t": snap.get("fetched_at_iso"), "v": value})
    if len(series) <= points:
        return series
    step = len(series) / points
    thinned = [series[int(i * step)] for i in range(points)]
    thinned[-1] = series[-1]
    return thinned


@router.get("/api/app/market/history")
async def market_history(
    caller: Caller = Depends(current_caller),
    src: str = Query("BTC", pattern="^[A-Z]{2,6}$"),
    dst: str = Query("USD", pattern="^[A-Z]{2,6}$"),
    days: int = Query(7),
) -> dict:
    """The pair's line for the converter — from our own ``market_history``
    snapshots, so drawing it costs no request to CoinGecko."""
    caller.require(FEATURE)
    if src not in {*CRYPTO, *FIAT} or dst not in {*CRYPTO, *FIAT}:
        raise HTTPException(status_code=422, detail="Такой валюты нет")
    days = days if days in HISTORY_DAYS else 7
    key = (src, dst, days)
    now = time.monotonic()
    hit = _history_cache.get(key)
    if hit and now - hit[0] < HISTORY_CACHE_SECONDS:
        return hit[1]
    since = (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    snapshots = await get_market_history(limit=20000, since_iso=since)
    body = {"src": src, "dst": dst, "days": days, "points": history_points(snapshots, src, dst)}
    for stale in [k for k, (at, _body) in _history_cache.items() if now - at >= HISTORY_CACHE_SECONDS]:
        del _history_cache[stale]
    _history_cache[key] = (now, body)
    return body


@router.get("/api/app/convert")
async def convert_text(
    caller: Caller = Depends(current_caller),
    q: str = Query(..., min_length=1, max_length=80),
) -> dict:
    caller.require(FEATURE)
    query = parse_query(q)
    if query is None:
        raise HTTPException(status_code=422, detail="Не понял. Пример: 100 usd в грн")
    snapshot = await latest_snapshot()
    value = convert(snapshot, query.amount, query.src, query.dst) if snapshot else None
    if value is None:
        raise HTTPException(status_code=422, detail="Такую пару пока не посчитать")
    return {
        "amount": query.amount,
        "src": query.src,
        "dst": query.dst,
        "value": value,
        "from_text": fmt_amount(query.amount, query.src),
        "to_text": fmt_money(value, query.dst),
        "updated": snapshot_time(snapshot),
    }
