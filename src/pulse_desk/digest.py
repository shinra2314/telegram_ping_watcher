"""Daily digest rendering: ping roundup + crypto block with day-over-day deltas.

Pure formatting — the caller fetches pings and market snapshots and passes them
in, so every branch here unit-tests without a database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from .bot.cards import spark

DIV = "━━━━━━━━━━━━━━━"

# coingecko id, emoji, ticker, decimals shown for the USD price
DIGEST_ASSETS: list[tuple[str, str, str, int]] = [
    ("bitcoin", "🟠", "BTC", 0),
    ("ethereum", "🔷", "ETH", 1),
    ("the-open-network", "💎", "TON", 3),
    ("solana", "🟣", "SOL", 2),
    ("binancecoin", "🟡", "BNB", 1),
    ("notcoin", "🪙", "NOT", 6),
    ("dogs-2", "🐕", "DOGS", 8),
    ("tether", "💵", "USDT", 4),
]

# assets whose hryvnia rate is worth a line of its own
UAH_ASSETS: list[tuple[str, str, int]] = [("tether", "USDT", 2), ("the-open-network", "TON", 2)]

_SPARK_BUCKETS = 12
# a day whose whole range is below this is flat noise, not a trend
_FLAT_RANGE = 0.0025


def _fmt_price(value: float, digits: int) -> str:
    return f"{value:,.{digits}f}".replace(",", " ")


def _fmt_pct(pct: float) -> str:
    """Signed percentage with an arrow: ▲3.82% / ▼1.49% / ⏸0.00%."""
    arrow = "▲" if pct > 0.005 else ("▼" if pct < -0.005 else "⏸")
    return f"{arrow}{abs(pct):.2f}%"


def _fmt_delta(now: int, before: int) -> str:
    diff = now - before
    sign = "+" if diff > 0 else ("−" if diff < 0 else "±")
    return f"(вчера {before} · {sign}{abs(diff)})"


def _price(snapshot: dict[str, Any], asset: str, currency: str = "usd") -> Optional[float]:
    value = (snapshot.get(asset) or {}).get(currency)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _change_pct(latest: dict[str, Any], oldest: dict[str, Any], asset: str, currency: str) -> Optional[float]:
    """Day move from our own snapshots; CoinGecko's 24 h field when history is short."""
    current = _price(latest, asset, currency)
    if current is None:
        return None
    base = _price(oldest, asset, currency) if oldest is not latest else None
    if base:
        return (current - base) / base * 100
    try:
        return float((latest.get(asset) or {}).get(f"{currency}_24h_change") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _series(snapshots: list[dict[str, Any]], asset: str) -> list[float]:
    """Prices oldest-first, bucketed down to a sparkline-sized sample."""
    prices = [p for p in (_price(s, asset) for s in reversed(snapshots)) if p is not None]
    if len(prices) <= _SPARK_BUCKETS:
        return prices
    step = len(prices) / _SPARK_BUCKETS
    buckets: list[float] = []
    for i in range(_SPARK_BUCKETS):
        start = int(i * step)
        chunk = prices[start:max(int((i + 1) * step), start + 1)]
        buckets.append(sum(chunk) / len(chunk))
    return buckets


def _sparkline(prices: list[float]) -> str:
    """Min-max normalised sparkline — raw prices would render as a flat wall.

    Min-max also magnifies noise, so a day that never moved (a pegged coin)
    gets no chart at all instead of a fake mountain range.
    """
    if len(prices) < 3:
        return ""
    floor, peak = min(prices), max(prices)
    if not peak or (peak - floor) / peak < _FLAT_RANGE:
        return ""
    return spark([p - floor for p in prices])


def ping_stats(pings: list[dict[str, Any]]) -> dict[str, int]:
    wins = sum(1 for p in pings if p.get("is_win"))
    return {"total": len(pings), "wins": wins, "mentions": len(pings) - wins}


def market_block(snapshots: list[dict[str, Any]]) -> list[str]:
    """Per-asset price, day delta and 24 h sparkline. `snapshots` newest first."""
    snapshots = [s for s in (snapshots or []) if isinstance(s, dict)]
    lines = [DIV, "💹 **Крипта за сутки**"]
    if not snapshots:
        lines.append("📉 __Данные о курсах пока недоступны.__")
        return lines

    latest, oldest = snapshots[0], snapshots[-1]
    from_history = len(snapshots) > 1
    moves: list[tuple[str, float]] = []

    for asset, emoji, ticker, digits in DIGEST_ASSETS:
        current = _price(latest, asset)
        pct = _change_pct(latest, oldest, asset, "usd")
        if current is None or pct is None:
            continue
        moves.append((ticker, pct))
        chart = _sparkline(_series(snapshots, asset))
        row = f"{emoji} **{ticker}** `${_fmt_price(current, digits)}`  {_fmt_pct(pct)}"
        lines.append(f"{row}  {chart}" if chart else row)

    if not moves:
        lines.append("📉 __Данные о курсах пока недоступны.__")
        return lines

    ranked = sorted(moves, key=lambda item: item[1], reverse=True)
    if len(ranked) > 1:
        top, worst = ranked[0], ranked[-1]
        lines.append(
            f"📈 Лидер: **{top[0]}** {_fmt_pct(top[1])} · 📉 Аутсайдер: **{worst[0]}** {_fmt_pct(worst[1])}"
        )
    # Tether is pegged — averaging it in would only damp the reading.
    volatile = [pct for ticker, pct in moves if ticker != "USDT"]
    if volatile:
        lines.append(f"📊 Средняя динамика рынка: {_fmt_pct(sum(volatile) / len(volatile))}")

    uah_parts: list[str] = []
    for asset, ticker, digits in UAH_ASSETS:
        current = _price(latest, asset, "uah")
        pct = _change_pct(latest, oldest, asset, "uah")
        if current is None or pct is None:
            continue
        uah_parts.append(f"{ticker} `₴{_fmt_price(current, digits)}` {_fmt_pct(pct)}")
    if uah_parts:
        lines.append("🇺🇦 Гривна: " + " · ".join(uah_parts))

    if from_history:
        lines.append(f"__Сравнение со снимком {_fmt_snapshot_time(oldest)}__")
    else:
        lines.append("__Динамика — по данным CoinGecko за 24 ч (своей истории мало)__")
    return lines


def _fmt_snapshot_time(snapshot: dict[str, Any]) -> str:
    raw = str(snapshot.get("fetched_at_iso") or "")
    try:
        return datetime.fromisoformat(raw).strftime("%d.%m %H:%M")
    except ValueError:
        return raw or "—"


def format_digest_caption(
    pings: list[dict[str, Any]],
    *,
    period_label: str = "за последние 24 ч",
    prev_pings: Optional[list[dict[str, Any]]] = None,
    max_wins: int = 6,
) -> str:
    """Caption for the two-card digest: the numbers live on the images, so this
    carries only what a picture cannot — the links to today's wins.

    Telegram caps a media caption at 1024 chars; the win list is trimmed to fit.
    """
    stats = ping_stats(pings)
    prev = ping_stats(prev_pings) if prev_pings is not None else None
    head = f"📊 **Pulse Desk Digest** — {period_label}"
    if prev is not None:
        head += f"\nВсего: {stats['total']} {_fmt_delta(stats['total'], prev['total'])}"
    else:
        head += f"\nВсего: {stats['total']} · 🏆 {stats['wins']} · 📌 {stats['mentions']}"

    wins = [p for p in pings if p.get("is_win")]
    if not wins:
        return head
    lines = [head, "", "🏆 **Победы:**"]
    for p in wins[:max_wins]:
        chat = str(p.get("chat") or "?")
        link = p.get("link") or ""
        entry = f"• [{chat}]({link})" if link else f"• {chat}"
        if sum(len(x) + 1 for x in lines) + len(entry) > 950:
            lines.append(f"…ещё {len(wins) - (len(lines) - 3)}")
            break
        lines.append(entry)
    if len(wins) > max_wins:
        lines.append(f"…ещё {len(wins) - max_wins}")
    return "\n".join(lines)


def format_digest(
    pings: list[dict[str, Any]],
    *,
    period_label: str = "за последние 24 ч",
    prev_pings: Optional[list[dict[str, Any]]] = None,
    market: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Digest text. `prev_pings` is the previous window, `market` the 24 h snapshots."""
    stats = ping_stats(pings)
    prev = ping_stats(prev_pings) if prev_pings is not None else None

    lines: list[str] = [f"📊 **Pulse Desk Digest** — {period_label}"]
    if prev is None:
        lines.append(f"Всего: {stats['total']} | 🏆 Побед: {stats['wins']} | 📌 Упоминаний: {stats['mentions']}")
    else:
        lines.append(f"Всего: {stats['total']} {_fmt_delta(stats['total'], prev['total'])}")
        lines.append(
            f"🏆 Побед: {stats['wins']} {_fmt_delta(stats['wins'], prev['wins'])} | "
            f"📌 Упоминаний: {stats['mentions']} {_fmt_delta(stats['mentions'], prev['mentions'])}"
        )
    lines.append("")

    if not pings:
        lines.append(f"📭 Нет новых пингов {period_label}.")
    else:
        wins = [p for p in pings if p.get("is_win")]
        mentions = [p for p in pings if not p.get("is_win")]
        if wins:
            lines.append("🏆 **Победы:**")
            for p in wins[:10]:
                chat = p.get("chat") or "?"
                link = p.get("link") or ""
                text = (p.get("text") or "")[:80].replace("\n", " ")
                lines.append(f"• [{chat}]({link}) — {text}")
            lines.append("")
        if mentions:
            lines.append("📌 **Упоминания:**")
            for p in mentions[:10]:
                chat = p.get("chat") or "?"
                link = p.get("link") or ""
                lines.append(f"• [{chat}]({link})")

    if market is not None:
        lines.append("")
        lines += market_block(market)

    lines.append(f"\n__Сгенерировано {datetime.now().strftime('%d.%m.%Y %H:%M')}__")
    return "\n".join(lines)
