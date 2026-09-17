"""Weekly and monthly report: what was won, what was claimed, what is still owed.

The daily digest is a 24-hour roundup; nobody could see the week or the month
at a glance — how many wins, what share was actually claimed, which channels
paid, and what the salary workbook says was earned. This builds that report as
data (pure, unit-tested), a text form, and a drawn card
(``bot/render/screens.build_report_card``). The ``weekly-report`` job sends it
to the owner on Monday morning (the week before) and on the 1st (the month
before); ``/report`` draws it on demand.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

FINAL = {"claimed", "scam", "missed", "closed"}
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
             "сентябрь", "октябрь", "ноябрь", "декабрь"]

# Scheduled sends: weekly on Monday, monthly on the 1st, both at this hour, and
# still sent when the PC comes up within the catch-up window. The window counts
# from the period's own slot, not from today's: with a same-day window a PC that
# stayed off for the whole Monday (or the 1st) lost that report for good.
REPORT_HOUR = 10
REPORT_MINUTE = 15
CATCHUP_HOURS = 72
STATE_KEY = "report_state"


@dataclass
class ReportData:
    kind: str                      # "week" | "month"
    start: datetime
    end: datetime
    label: str
    wins: int = 0
    claimed: int = 0
    scam: int = 0
    open_wins: int = 0
    giveaways: int = 0
    mentions: int = 0
    prev_wins: int = 0
    daily_wins: list[float] = field(default_factory=list)
    top_channels: list[tuple[str, int]] = field(default_factory=list)
    unclaimed_usd: float = 0.0
    unclaimed_unpriced: int = 0
    book_won_usd: Optional[float] = None
    book_payout_usd: Optional[float] = None
    book_paid_usd: Optional[float] = None
    book_month: str = ""

    @property
    def claim_rate(self) -> Optional[float]:
        return self.claimed / self.wins if self.wins else None


def period(kind: str, now: datetime, *, previous: bool = False) -> tuple[datetime, datetime, str]:
    """``(start, end, label)``.

    Not previous: the last 7 days / the month so far. Previous: the last full
    ISO week (Mon–Sun) / the last full calendar month — what a scheduled report
    covers.
    """
    today = datetime(now.year, now.month, now.day)
    if kind == "month":
        first = today.replace(day=1)
        if previous:
            end = first
            start = (first - timedelta(days=1)).replace(day=1)
        else:
            start, end = first, now
        return start, end, f"{MONTHS_RU[start.month - 1]} {start.year}"
    if previous:
        end = today - timedelta(days=today.weekday())
        start = end - timedelta(days=7)
    else:
        start, end = today - timedelta(days=6), now
    last_day = (end - timedelta(seconds=1)) if previous else end
    return start, end, f"{start:%d.%m}–{last_day:%d.%m}"


def _ts(raw: Any) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def build_report(kind: str, start: datetime, end: datetime, label: str,
                 pings: list[dict[str, Any]], prev_pings: list[dict[str, Any]],
                 *, unclaimed: Optional[dict[str, float]] = None,
                 book: Any = None) -> ReportData:
    data = ReportData(kind=kind, start=start, end=end, label=label)
    days = max(1, (end.date() - start.date()).days + (0 if end.time() == datetime.min.time() else 1))
    per_day = [0.0] * days
    channels: Counter[str] = Counter()
    for row in pings:
        if row.get("duplicate_of"):
            continue
        if row.get("is_win"):
            data.wins += 1
            status = str(row.get("action_status") or "")
            if status == "claimed" or row.get("giveaway_status") == "claimed":
                data.claimed += 1
            elif status == "scam":
                data.scam += 1
            elif status not in FINAL:
                data.open_wins += 1
            channels[str(row.get("chat") or "?")] += 1
            when = _ts(row.get("detected_at"))
            if when:
                index = (when.date() - start.date()).days
                if 0 <= index < days:
                    per_day[index] += 1
        elif row.get("is_giveaway"):
            data.giveaways += 1
        else:
            data.mentions += 1
    data.prev_wins = sum(1 for r in prev_pings if r.get("is_win") and not r.get("duplicate_of"))
    data.daily_wins = per_day
    data.top_channels = channels.most_common(5)
    if unclaimed:
        data.unclaimed_usd = float(unclaimed.get("usd") or 0)
        data.unclaimed_unpriced = int(unclaimed.get("unpriced") or 0)
    if book is not None:
        _apply_book(data, book)
    return data


def _apply_book(data: ReportData, book: Any) -> None:
    """Numbers from the salary workbook: prizes logged in the period, the month's payout."""
    first, last = data.start.date(), (data.end - timedelta(seconds=1)).date()
    journal = [e for e in getattr(book, "journal", []) or [] if isinstance(getattr(e, "day", None), date)]
    data.book_won_usd = round(sum(float(e.value or 0) for e in journal if first <= e.day <= last), 2)
    month = f"{first:%Y-%m}"
    rows = [m for m in getattr(book, "months", []) or [] if m.month == month]
    if rows:
        data.book_month = month
        data.book_payout_usd = round(sum(float(m.total or 0) for m in rows), 2)
        data.book_paid_usd = round(sum(float(m.total or 0) for m in rows if m.paid_at), 2)


