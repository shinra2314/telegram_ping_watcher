"""Telegram Mini App ``initData`` validation.

Pure: hand in the raw ``initData`` string and the bot token, get the caller's
Telegram identity back. No I/O and no global state, so it unit-tests without a
running bot or a database.

The algorithm is Telegram's own: drop ``hash`` from the query string, join the
remaining ``key=value`` pairs with newlines in key order, and HMAC-SHA256 that
under a secret which is itself ``HMAC_SHA256(b"WebAppData", bot_token)``.
Values are compared after percent-decoding, which is what ``parse_qsl`` gives —
signing the still-encoded form would fail against every real client.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import NamedTuple, Optional
from urllib.parse import parse_qsl

# Telegram recommends rejecting old initData. A day is long enough for a panel
# left open on a phone and short enough that a leaked string goes stale.
MAX_AGE_SECONDS = 24 * 60 * 60


class InitDataError(Exception):
    """initData was missing, malformed, forged or stale."""


class WebAppUser(NamedTuple):
    tg_id: int
    first_name: str
    username: str
    auth_date: int


def data_check_string(pairs: list[tuple[str, str]]) -> str:
    """Telegram's canonical form: ``k=v`` per line, key order, no ``hash``."""
    return "\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign(init_data: str, bot_token: str) -> str:
    """The hash Telegram would produce for `init_data`. Used by verify and tests."""
    pairs = parse_qsl(init_data, keep_blank_values=True)
    return hmac.new(
        _secret_key(bot_token), data_check_string(pairs).encode(), hashlib.sha256
    ).hexdigest()


def verify(
    init_data: str,
    bot_token: str,
    now: Optional[int] = None,
    max_age: int = MAX_AGE_SECONDS,
) -> WebAppUser:
    """Validate `init_data` and return the caller, or raise ``InitDataError``."""
    if not init_data or not bot_token:
        raise InitDataError("empty initData or bot token")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    supplied = fields.get("hash", "")
    if not supplied:
        raise InitDataError("no hash in initData")
    expected = hmac.new(
        _secret_key(bot_token), data_check_string(pairs).encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise InitDataError("initData signature mismatch")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("auth_date is not an integer") from exc
    age = (now if now is not None else int(time.time())) - auth_date
    if auth_date <= 0 or age > max_age:
        raise InitDataError("initData is stale")

    try:
        user = json.loads(fields.get("user", ""))
        tg_id = int(user["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise InitDataError("initData carries no usable user") from exc
    return WebAppUser(
        tg_id=tg_id,
        first_name=str(user.get("first_name") or ""),
        username=str(user.get("username") or ""),
        auth_date=auth_date,
    )
