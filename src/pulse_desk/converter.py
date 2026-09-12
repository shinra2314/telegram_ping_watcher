"""Currency + crypto conversion on top of the cached market snapshots.

Everything here is pure: a caller hands in a snapshot (the dict shape
``database.get_market_history`` returns) and gets numbers or Markdown back.
No network call happens during a conversion — the ``market-fetch`` loop
already stores CoinGecko quotes every ``MARKET_POLL_SECONDS``, so the bot
answers instantly and keeps working while CoinGecko is down.

Fiat rates ride along in that same snapshot under ``_fiat`` (currency ->
units per 1 USD), written by :func:`fiat_block` at fetch time. Older
snapshots have no ``_fiat`` key; for them the rate is recovered from the
per-coin quotes the snapshot does carry (``usd`` and ``uah`` are always
there), so an upgrade needs no migration and no backfill.
"""
from __future__ import annotations

import re
from datetime import datetime
from statistics import median
from typing import Any, NamedTuple, Optional

from .bot.chrome import empty, header, kv

DIV = "━━━━━━━━━━━━━━━"

# code -> (coingecko id, emoji). The eight coins the market loop already polls.
CRYPTO: dict[str, tuple[str, str]] = {
    "BTC": ("bitcoin", "🟠"),
    "ETH": ("ethereum", "🔷"),
    "TON": ("the-open-network", "💎"),
    "SOL": ("solana", "🟣"),
    "BNB": ("binancecoin", "🟡"),
    "NOT": ("notcoin", "🪙"),
    "DOGS": ("dogs-2", "🐕"),
    "USDT": ("tether", "💵"),
}

# code -> (sign, flag). Every one of these is a CoinGecko `vs_currency`;
# KZT and BYN are not supported upstream, so they are deliberately absent.
FIAT: dict[str, tuple[str, str]] = {
    "USD": ("$", "🇺🇸"),
    "UAH": ("₴", "🇺🇦"),
    "EUR": ("€", "🇪🇺"),
    "RUB": ("₽", "🇷🇺"),
    "PLN": ("zł", "🇵🇱"),
    "GBP": ("£", "🇬🇧"),
    "TRY": ("₺", "🇹🇷"),
    "CZK": ("Kč", "🇨🇿"),
    "CHF": ("Fr", "🇨🇭"),
    "CAD": ("C$", "🇨🇦"),
    "JPY": ("¥", "🇯🇵"),
    "CNY": ("元", "🇨🇳"),
}

FIAT_CODES: list[str] = list(FIAT)
# Coins used to derive fiat cross-rates. Two, so one stale quote cannot skew it.
BRIDGE_IDS: tuple[str, ...] = ("tether", "bitcoin")

# Exact spellings, then prefixes below for the Russian case forms.
_ALIASES: dict[str, str] = {
    "$": "USD", "usd": "USD", "уе": "USD",
    "₴": "UAH", "uah": "UAH", "grn": "UAH", "hrn": "UAH",
    "€": "EUR", "eur": "EUR",
    "₽": "RUB", "rub": "RUB", "rur": "RUB",
    "zł": "PLN", "pln": "PLN", "zl": "PLN",
    "£": "GBP", "gbp": "GBP",
    "₺": "TRY", "try": "TRY",
    "kč": "CZK", "czk": "CZK",
    "chf": "CHF",
    "cad": "CAD",
    "¥": "JPY", "jpy": "JPY",
    "元": "CNY", "cny": "CNY", "rmb": "CNY",
    "btc": "BTC", "xbt": "BTC",
    "eth": "ETH",
    "ton": "TON",
    "sol": "SOL",
    "bnb": "BNB",
    "not": "NOT",
    "dogs": "DOGS",
    "usdt": "USDT", "usd₮": "USDT", "tether": "USDT",
}

# Russian stems — matched by prefix so every case form resolves without a table.
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("доллар", "USD"), ("бакс", "USD"), ("долл", "USD"),
    ("гривн", "UAH"), ("гривен", "UAH"), ("грн", "UAH"),
    ("евро", "EUR"),
    ("рубл", "RUB"), ("руб", "RUB"),
    ("злот", "PLN"),
    ("фунт", "GBP"),
    ("лир", "TRY"),
    ("крон", "CZK"),
    ("франк", "CHF"),
    ("иен", "JPY"), ("йен", "JPY"),
    ("юан", "CNY"),
    ("биткоин", "BTC"), ("битк", "BTC"), ("биток", "BTC"), ("бтк", "BTC"),
    ("эфир", "ETH"),
    ("тонкоин", "TON"), ("тон", "TON"),
    ("солан", "SOL"),
    ("ноткоин", "NOT"),
    ("тезер", "USDT"), ("юсдт", "USDT"),
    ("догс", "DOGS"),
)

