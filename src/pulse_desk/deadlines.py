from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable, Optional


DEADLINE_MARKERS = (
    "до",
    "дедлайн",
    "дедлаин",
    "итоги",
    "підсумки",
    "результаты",
    "результати",
    "deadline",
    "ends",
    "until",
)

MONTHS = {
    "января": 1,
    "январь": 1,
    "січня": 1,
    "january": 1,
    "jan": 1,
    "февраля": 2,
    "февраль": 2,
    "лютого": 2,
    "february": 2,
    "feb": 2,
    "марта": 3,
    "март": 3,
    "березня": 3,
    "march": 3,
    "mar": 3,
    "апреля": 4,
    "апрель": 4,
    "квітня": 4,
    "april": 4,
    "apr": 4,
    "мая": 5,
    "май": 5,
    "травня": 5,
    "may": 5,
    "июня": 6,
    "июнь": 6,
    "червня": 6,
    "june": 6,
    "jun": 6,
    "июля": 7,
    "июль": 7,
    "липня": 7,
    "july": 7,
    "jul": 7,
    "августа": 8,
    "август": 8,
    "серпня": 8,
    "august": 8,
    "aug": 8,
    "сентября": 9,
    "сентябрь": 9,
    "вересня": 9,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "октября": 10,
    "октябрь": 10,
    "жовтня": 10,
    "october": 10,
    "oct": 10,
    "ноября": 11,
    "ноябрь": 11,
    "листопада": 11,
    "november": 11,
    "nov": 11,
    "декабря": 12,
    "декабрь": 12,
    "грудня": 12,
    "december": 12,
    "dec": 12,
}

NUMERIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>[0-3]?\d)[./-](?P<month>[01]?\d)(?:[./-](?P<year>20\d{2}|\d{2}))?(?!\d)"
)
ISO_DATE_RE = re.compile(r"(?<!\d)(?P<year>20\d{2})-(?P<month>[01]?\d)-(?P<day>[0-3]?\d)(?!\d)")
TEXT_DATE_RE = re.compile(
    r"(?<!\d)(?P<day>[0-3]?\d)\s+(?P<month>"
    + "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
    + r")(?:\s+(?P<year>20\d{2}|\d{2}))?",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"(?<!\d)(?P<hour>[0-2]?\d)[:.](?P<minute>[0-5]\d)(?!\d)")
RELATIVE_RE = re.compile(
    r"\b(?:через|за|in)\s+(?P<amount>\d{1,4}|полтора|полторы|час|сутки)\s*"
    r"(?P<unit>минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч|д(?:ень|ня|ней)?|сут(?:ки|ок)?|"
    r"minutes?|mins?|hours?|hrs?|days?)\b",
    re.IGNORECASE,
)
RELATIVE_WORD_RE = re.compile(
    r"\b(?:через|за|in)\s+(?P<amount>полчаса|пол\s+часа|час|день|сутки|недел[юя]|тиждень|week)\b",
    re.IGNORECASE,
)
CLAIM_WINDOW_RE = re.compile(
    r"\b(?:в\s+течени[еи]|у\s+вас\s+есть|есть|на\s+отписк[уы]|на\s+связь|within)\s+"
    r"(?P<amount>\d{1,4}|полтора|полторы|час|сутки|день)\s*"
    r"(?P<unit>минут(?:у|ы)?|мин|м|час(?:а|ов)?|ч|д(?:ень|ня|ней)?|сут(?:ки|ок)?|"
    r"minutes?|mins?|hours?|hrs?|days?)?\b",
    re.IGNORECASE,
)
CLAIM_CONTEXT_RE = re.compile(
    r"\b(?:отпис|напис|получить\s+приз|забрать\s+приз|получения\s+приза|"
    r"получени[ея]|админ|связь|claim|prize)\b",
    re.IGNORECASE,
)
WINNER_LIST_RE = re.compile(r"(?:^|\n)\s*(?:🏆\s*)?(?:победител[ьи]|winners?)\s*[:：-]", re.IGNORECASE)
DAY_WORD_RE = re.compile(
    r"\b(?P<word>сегодня|сьогодні|today|завтра|tomorrow|послезавтра|післязавтра)\b",
    re.IGNORECASE,
)
END_OF_DAY_RE = re.compile(r"\b(?:до\s+)?конц[ауы]\s+дня\b|\bend\s+of\s+day\b", re.IGNORECASE)


