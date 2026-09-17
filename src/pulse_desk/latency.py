"""How late the app notices things (pure).

Two clocks matter and they are not the same:

* **Post → detection**: ``detected_at − date`` — how long a new post waited
  before a sweep or the realtime handler saw it.
* **Win → flag**: most wins arrive as an *edit* of an old giveaway post (the
  winners list is appended hours later). Measuring those from the post date
  would blame the app for the giveaway's own duration, so a win is measured from
  the edit (``edited_at``) to the moment the row became a win
  (``win_detected_at``). Rows stored before those columns existed have neither
  and are left out of this half rather than guessed.

Anything longer than ``MAX_MINUTES`` is a backfill of old history, not a delay,
and is excluded too.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Iterable, Optional

MAX_MINUTES = 3 * 24 * 60
SLOW_MINUTES = 60


def parse_ts(raw: Any) -> Optional[datetime]:
    """ISO timestamp → naive local time (``date`` carries an offset, ``detected_at`` does not)."""
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


def _minutes(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    minutes = (end - start).total_seconds() / 60
    if minutes < 0 or minutes > MAX_MINUTES:
        return None
    return minutes


def post_delay(row: dict[str, Any]) -> Optional[float]:
    return _minutes(parse_ts(row.get("date")), parse_ts(row.get("detected_at")))


def win_delay(row: dict[str, Any]) -> Optional[float]:
    if not row.get("is_win"):
        return None
    flagged = parse_ts(row.get("win_detected_at"))
    posted = parse_ts(row.get("date"))
    edited = parse_ts(row.get("edited_at"))
    start = edited if edited and posted and edited >= posted else posted
    if flagged is None:
        return None
    return _minutes(start, flagged)


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def summarize(values: Iterable[Optional[float]]) -> dict[str, float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"count": 0, "median": 0.0, "p90": 0.0, "slow": 0}
    return {
        "count": len(clean),
        "median": median(clean),
        "p90": _percentile(clean, 0.9),
        "slow": sum(1 for v in clean if v >= SLOW_MINUTES),
    }


def slowest_channels(rows: Iterable[dict[str, Any]], min_count: int = 2, top: int = 6) -> list[dict[str, Any]]:
    """Channels ranked by median post → detection delay."""
    by_chat: dict[str, list[float]] = {}
    for row in rows:
        delay = post_delay(row)
        if delay is None:
            continue
        by_chat.setdefault(str(row.get("chat") or "?"), []).append(delay)
    ranked = [
        {"chat": chat, "count": len(values), "median": median(values)}
        for chat, values in by_chat.items() if len(values) >= min_count
    ]
    ranked.sort(key=lambda item: item["median"], reverse=True)
    return ranked[:top]


def build_latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "posts": summarize(post_delay(r) for r in rows),
        "wins": summarize(win_delay(r) for r in rows),
        "channels": slowest_channels(rows),
    }


def fmt_minutes(value: float) -> str:
    if value < 1:
        return "<1 мин"
    if value < 90:
        return f"{value:.0f} мин"
    return f"{value / 60:.1f} ч"
