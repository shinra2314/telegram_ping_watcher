"""One win, one row: glue copies of the same winners post together (pure).

A giveaway result is forwarded and copy-pasted: the channel post, its linked
discussion chat, and a string of private messages from people who share it.
Each copy mentions the same tracked account, so each became its own win — the
same prize sat on the debts board three or five times (25 such groups, 60 rows
by 13.09.2026), and claiming one left the rest waiting.

Copies are recognised by the *normalised text* (case and whitespace folded) plus
at least one shared tracked account, within ``WINDOW``. The primary is the best
source — a channel post over a group over a private copy, then the earliest —
and every other copy points to it through ``pings.duplicate_of``. The debts
board shows primaries only, and a status set on the primary is copied to its
duplicates.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

WINDOW = timedelta(hours=72)
# Shorter texts («Победитель: @x») are too generic to call two posts the same.
MIN_TEXT_LENGTH = 40
_CHAT_RANK = {"channel": 0, "group": 1}


def normalize_text(text: Any) -> str:
    folded = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    return folded[:400]


def _mentions(row: dict[str, Any]) -> set[str]:
    raw = row.get("mentions") or []
    if isinstance(raw, str):
        raw = [m.strip() for m in raw.strip("[]").replace('"', "").split(",")]
    return {str(m).strip().lstrip("@").lower() for m in raw if str(m).strip()}


def _ts(row: dict[str, Any]) -> datetime:
    """Naive local time: old rows carry a `+03:00` offset, newer ones do not."""
    try:
        value = datetime.fromisoformat(str(row.get("detected_at") or ""))
    except ValueError:
        return datetime.min
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def source_rank(row: dict[str, Any]) -> tuple[int, datetime, int]:
    """Lower is a better primary: channel, group, anything else; then earliest."""
    return (_CHAT_RANK.get(str(row.get("chat_type") or ""), 2), _ts(row), int(row.get("id") or 0))


def same_win(a: dict[str, Any], b: dict[str, Any]) -> bool:
    text_a, text_b = normalize_text(a.get("text")), normalize_text(b.get("text"))
    if len(text_a) < MIN_TEXT_LENGTH or text_a != text_b:
        return False
    if not (_mentions(a) & _mentions(b)):
        return False
    return abs(_ts(a) - _ts(b)) <= WINDOW


def group_duplicates(rows: Iterable[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    """``[(primary_id, [duplicate ids])]`` over win rows; rows without copies are omitted."""
    groups: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=_ts):
        for group in groups:
            if any(same_win(row, member) for member in group):
                group.append(row)
                break
        else:
            groups.append([row])
    result: list[tuple[int, list[int]]] = []
    for group in groups:
        if len(group) < 2:
            continue
        ordered = sorted(group, key=source_rank)
        result.append((int(ordered[0]["id"]), [int(r["id"]) for r in ordered[1:]]))
    return result


def find_primary(record: dict[str, Any], candidates: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The existing win a freshly stored one copies, if any (best source first)."""
    matches = [row for row in candidates if int(row.get("id") or 0) != int(record.get("id") or 0)
               and same_win(record, row)]
    return min(matches, key=source_rank) if matches else None
