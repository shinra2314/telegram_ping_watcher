from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

try:
    from telethon import types
except ModuleNotFoundError:  # Keeps parser/scoring tests runnable without Telegram deps.
    types = None


GIVEAWAY_WORDS = (
    "розыгрыш",
    "конкурс",
    "giveaway",
    "приз",
    "итоги",
    "winner",
    "skin",
    "скин",
)
GIVEAWAY_OUTCOME_WORDS = (
    "результаты розыгрыша",
    "результаты конкурса",
    "итоги розыгрыша",
    "итоги конкурса",
    "розыгрыш закончен",
    "конкурс закончен",
    "congratulations",
)
WINNER_LIST_RE = re.compile(r"(?:^|\n)\s*(?:🏆\s*)?(?:победител[ьи]|winners?)\s*[:：-]", re.IGNORECASE)
GENERIC_WINNER_COUNT_RE = re.compile(
    r"\b(?:\d+|один|одна|два|две|три|четыре|пять|одного)\s+победител[ьяей]*\b|"
    r"\bпобедител[ьяей]*\s+(?:будет|будут|выбер|получит|получат)\b",
    re.IGNORECASE,
)
NEGATIVE_OUTCOME_RE = re.compile(
    r"\b(?:не\s+выполнил|не\s+выполнила|не\s+выполнили|не\s+соблюд|не\s+отписал|"
    r"reroll|re-roll|рерол|перевыбор)\b",
    re.IGNORECASE,
)
INTERNAL_NOTIFICATION_RE = re.compile(
    r"^\s*(?:новое\s+упоминание|найден\s+розыгрыш|похоже\s+на\s+победу)\b",
    re.IGNORECASE,
)


def is_internal_notification_text(text: str) -> bool:
    value = text or ""
    return bool(INTERNAL_NOTIFICATION_RE.search(value)) and "чат:" in value.lower()
SUBSCRIBE_WORDS = ("подпис", "subscribe", "join", "вступ")
CAPTCHA_WORDS = ("captcha", "капч", "verify", "провер", "бот не допускается")
COMMENT_WORDS = ("коммент", "comment", "чат", "chat")
EXTERNAL_WORDS = ("youtube.com", "youtu.be", "twitch.tv", "discord.gg", "x.com/", "twitter.com/")
CHANNEL_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?!c/|share/|\+)([A-Za-z0-9_]{5,32})", re.IGNORECASE)
USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{5,32})")
PRICE_RE = re.compile(r"(?:(\d+(?:[.,]\d+)?)\s*(?:\$|usd|usdt|ton|uah|грн|₴))|(?:(?:\$|usd|usdt|₴)\s*(\d+(?:[.,]\d+)?))", re.IGNORECASE)


def matches_strict_giveaway_rule(text: str, chat_type: str, keywords: Iterable[str]) -> bool:
    if chat_type != "channel":
        return False
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords if keyword)


def is_giveaway_outcome_text(text: str) -> bool:
    if is_internal_notification_text(text):
        return False
    lowered = (text or "").lower()
    return any(marker in lowered for marker in GIVEAWAY_OUTCOME_WORDS) or bool(WINNER_LIST_RE.search(text or ""))


def is_win_text(text: str, keywords: Iterable[str]) -> bool:
    if is_internal_notification_text(text):
        return False
    if is_giveaway_outcome_text(text):
        return True
    lowered = (text or "").lower()
    generic_winner_count = bool(GENERIC_WINNER_COUNT_RE.search(text or ""))
    for keyword in keywords:
        normalized = keyword.lower().strip()
        if not normalized:
            continue
        if generic_winner_count and normalized in {"победитель", "победители", "winner", "win"}:
            continue
        if normalized in {"win"}:
            if re.search(r"\bwin\b", lowered):
                return True
            continue
        if normalized in lowered:
            return True
    return False


def giveaway_outcome_resolution(text: str) -> str:
    if NEGATIVE_OUTCOME_RE.search(text or ""):
        return "missed"
    return "pending"


