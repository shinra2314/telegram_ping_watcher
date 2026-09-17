"""What the unclaimed wins are worth, per tracked account (pure).

Most giveaway posts state the prize as an amount with a currency — «4.00 USDT
💵 x 4», «ФАСТ 5 USDT», «Конкурс на 5$», «1💵 300cек». Skins and bots have no
price and stay "без оценки": an honest count beats a made-up number.

Currency words are resolved by the converter's own vocabulary (``code_of``), so
«5 тон», «100 грн» and «$5» are recognised the same way the converter reads
them, and the amount is priced from the newest market snapshot — no network.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from .converter import code_of, convert

# «5 USDT», «5$», «100 грн», «1💵»; the token after the number is checked
# against the converter vocabulary, so «5 победителей» or «2 часа» never match.
_AMOUNT_THEN_UNIT = re.compile(r"(\d+(?:[.,]\d+)?)\s*([$€₴₽£💵]|[A-Za-zА-Яа-яЁё₮]{2,12})")
# «$5», «₴100».
_SIGN_THEN_AMOUNT = re.compile(r"([$€₴₽£])\s*(\d+(?:[.,]\d+)?)")
# 💵 in these channels means dollars (USDT, really); count it as USD.
_EMOJI_UNITS = {"💵": "USD"}
_WORD_TICKERS = frozenset({"NOT", "TRY"})
# Plausible single-prize ceiling: above it the number is a subscriber count or
# a date, not a prize.
MAX_PRIZE_USD = 10_000


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def parse_prizes(text: str) -> list[tuple[float, str]]:
    """Every (amount, currency code) the text states."""
    found: list[tuple[float, str]] = []
    # Signs already read as the unit of a preceding amount: in «4$ 15мин» the
    # `$` belongs to 4, and must not be read again as «$15».
    used_units: set[int] = set()
    for match in _AMOUNT_THEN_UNIT.finditer(text or ""):
        amount_raw, unit = match.groups()
        code = _EMOJI_UNITS.get(unit) or code_of(unit)
        # «NOT» and «TRY» are ordinary English words; only the ticker in capitals counts.
        if code in _WORD_TICKERS and unit != code:
            continue
        amount = _to_float(amount_raw)
        if code and amount:
            found.append((amount, code))
            used_units.add(match.start(2))
    for match in _SIGN_THEN_AMOUNT.finditer(text or ""):
        if match.start(1) in used_units:
            continue
        sign, amount_raw = match.groups()
        code = code_of(sign)
        amount = _to_float(amount_raw)
        if code and amount:
            found.append((amount, code))
    return found


def prize_usd(text: str, snapshot: Optional[dict[str, Any]]) -> Optional[float]:
    """The largest stated prize in USD; None when nothing priceable is stated."""
    if not snapshot:
        return None
    best: Optional[float] = None
    for amount, code in parse_prizes(text):
        usd = convert(snapshot, amount, code, "USD")
        if usd is None or usd <= 0 or usd > MAX_PRIZE_USD:
            continue
        best = usd if best is None else max(best, usd)
    return best


def value_by_account(rows: Iterable[dict[str, Any]], tracked: Iterable[str],
                     snapshot: Optional[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """``{account: {"usd", "priced", "unpriced"}}`` over unclaimed win rows.

    A win naming two tracked accounts counts for both — each of them has to go
    and claim it.
    """
    names = {str(u).strip().lstrip("@").lower(): str(u).strip().lstrip("@") for u in tracked if u}
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        mentions = row.get("mentions") or []
        if isinstance(mentions, str):
            mentions = [m.strip() for m in mentions.strip("[]").replace('"', "").split(",")]
        accounts = [names[m.strip().lstrip("@").lower()] for m in mentions
                    if m and m.strip().lstrip("@").lower() in names]
        if not accounts:
            continue
        usd = prize_usd(str(row.get("text") or ""), snapshot)
        for account in dict.fromkeys(accounts):
            bucket = out.setdefault(account, {"usd": 0.0, "priced": 0, "unpriced": 0})
            if usd is None:
                bucket["unpriced"] += 1
            else:
                bucket["usd"] += usd
                bucket["priced"] += 1
    return out


def totals(by_account: dict[str, dict[str, float]], rows_usd: Optional[float] = None) -> dict[str, float]:
    """Sum over accounts (a shared win is counted once per account — see above)."""
    return {
        "usd": sum(b["usd"] for b in by_account.values()),
        "priced": sum(b["priced"] for b in by_account.values()),
        "unpriced": sum(b["unpriced"] for b in by_account.values()),
    }


def in_uah(usd: float, snapshot: Optional[dict[str, Any]]) -> Optional[float]:
    return convert(snapshot, usd, "USD", "UAH") if snapshot else None
