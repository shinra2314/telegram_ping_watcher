"""Helper functions extracted from main.py for reuse across routers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from .giveaways import (
    giveaway_outcome_resolution,
    is_giveaway_outcome_text,
    is_win_text,
    matches_strict_giveaway_rule,
)

# Keywords used by apply_priority. Mirrors the constants in main.py.
HIGH_PRIORITY_KEYWORDS: list[str] = [
    "срочно", "важно", "winner", "победитель", "итоги",
    "приз", "claim", "airdrop", "ton",
]
IGNORE_KEYWORDS: list[str] = []


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def priority_label(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "normal"


def check_is_win(text: str, win_keywords: Iterable[str]) -> bool:
    return is_win_text(text, list(win_keywords))


def check_is_giveaway(text: str, chat_type: str, giveaway_keywords: Iterable[str]) -> bool:
    return matches_strict_giveaway_rule(text, chat_type, list(giveaway_keywords))


def apply_priority(
    record: dict[str, Any],
    high_priority_keywords: Optional[Iterable[str]] = None,
    ignore_keywords: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    text = (record.get("text") or "").lower()
    chat = (record.get("chat") or "").lower()
    hp = list(high_priority_keywords) if high_priority_keywords is not None else HIGH_PRIORITY_KEYWORDS
    ig = list(ignore_keywords) if ignore_keywords is not None else IGNORE_KEYWORDS
    score = 0
    if record.get("is_win"):
        score += 70
    if record.get("is_giveaway"):
        score += 45
    if record.get("chat_type") == "channel":
        score += 10
    if record.get("chat_type") == "private":
        score += 20
    score += min(len(record.get("mentions") or []) * 5, 20)
    if any(keyword.lower() in text for keyword in hp):
        score += 20
    if any(keyword.lower() in text or keyword.lower() in chat for keyword in ig):
        score -= 40
    record["priority_score"] = max(0, min(score, 100))
    record["priority_label"] = priority_label(record["priority_score"])
    return record


def apply_giveaway_state(record: dict[str, Any]) -> dict[str, Any]:
    text = record.get("text") or ""
    if (
        record.get("is_win")
        and is_giveaway_outcome_text(text)
        and giveaway_outcome_resolution(text) == "missed"
    ):
        record["giveaway_status"] = "missed"
    else:
        record["giveaway_status"] = "pending" if record.get("is_giveaway") else ""
    return record


def apply_action_state(record: dict[str, Any]) -> dict[str, Any]:
    if record.get("giveaway_status") == "missed":
        record["action_status"] = "missed"
    elif record.get("is_win"):
        record["action_status"] = "claim_prize"
    elif record.get("is_giveaway"):
        record["action_status"] = "waiting_result"
    elif record.get("priority_score", 0) >= 60:
        record["action_status"] = "to_check"
    else:
        record["action_status"] = "new"
    return record


def default_notification_settings() -> dict[str, Any]:
    return {
        "enabled": True,
        "usernames": [],
        "chats": [],
        "keywords": [],
        "rules": [],
        "cooldown_seconds": 120,
        "include_giveaways": True,
        "include_wins": True,
    }