@dataclass(frozen=True)
class DeadlineMatch:
    deadline_at: datetime
    source_text: str
    matched_text: str
    has_time: bool


def _line_candidates(text: str) -> list[str]:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "") if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]
    marked = [
        line
        for line in lines
        if any(marker in line.lower() for marker in DEADLINE_MARKERS)
        or CLAIM_WINDOW_RE.search(line)
        or RELATIVE_RE.search(line)
        or RELATIVE_WORD_RE.search(line)
    ]
    return marked or lines


def _normalize_year(raw_year: Optional[str], month: int, day: int, now: datetime) -> int:
    if raw_year:
        year = int(raw_year)
        if year < 100:
            year += 2000
        return year
    year = now.year
    try:
        candidate = datetime.combine(datetime(year, month, day).date(), time(23, 59))
    except ValueError:
        return year
    if candidate < now:
        year += 1
    return year


def _extract_time(fragment: str, start: int, end: int) -> tuple[int, int, bool]:
    windows = (
        fragment[end : min(len(fragment), end + 24)],
        fragment[max(0, start - 18) : start],
    )
    match = next((found for window in windows for found in [TIME_RE.search(window)] if found), None)
    if not match:
        return 23, 59, False
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23:
        return 23, 59, False
    return hour, minute, True


def _has_calendar_date(fragment: str) -> bool:
    return bool(
        ISO_DATE_RE.search(fragment)
        or NUMERIC_DATE_RE.search(fragment)
        or TEXT_DATE_RE.search(fragment)
        or DAY_WORD_RE.search(fragment)
        or END_OF_DAY_RE.search(fragment)
    )


def _build_match(
    fragment: str,
    match: re.Match[str],
    year: Optional[str],
    month: int,
    day: int,
    now: datetime,
) -> Optional[DeadlineMatch]:
    try:
        resolved_year = _normalize_year(year, month, day, now)
        hour, minute, has_time = _extract_time(fragment, match.start(), match.end())
        deadline_at = datetime(resolved_year, month, day, hour, minute)
    except ValueError:
        return None
    matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
    return DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=has_time)


def _matches_for_fragment(fragment: str, now: datetime) -> Iterable[DeadlineMatch]:
    for match in CLAIM_WINDOW_RE.finditer(fragment):
        amount_raw = match.group("amount").lower()
        unit = (match.group("unit") or "").lower()
        if amount_raw in {"полтора", "полторы"}:
            amount = 1.5
        elif amount_raw == "час":
            amount = 1
            unit = "час"
        elif amount_raw in {"сутки", "день"}:
            amount = 1
            unit = "день"
        else:
            amount = float(amount_raw)
        if unit.startswith(("мин", "м", "minute", "min")):
            delta = timedelta(minutes=amount)
        elif unit.startswith(("д", "сут", "day")):
            delta = timedelta(days=amount)
        else:
            delta = timedelta(hours=amount)
        deadline_at = (now + delta).replace(microsecond=0)
        matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
        yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=True)
    for match in RELATIVE_WORD_RE.finditer(fragment):
        amount_raw = re.sub(r"\s+", " ", match.group("amount").lower())
        if amount_raw in {"полчаса", "пол часа"}:
            delta = timedelta(minutes=30)
        elif amount_raw == "час":
            delta = timedelta(hours=1)
        elif amount_raw in {"день", "сутки"}:
            delta = timedelta(days=1)
        else:
            delta = timedelta(days=7)
        deadline_at = (now + delta).replace(microsecond=0)
        matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
        yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=True)
    for match in RELATIVE_RE.finditer(fragment):
        amount_raw = match.group("amount").lower()
        unit = match.group("unit").lower()
        if amount_raw in {"полтора", "полторы"}:
            amount = 1.5
        elif amount_raw == "час":
            amount = 1
            unit = "час"
        elif amount_raw == "сутки":
            amount = 1
            unit = "день"
        else:
            amount = float(amount_raw)
        if unit.startswith(("мин", "м", "minute", "min")):
            delta = timedelta(minutes=amount)
        elif unit.startswith(("час", "ч", "hour", "hr")):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        deadline_at = (now + delta).replace(microsecond=0)
        matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
        yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=True)
    for match in END_OF_DAY_RE.finditer(fragment):
        deadline_at = datetime.combine(now.date(), time(23, 59))
        if deadline_at < now:
            deadline_at += timedelta(days=1)
        matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
        yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=False)
    for match in DAY_WORD_RE.finditer(fragment):
        word = match.group("word").lower()
        days = 0
        if word in {"завтра", "tomorrow"}:
            days = 1
        elif word in {"послезавтра", "післязавтра"}:
            days = 2
        hour, minute, has_time = _extract_time(fragment, match.start(), match.end())
        deadline_at = datetime.combine((now + timedelta(days=days)).date(), time(hour, minute))
        if days == 0 and deadline_at < now:
            deadline_at += timedelta(days=1)
        matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
        yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=has_time)
    for match in ISO_DATE_RE.finditer(fragment):
        parsed = _build_match(
            fragment,
            match,
            match.group("year"),
            int(match.group("month")),
            int(match.group("day")),
            now,
        )
        if parsed:
            yield parsed
    if any(marker in fragment.lower() for marker in DEADLINE_MARKERS) and not _has_calendar_date(fragment):
        for match in TIME_RE.finditer(fragment):
            hour = int(match.group("hour"))
            minute = int(match.group("minute"))
            if hour > 23:
                continue
            deadline_at = datetime.combine(now.date(), time(hour, minute))
            if deadline_at < now:
                deadline_at += timedelta(days=1)
            matched_text = fragment[max(0, match.start() - 24) : min(len(fragment), match.end() + 32)].strip()
            yield DeadlineMatch(deadline_at=deadline_at, source_text=fragment.strip(), matched_text=matched_text, has_time=True)
    for match in NUMERIC_DATE_RE.finditer(fragment):
        parsed = _build_match(
            fragment,
            match,
            match.group("year"),
            int(match.group("month")),
            int(match.group("day")),
            now,
        )
        if parsed:
            yield parsed
    for match in TEXT_DATE_RE.finditer(fragment):
        month_name = match.group("month").lower()
        parsed = _build_match(
            fragment,
            match,
            match.group("year"),
            MONTHS[month_name],
            int(match.group("day")),
            now,
        )
        if parsed:
            yield parsed