def short_channel(name: str, limit: int = 28) -> str:
    """«PRO CS2 | Розыгрыши 🔥 (@PRO_CS777)» → «PRO CS2 | Розыгрыши».

    The trailing «(@username)» only repeats the channel, and pictographs have no
    glyph in the card fonts (they drew as empty boxes).
    """
    import unicodedata

    base = name.split(" (@")[0] if " (@" in name else name
    clean = "".join(ch for ch in base
                    if unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co", "Mn")
                    and ord(ch) < 0x1F000 and not 0xFE00 <= ord(ch) <= 0xFE0F)
    clean = " ".join(clean.split())
    return (clean[:limit - 1] + "…") if len(clean) > limit else (clean or name[:limit])


def pct(value: Optional[float]) -> str:
    return f"{round(value * 100)}%" if value is not None else "—"


def trend(current: int, previous: int) -> str:
    if not previous:
        return "" if not current else "новое"
    change = (current - previous) / previous * 100
    return f"{change:+.0f}% к прошлому периоду"


def report_text(data: ReportData) -> str:
    """Caption / text fallback. Short on purpose — the card carries the rest."""
    title = "Неделя" if data.kind == "week" else "Месяц"
    lines = [
        f"📊 **{title}: {data.label}**",
        "━━━━━━━━━━━━━━━",
        f"🏆 Побед: `{data.wins}` {('· ' + trend(data.wins, data.prev_wins)) if trend(data.wins, data.prev_wins) else ''}",
        f"✅ Забрано: `{data.claimed}` · {pct(data.claim_rate)}   ⏳ Ждут: `{data.open_wins}`   🚫 Скам: `{data.scam}`",
        f"🎁 Розыгрышей: `{data.giveaways}`   📌 Упоминаний: `{data.mentions}`",
    ]
    if data.unclaimed_usd or data.unclaimed_unpriced:
        lines.append(f"💵 Незабрано сейчас: `${data.unclaimed_usd:,.2f}`".replace(",", " ")
                     + (f" + {data.unclaimed_unpriced} без оценки" if data.unclaimed_unpriced else ""))
    if data.book_won_usd is not None:
        lines.append(f"📒 По книге выиграно: `${data.book_won_usd:,.2f}`".replace(",", " "))
    if data.book_payout_usd is not None:
        lines.append(f"💸 Зарплаты за {data.book_month}: `${data.book_payout_usd:,.2f}` · выплачено "
                     f"`${(data.book_paid_usd or 0):,.2f}`".replace(",", " "))
    if data.top_channels:
        lines.append("🔝 " + " · ".join(f"{short_channel(name, 24)} ({count})"
                                         for name, count in data.top_channels[:3]))
    return "\n".join(lines)


def due_reports(now: datetime, state: dict[str, str]) -> list[str]:
    """Which scheduled reports should go out now: "week" on Monday, "month" on the 1st.

    ``state`` holds the last sent period per kind (ISO week key / month key), so
    each period is sent once, and a slot the PC slept through is still sent
    within ``CATCHUP_HOURS``.
    """
    due: list[str] = []
    today_slot = now.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)
    window = timedelta(hours=CATCHUP_HOURS)
    week_slot = today_slot - timedelta(days=now.weekday())
    if week_slot <= now <= week_slot + window and state.get("week") != week_key(now):
        due.append("week")
    month_slot = today_slot.replace(day=1)
    if month_slot <= now <= month_slot + window and state.get("month") != f"{now:%Y-%m}":
        due.append("month")
    return due


def week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"