@dataclass
class RequiredChannel:
    username: str
    title: str = ""
    subscribers: Optional[int] = None
    giveaway_posts: Optional[int] = None
    accessible: bool = False
    last_active_at: str = ""
    reason: str = "required"

    def as_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "title": self.title,
            "subscribers": self.subscribers,
            "giveaway_posts": self.giveaway_posts,
            "accessible": self.accessible,
            "last_active_at": self.last_active_at,
            "reason": self.reason,
        }


@dataclass
class GiveawayAnalysis:
    score: int
    status: str
    reasons: list[str] = field(default_factory=list)
    required_channels: list[RequiredChannel] = field(default_factory=list)
    join_buttons: list[str] = field(default_factory=list)
    external_requirements: list[str] = field(default_factory=list)
    blocked_reason: str = ""
    estimated_value: Optional[float] = None

    def as_candidate(self, ping_id: int) -> dict[str, Any]:
        return {
            "ping_id": ping_id,
            "status": self.status,
            "score": self.score,
            "reasons": self.reasons,
            "required_channels": [channel.as_dict() for channel in self.required_channels],
            "join_buttons": self.join_buttons,
            "external_requirements": self.external_requirements,
            "blocked_reason": self.blocked_reason,
            "estimated_value": self.estimated_value,
        }


def extract_join_buttons(message: Any, join_keywords: Iterable[str]) -> list[str]:
    markup = getattr(message, "reply_markup", None)
    if types is None or not isinstance(markup, types.ReplyInlineMarkup):
        return []
    keywords = [item.lower() for item in join_keywords if item]
    buttons: list[str] = []
    for row in markup.rows:
        for button in row.buttons:
            text = (getattr(button, "text", "") or "").strip()
            if text and any(keyword in text.lower() for keyword in keywords):
                buttons.append(text)
    return buttons


def extract_required_channel_usernames(text: str) -> list[str]:
    text = text or ""
    candidates: list[str] = []
    for match in CHANNEL_LINK_RE.finditer(text):
        candidates.append(match.group(1))
    lines = text.splitlines() or [text]
    for line in lines:
        lowered = line.lower()
        if not any(word in lowered for word in SUBSCRIBE_WORDS):
            continue
        for match in USERNAME_RE.finditer(line):
            candidates.append(match.group(1))
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        normalized = item.strip().lstrip("@")
        lowered = normalized.lower()
        if lowered and lowered not in seen:
            seen.add(lowered)
            result.append(normalized)
    return result


def extract_external_requirements(text: str) -> list[str]:
    lowered = (text or "").lower()
    found = [item for item in EXTERNAL_WORDS if item in lowered]
    if any(word in lowered for word in COMMENT_WORDS):
        found.append("comment_or_chat")
    if any(word in lowered for word in CAPTCHA_WORDS):
        found.append("captcha_or_verification")
    return list(dict.fromkeys(found))


def extract_estimated_value(text: str) -> Optional[float]:
    values: list[float] = []
    for match in PRICE_RE.finditer(text or ""):
        raw = next((group for group in match.groups() if group), "")
        try:
            values.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    return max(values) if values else None


