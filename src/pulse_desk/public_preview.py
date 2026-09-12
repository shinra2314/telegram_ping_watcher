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
import re
from typing import Any, Optional

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