def parse_deadline(text: str, now: Optional[datetime] = None) -> Optional[DeadlineMatch]:
    """Find the best deadline-looking date in Telegram text or channel description."""
    now = now or datetime.now()
    candidates: list[DeadlineMatch] = []
    for fragment in _line_candidates(text):
        candidates.extend(_matches_for_fragment(fragment, now))
    if not candidates:
        return None

    future = [item for item in candidates if item.deadline_at >= now]
    pool = future or candidates

    def score(item: DeadlineMatch) -> tuple[int, int, datetime]:
        text = item.source_text.lower()
        marker_score = 0 if any(marker in text for marker in DEADLINE_MARKERS) else 1
        time_score = 0 if item.has_time else 1
        return (marker_score, time_score, item.deadline_at)

    return sorted(pool, key=score)[0]


def parse_claim_deadline(text: str, now: Optional[datetime] = None) -> Optional[DeadlineMatch]:
    """Find a prize-claim deadline after a result post, not the original participation deadline."""
    now = now or datetime.now()
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "") if line.strip()]
    if not lines and text.strip():
        lines = [text.strip()]
    candidates: list[DeadlineMatch] = []
    for line in lines:
        lowered = line.lower()
        if "итоги" in lowered or "дедлайн" in lowered:
            continue
        if not (CLAIM_CONTEXT_RE.search(line) or CLAIM_WINDOW_RE.search(line)):
            continue
        candidates.extend(_matches_for_fragment(line, now))
    if not candidates:
        return None
    future = [item for item in candidates if item.deadline_at >= now]
    return sorted(future or candidates, key=lambda item: (0 if item.has_time else 1, item.deadline_at))[0]


def parse_participation_deadline(text: str, now: Optional[datetime] = None) -> Optional[DeadlineMatch]:
    """Find the deadline for joining a giveaway before winners are known."""
    if WINNER_LIST_RE.search(text or ""):
        before_winners = WINNER_LIST_RE.split(text or "", maxsplit=1)[0]
        return parse_deadline(before_winners, now=now)
    return parse_deadline(text, now=now)


def iso_or_none(value: Optional[datetime]) -> Optional[str]:
    return value.replace(microsecond=0).isoformat() if value else None
