from __future__ import annotations

PING_STATUSES = {"new", "read", "important", "ignored", "resolved"}

ACTION_STATUSES = {
    "new",
    "to_check",
    "waiting_result",
    "claim_prize",
    "claimed",
    "scam",
    "missed",
    "closed",
}

GIVEAWAY_STATUSES = {
    "",
    "pending",
    "claimed",
    "missed",
    "missed_unsubscribe",
    "missed_reply",
    "scam",
    "closed",
}

FINAL_GIVEAWAY_STATUSES = {
    "claimed",
    "missed",
    "missed_unsubscribe",
    "missed_reply",
    "scam",
    "closed",
}

GIVEAWAY_TO_ACTION_STATUS = {
    "pending": "waiting_result",
    "claimed": "claimed",
    "missed": "missed",
    "missed_unsubscribe": "missed",
    "missed_reply": "missed",
    "scam": "scam",
    "closed": "closed",
}

