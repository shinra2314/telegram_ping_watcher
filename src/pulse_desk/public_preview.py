"""Recover the text of a channel post Telethon cannot decode.

Telegram downgrades any media newer than the client's API layer to
``messageMediaUnsupported``: the message arrives with an empty ``message``
field, no entities and no buttons. The new mini-app lottery result cards (the
@Random "Lot" winner table) are delivered exactly this way, so a win inside one
of them carried no @username to match.

Telegram's *global* search does still index such a card (see ``global_search``,
which is the path that actually catches those wins); the public page below is
the fallback for the rest, and for recovering the card's wording rather than
just the fact of a match.

The public ``t.me/<channel>/<id>`` page still renders such a post, and its
``og:description`` meta tag carries the whole card as plain text, winner list
included. Reading that page gives the mention parser something to work with;
everything downstream (win detection, notifications) stays unchanged.

Only public channels have such a page — a private one cannot be recovered.
"""
from __future__ import annotations

import html as html_module
import logging
import re
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

PUBLIC_MESSAGE_URL = "https://t.me/{username}/{message_id}"
# The page ships one og:description holding the full rendered card.
OG_DESCRIPTION_RE = re.compile(
    r"<meta\s+property=\"og:description\"\s+content=\"(.*?)\"\s*/?>",
    re.IGNORECASE | re.DOTALL,
)
# Telethon names every media it cannot decode this way, whatever the server sent.
UNREADABLE_MEDIA_TYPES = ("MessageMediaUnsupported",)
FETCH_TIMEOUT_SECONDS = 10.0
# t.me answers a bot-looking client with a stub page.
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


def is_unreadable_media(message: Any) -> bool:
    """True when the whole payload is media Telethon could not decode.

    A caption is left alone: it is real text, so the normal mention parser owns
    the message and no network round-trip is needed.
    """
    media = getattr(message, "media", None)
    if media is None or type(media).__name__ not in UNREADABLE_MEDIA_TYPES:
        return False
    return not (getattr(message, "raw_text", "") or "").strip()


def public_message_url(username: str, message_id: Any) -> str:
    return PUBLIC_MESSAGE_URL.format(username=str(username).lstrip("@"), message_id=message_id)


def parse_og_description(page_html: str) -> str:
    """Plain text of the rendered post, or "" when the page carries none."""
    match = OG_DESCRIPTION_RE.search(page_html or "")
    if not match:
        return ""
    return html_module.unescape(match.group(1)).strip()


async def fetch_public_message_text(username: str, message_id: Any) -> str:
    """Rendered text of a public channel post, or "" when it cannot be read."""
    if not username:
        return ""
    url = public_message_url(username, message_id)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_USER_AGENT},
    ) as client:
        response = await client.get(url)
    if response.status_code != 200:
        return ""
    return parse_og_description(response.text)


# Every sweep re-reads the recent window on every account that sits in the
# channel, so one undecodable post was fetched from t.me — and logged — several
# times a minute. The page only changes when the post is edited, so the text is
# kept per post and re-read on a new edit date or once the entry expires. A
# failed fetch is remembered briefly too: a t.me hiccup must not cost a 10 s
# timeout on every account in every sweep.
RECOVERY_CACHE_SECONDS = 30 * 60
RECOVERY_FAILURE_SECONDS = 5 * 60
RECOVERY_CACHE_MAX = 512
# (username, message id) -> (expires at, edit marker, text)
_recovered: dict[tuple[str, str], tuple[float, str, str]] = {}


async def recover_public_text(
    username: str,
    message_id: Any,
    edit_date: Any = None,
    *,
    fetch: Optional[Callable[[str, Any], Awaitable[str]]] = None,
    now: Optional[float] = None,
) -> tuple[str, bool]:
    """(text, changed) for a public post, served from cache while it is fresh.

    ``changed`` is True only when the text differs from what this process saw
    for the post before, so the caller logs a recovery once instead of on every
    sweep. Never raises.
    """
    clock = time.monotonic() if now is None else now
    key = (str(username).lstrip("@").lower(), str(message_id))
    marker = str(edit_date or "")
    cached = _recovered.get(key)
    if cached and cached[0] > clock and cached[1] == marker:
        return cached[2], False
    try:
        text = await (fetch or fetch_public_message_text)(username, message_id)
        ttl = RECOVERY_CACHE_SECONDS
    except Exception:
        logging.getLogger("pulse_desk").debug(
            "Could not read the public page of %s/%s", username, message_id, exc_info=True)
        text, ttl = "", RECOVERY_FAILURE_SECONDS
    previous = cached[2] if cached else ""
    if not text and previous:
        text = previous  # a failed re-read does not forget a good copy
    _recovered[key] = (clock + ttl, marker, text)
    if len(_recovered) > RECOVERY_CACHE_MAX:
        for stale in [k for k, v in _recovered.items() if v[0] <= clock]:
            del _recovered[stale]
        while len(_recovered) > RECOVERY_CACHE_MAX:
            _recovered.pop(next(iter(_recovered)))
    return text, bool(text) and text != previous


def chat_username(chat: Any) -> Optional[str]:
    username = getattr(chat, "username", None)
    if username:
        return str(username)
    # A channel can carry its public name in the collectible-usernames list
    # instead of the primary field.
    for extra in getattr(chat, "usernames", None) or []:
        name = getattr(extra, "username", None)
        if name and getattr(extra, "active", True):
            return str(name)
    return None
