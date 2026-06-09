from __future__ import annotations

import hmac
import secrets
from typing import Optional


WEAK_TOKENS = {"change-this-owner-token", "change-this-friend-token", "admin", "viewer", "password", "token"}


def generate_access_key() -> str:
    """Return a fresh URL-safe access key for bot sharing."""
    return secrets.token_urlsafe(24)


def mask_secret(value: str, visible: int = 3) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def is_weak_token(value: str) -> bool:
    return bool(value) and (value in WEAK_TOKENS or len(value) < 16)


def token_matches(provided: Optional[str], expected: str) -> bool:
    return bool(provided and expected and hmac.compare_digest(provided, expected))
