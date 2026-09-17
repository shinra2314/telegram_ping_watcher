"""Курсы и конвертер.

Конвертер считает **на странице**: сюда отдаётся цена каждого кода в долларах
(``usd``), и любая пара — это ``amount * usd[src] / usd[dst]``. Так ввод в поле
пересчитывается на каждое нажатие без запроса. ``/convert?q=`` — для свободной
строки («100 usd в грн»): её разбирает тот же ``parse_query``, что и бот.
Всё читается из последнего снимка ``market_history`` — сети на запросе нет.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

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
