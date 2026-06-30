"""Aperture custom-emoji rendering for the bot.

Turns the standard emoji the bot already uses in its cards (🏆🎁💸…) into
branded Telegram custom emoji, inline, when a pack is configured via
BOT_CUSTOM_EMOJI_SET. Premium users see the custom glyphs; everyone else sees
the same standard emoji as the fallback. With no pack resolved, every helper is
a no-op and the bot sends plain Markdown exactly as before.

The pack is created manually via @Stickers (needs Premium); the bot only
*resolves* the published set's document ids and references them.
"""
from __future__ import annotations

from typing import Optional

from telethon.extensions import markdown
from telethon.tl.types import MessageEntityCustomEmoji


def _utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units (Telegram entity unit)."""
    return len(text.encode("utf-16-le")) // 2


def build_entities(clean_text: str, emoji_map: dict[str, int]) -> list[MessageEntityCustomEmoji]:
    """Custom-emoji entities for every mapped emoji occurrence in `clean_text`.

    Offsets/lengths are in UTF-16 units. Longest keys are matched first so
    multi-codepoint emoji (e.g. ⚙️) win over any single-codepoint prefix.
    Pure — no I/O, unit-testable with fake document ids.
    """
    if not emoji_map:
        return []
    keys = sorted(emoji_map, key=len, reverse=True)
    entities: list[MessageEntityCustomEmoji] = []
    offset = 0
    i = 0
    n = len(clean_text)
    while i < n:
        match = next((k for k in keys if clean_text.startswith(k, i)), None)
        if match:
            length = _utf16_len(match)
            entities.append(MessageEntityCustomEmoji(
                offset=offset, length=length, document_id=emoji_map[match]))
            offset += length
            i += len(match)
        else:
            offset += _utf16_len(clean_text[i])
            i += 1
    return entities


def enrich(text: str, emoji_map: dict[str, int]) -> tuple[str, Optional[list]]:
    """Parse Markdown `text` and inject custom-emoji entities.

    Returns ``(clean_text, entities)`` ready for ``formatting_entities=``, or
    ``(text, None)`` when there is nothing to inject (no pack, or no mapped
    emoji present) — callers then send the original text via normal Markdown.
    """
    if not emoji_map or not any(k in text for k in emoji_map):
        return text, None
    clean, md_entities = markdown.parse(text)
    custom = build_entities(clean, emoji_map)
    if not custom:
        return text, None
    merged = sorted(list(md_entities) + custom, key=lambda e: e.offset)
    return clean, merged


async def resolve_custom_emoji_map(client, short_name: str) -> dict[str, int]:
    """Resolve a published custom-emoji pack to ``{emoji_char: document_id}``.

    Best-effort: any failure (no Premium, unknown pack, network) yields an empty
    map so the bot silently falls back to plain emoji. Never raises.
    """
    if not client or not short_name:
        return {}
    try:
        from telethon.tl.functions.messages import GetStickerSetRequest
        from telethon.tl.types import InputStickerSetShortName
        result = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(short_name=short_name), hash=0))
    except Exception:
        return {}
    emap: dict[str, int] = {}
    for pack in getattr(result, "packs", []) or []:
        docs = getattr(pack, "documents", None) or []
        if docs:
            emap[pack.emoticon] = int(docs[0])
    return emap