def score_analysis(
    required_channels: list[RequiredChannel],
    join_buttons: list[str],
    external_requirements: list[str],
    estimated_value: Optional[float],
) -> tuple[int, list[str], str, str]:
    score = 25
    reasons: list[str] = []
    blocked_reason = ""

    if join_buttons:
        score += 15
        reasons.append("Found a normal Telegram participation button.")
    else:
        score -= 10
        reasons.append("No safe participation button was detected.")

    if estimated_value is not None:
        value_bonus = min(25, int(estimated_value // 5))
        score += value_bonus
        reasons.append(f"Estimated prize value detected: {estimated_value:g}.")
    else:
        reasons.append("Prize value is unknown.")

    for channel in required_channels:
        if channel.subscribers is None:
            score -= 5
            reasons.append(f"@{channel.username}: subscriber count is unknown.")
        elif channel.subscribers < 2_000:
            score += 14
            reasons.append(f"@{channel.username}: small channel, higher odds signal.")
        elif channel.subscribers < 20_000:
            score += 6
            reasons.append(f"@{channel.username}: medium subscriber count.")
        else:
            score -= 8
            reasons.append(f"@{channel.username}: large subscriber count lowers odds.")

        if channel.giveaway_posts is None:
            reasons.append(f"@{channel.username}: giveaway history is unknown.")
        elif channel.giveaway_posts > 0:
            score += min(12, channel.giveaway_posts * 4)
            reasons.append(f"@{channel.username}: recent giveaway posts found.")
        else:
            score -= 4
            reasons.append(f"@{channel.username}: no recent giveaway posts found.")

    if external_requirements:
        score -= 35
        blocked_reason = "Manual-only requirement: " + ", ".join(external_requirements)
        reasons.append(blocked_reason)

    score = max(0, min(100, score))
    if blocked_reason:
        status = "manual_required"
    elif score >= 65:
        status = "recommended"
    else:
        status = "pending_review"
    return score, reasons, status, blocked_reason


async def inspect_required_channel(client: Any, username: str, recent_limit: int = 50) -> RequiredChannel:
    channel = RequiredChannel(username=username)
    try:
        entity = await client.get_entity(username)
        title = getattr(entity, "title", "") or username
        full = await client.get_entity(entity)
        channel.title = title
        channel.accessible = True
        channel.subscribers = getattr(full, "participants_count", None)
        giveaway_posts = 0
        last_active_at = ""
        async for message in client.iter_messages(entity, limit=recent_limit):
            text = (getattr(message, "raw_text", "") or "").lower()
            if not last_active_at and getattr(message, "date", None):
                last_active_at = message.date.replace(tzinfo=None).isoformat()
            if any(word in text for word in GIVEAWAY_WORDS):
                giveaway_posts += 1
        channel.giveaway_posts = giveaway_posts
        channel.last_active_at = last_active_at
    except Exception as exc:
        channel.reason = f"unknown: {exc}"
    return channel


async def analyze_giveaway(
    client: Any,
    ping_id: int,
    text: str,
    message: Any = None,
    join_keywords: Iterable[str] = (),
    recent_limit: int = 50,
) -> dict[str, Any]:
    required_usernames = extract_required_channel_usernames(text)
    required_channels = [
        await inspect_required_channel(client, username, recent_limit=recent_limit)
        for username in required_usernames
    ]
    join_buttons = extract_join_buttons(message, join_keywords) if message is not None else []
    external_requirements = extract_external_requirements(text)
    estimated_value = extract_estimated_value(text)
    score, reasons, status, blocked_reason = score_analysis(
        required_channels,
        join_buttons,
        external_requirements,
        estimated_value,
    )
    return GiveawayAnalysis(
        score=score,
        status=status,
        reasons=reasons,
        required_channels=required_channels,
        join_buttons=join_buttons,
        external_requirements=external_requirements,
        blocked_reason=blocked_reason,
        estimated_value=estimated_value,
    ).as_candidate(ping_id)


def inactive_channel_candidate(dialog: Any, inactive_days: int) -> Optional[dict[str, Any]]:
    entity = getattr(dialog, "entity", None)
    if types is None or not isinstance(entity, types.Channel):
        return None
    message = getattr(dialog, "message", None)
    last_date = getattr(message, "date", None)
    if not last_date:
        return None
    last_date = last_date.replace(tzinfo=None)
    if last_date > datetime.now() - timedelta(days=inactive_days):
        return None
    return {
        "chat_id": getattr(entity, "id", None),
        "title": getattr(entity, "title", "") or str(getattr(entity, "id", "")),
        "username": getattr(entity, "username", "") or "",
        "last_active_at": last_date.isoformat(),
        "inactive_days": (datetime.now() - last_date).days,
    }
