"""One win, one row: glue copies of the same winners post together (pure).

A giveaway result is forwarded and copy-pasted: the channel post, its linked
discussion chat, and a string of private messages from people who share it.
Each copy mentions the same tracked account, so each became its own win — the
same prize sat on the debts board three or five times (25 such groups, 60 rows
by 13.09.2026), and claiming one left the rest waiting.

Copies are recognised by the *normalised text* (case and whitespace folded) plus
at least one shared tracked account, in *another chat*, within ``WINDOW``. The
same text twice in one chat is two wins: a channel reuses one template for every
fast giveaway, and the same account can win twice (three such pairs were glued
and one live debt hidden by 21.09.2026). The window measures detection time, not
the post date: winners are edited into a post published days earlier, often
without an edit stamp, and pasted around days later — 7 of 31 real copies by
21.09 were more than 72 h apart by post date, minutes apart by detection.

The primary is the best source — a channel post over a group over a private
copy, then the earliest — and every other copy points to it through
``pings.duplicate_of``; two pastes of one post into another chat are both its
copies, but a post in the primary's own chat never is. The debts
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
    if a.get("chat_id") == b.get("chat_id"):
        return False
    text_a, text_b = normalize_text(a.get("text")), normalize_text(b.get("text"))
    if len(text_a) < MIN_TEXT_LENGTH or text_a != text_b:
        return False
    if not (_mentions(a) & _mentions(b)):
        return False
    return abs(_ts(a) - _ts(b)) <= WINDOW


def group_duplicates(rows: Iterable[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    """``[(primary_id, [duplicate ids])]`` over win rows; rows without copies are omitted.

    Only rows with the same normalised text can be copies, so rows are grouped by
    it first and compared within their text only. Comparing every win with every
    other (normalising both texts per pair) held the event loop for 2.9 s at every
    start (24.09). Groups come out in the order of their earliest row, as before.
    """
    buckets: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(sorted(rows, key=_ts)):
        text = normalize_text(row.get("text"))
        if len(text) >= MIN_TEXT_LENGTH:
            buckets.setdefault(text, []).append((index, row))
    found: list[tuple[int, list[dict[str, Any]]]] = []
    for members in buckets.values():
        groups: list[tuple[int, list[dict[str, Any]]]] = []
        for index, row in members:
            for _first, group in groups:
                # A copy elsewhere matches every post of its template; it must not
                # pull a second post of the primary's chat into the group. Two
                # pastes of one post into another chat are both copies.
                primary = min(group, key=source_rank)
                if row.get("chat_id") != primary.get("chat_id") and any(same_win(row, member) for member in group):
                    group.append(row)
                    break
            else:
                groups.append((index, [row]))
        found.extend(groups)
    result: list[tuple[int, list[int]]] = []
    for _first, group in sorted(found, key=lambda item: item[0]):
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