# Filler between the two currencies: "100 usd в грн", "5 ton to usd".
_STOP_WORDS = frozenset({
    "в", "во", "на", "из", "к", "по", "это", "будет", "сколько", "стоит",
    "to", "in", "into", "of", "is", "eq", "equals",
})
_SYMBOLS = "$€₴₽£₺¥元"
_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


class Query(NamedTuple):
    amount: float
    src: str
    dst: str


def code_of(token: str) -> Optional[str]:
    """Resolve one word/symbol to a currency code; None when unrecognised."""
    cleaned = (token or "").strip().strip(".,;:!?()").lower()
    if not cleaned:
        return None
    upper = cleaned.upper()
    if upper in CRYPTO or upper in FIAT:
        return upper
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    # Longest prefix wins, so "тонкоин" never resolves through "тон".
    best: Optional[tuple[int, str]] = None
    for stem, code in _PREFIXES:
        if cleaned.startswith(stem) and (best is None or len(stem) > best[0]):
            best = (len(stem), code)
    return best[1] if best else None


def parse_query(text: str, default_dst: str = "USD") -> Optional[Query]:
    """Parse free text into (amount, src, dst).

    Accepts `100 usd uah`, `100$ в грн`, `1 btc -> uah`, `5 ton` (defaults the
    target) and a bare `btc` (defaults the amount to 1). Returns None when no
    currency is recognisable — the caller then shows the usage hint.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return None
    raw = raw.replace("→", " ").replace("->", " ").replace("=", " ").replace("/", " ")
    for sign in _SYMBOLS:
        raw = raw.replace(sign, f" {sign} ")
    amount: Optional[float] = None
    codes: list[str] = []
    for token in raw.split():
        if token in _STOP_WORDS:
            continue
        if amount is None and _NUMBER.match(token):
            amount = float(token.replace(",", "."))
            continue
        code = code_of(token)
        if code and code not in codes:
            codes.append(code)
        if len(codes) == 2:
            break
    if not codes:
        return None
    src = codes[0]
    if len(codes) > 1:
        dst = codes[1]
    else:
        # A lone USD would convert to itself — show the hryvnia instead.
        dst = "UAH" if src == default_dst else default_dst
    return Query(1.0 if amount is None else amount, src, dst)


def _quote(snapshot: dict[str, Any], asset: str, currency: str) -> Optional[float]:
    try:
        value = float((snapshot.get(asset) or {}).get(currency))
    except (TypeError, ValueError, AttributeError):
        return None
    return value if value > 0 else None


def fiat_block(prices: dict[str, Any]) -> dict[str, float]:
    """Fiat units per 1 USD, from a `simple/price` payload of the bridge coins.

    Stored on the snapshot as ``_fiat`` so a row stays small: one float per
    currency instead of a full quote (and a 24 h change) on all eight coins.
    """
    block: dict[str, float] = {}
    for code in FIAT_CODES:
        ratios = []
        for asset in BRIDGE_IDS:
            usd = _quote(prices, asset, "usd")
            local = _quote(prices, asset, code.lower())
            if usd and local:
                ratios.append(local / usd)
        if ratios:
            block[code] = median(ratios)
    return block


def fiat_per_usd(snapshot: dict[str, Any], code: str) -> Optional[float]:
    """How many units of `code` one USD buys."""
    if code == "USD":
        return 1.0
    stored = (snapshot.get("_fiat") or {}).get(code)
    try:
        value = float(stored)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    # Pre-`_fiat` snapshot (or a currency the fetch missed): rebuild the cross
    # rate from whatever coins carry both quotes. Always works for USD/UAH.
    ratios = []
    for asset, _emoji in CRYPTO.values():
        usd = _quote(snapshot, asset, "usd")
        local = _quote(snapshot, asset, code.lower())
        if usd and local:
            ratios.append(local / usd)
    return median(ratios) if ratios else None


def usd_value(snapshot: dict[str, Any], code: str) -> Optional[float]:
    """Price of one unit of `code` in USD."""
    if code in CRYPTO:
        return _quote(snapshot, CRYPTO[code][0], "usd")
    if code in FIAT:
        per_usd = fiat_per_usd(snapshot, code)
        return 1 / per_usd if per_usd else None
    return None


def convert(snapshot: dict[str, Any], amount: float, src: str, dst: str) -> Optional[float]:
    """`amount` of `src` expressed in `dst`; None when either rate is missing."""
    if src == dst:
        return amount
    src_usd, dst_usd = usd_value(snapshot, src), usd_value(snapshot, dst)
    if not src_usd or not dst_usd:
        return None
    return amount * src_usd / dst_usd


def sign_of(code: str) -> str:
    """Display prefix: a fiat sign, a coin emoji, or nothing."""
    if code in FIAT:
        return FIAT[code][0]
    return CRYPTO[code][1] if code in CRYPTO else ""


def _digits(value: float, code: str) -> int:
    """Decimals that keep a value readable — cents for fiat, more for satoshis."""
    magnitude = abs(value)
    if code in FIAT:
        if magnitude >= 1:
            return 2
        return 4 if magnitude >= 0.01 else 6
    if magnitude >= 1000:
        return 2
    if magnitude >= 1:
        return 4
    return 6 if magnitude >= 0.001 else 8


def fmt_amount(value: float, code: str) -> str:
    """`1 234.56` — grouped with spaces, precision chosen by magnitude.

    Fiat keeps its cents; a coin drops trailing zeros, so a round amount reads
    as `1 BTC` instead of `1.0000 BTC`.
    """
    text = f"{value:,.{_digits(value, code)}f}".replace(",", " ")
    if code not in FIAT and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def fmt_money(value: float, code: str) -> str:
    """`$1 234.56` / `0.0421 BTC` — the amount with its sign or ticker."""
    body = fmt_amount(value, code)
    if code in FIAT:
        return f"{FIAT[code][0]}{body}"
    return f"{body} {code}"


def snapshot_time(snapshot: dict[str, Any]) -> str:
    raw = str((snapshot or {}).get("fetched_at_iso") or "")
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m %H:%M")
    except ValueError:
        return raw or "—"


def supported_text() -> str:
    """Two lines listing every code the converter accepts."""
    coins = " ".join(f"{emoji}{code}" for code, (_id, emoji) in CRYPTO.items())
    money = " ".join(f"{FIAT[code][1]}{code}" for code in FIAT_CODES)
    return f"🪙 {coins}\n💱 {money}"


USAGE_HINT = (
    "Пришлите запрос вида:\n"
    "• `100 usd uah`\n"
    "• `1 btc в грн`\n"
    "• `2.5 ton eur`\n"
    "• `50 злотых рубли`"
)


def render_converter_home(snapshot: Optional[dict[str, Any]]) -> str:
    """Converter landing card: how to ask + a few live reference rates."""
    lines = [header("💱", "Конвертер", "валюты и крипта")]
    if not snapshot:
        lines.append(empty("Курсы пока не загружены — попробуйте через минуту."))
        lines.append(DIV)
        lines.append(USAGE_HINT)
        return "\n".join(lines)

    reference = [("USD", "UAH"), ("EUR", "UAH"), ("BTC", "USD"), ("TON", "USD")]
    for src, dst in reference:
        value = convert(snapshot, 1.0, src, dst)
        if value is None:
            continue
        lines.append(f"{sign_of(src)} 1 {src} = `{fmt_amount(value, dst)}` {dst}")
    lines += [DIV, USAGE_HINT, "", kv("🕐", "Курс на", snapshot_time(snapshot))]
    return "\n".join(lines)


def render_conversion(snapshot: Optional[dict[str, Any]], query: Query) -> str:
    """Result card for one conversion, with both directions of the rate."""
    amount, src, dst = query
    if not snapshot:
        return "\n".join([
            header("💱", "Конвертер"),
            empty("Курсы пока не загружены — попробуйте через минуту."),
        ])
    value = convert(snapshot, amount, src, dst)
    if value is None:
        return "\n".join([
            header("💱", "Конвертер"),
            f"❌ Нет курса для пары **{src} → {dst}**.",
            DIV,
            supported_text(),
        ])
    rate = convert(snapshot, 1.0, src, dst) or 0.0
    back = convert(snapshot, 1.0, dst, src) or 0.0
    lines = [
        header("💱", "Конвертер", f"{src} → {dst}"),
        f"{sign_of(src)} `{fmt_amount(amount, src)} {src}`",
        f"➡️ {sign_of(dst)} **`{fmt_amount(value, dst)} {dst}`**",
        DIV,
        f"📈 1 {src} = `{fmt_amount(rate, dst)}` {dst}",
        f"📉 1 {dst} = `{fmt_amount(back, src)}` {src}",
    ]
    # A cross rate hides its own size — anchor it to dollars.
    if "USD" not in (src, dst):
        in_usd = convert(snapshot, amount, src, "USD")
        if in_usd is not None:
            lines.append(f"💵 ≈ `{fmt_money(in_usd, 'USD')}`")
    lines.append(kv("🕐", "Курс на", snapshot_time(snapshot)))
    return "\n".join(lines)
