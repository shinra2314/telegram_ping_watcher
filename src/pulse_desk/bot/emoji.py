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


VS16 = "️"


def _utf16_len(text: str) -> int:
    """Length of `text` in UTF-16 code units (Telegram entity unit)."""
    return len(text.encode("utf-16-le")) // 2


def with_vs16_variants(emap: dict[str, int]) -> dict[str, int]:
    """Register each emoticon both with and without its variation selector.

    Telegram normalises a pack's emoticons, so a glyph assigned as ``⚠️``
    (U+26A0 U+FE0F) can come back as bare ``⚠``. The bot's card text uses the
    VS16 form, and matching only the bare codepoint would emit an entity one
    UTF-16 unit short — a misaligned entity Telegram rejects or renders on the
    wrong character. Registering both forms makes the match exact either way;
    ``build_entities`` tries the longest key first, so the VS16 form wins when
    the text carries it. Existing keys are never overwritten.
    """
    out = dict(emap)
    for key, doc in emap.items():
        alt = key[:-1] if key.endswith(VS16) else key + VS16
        if alt and alt not in out:
            out[alt] = doc
    return out


def compile_emoji_map(emoji_map: dict[str, int]) -> dict[str, list[str]]:
    """Bucket the pack's keys by first character, longest first.

    ``build_entities`` walks the text one character at a time and has to know
    which key (if any) starts there. Scanning all ~170 keys per position made a
    3 500-character card cost ~600 000 ``startswith`` calls on the event loop,
    for every message the bot sends or edits. Bucketing by first character turns
    that into a dict lookup plus a comparison against the handful of keys that
    can possibly match, and the sort happens once instead of once per render.

    Pure; the result is cached on ``state.custom_emoji_index`` at startup.
    """
    buckets: dict[str, list[str]] = {}
    for key in emoji_map:
        if key:
            buckets.setdefault(key[0], []).append(key)
    for keys in buckets.values():
        # Longest first so multi-codepoint emoji (e.g. ⚙️) beat a shorter prefix.
        keys.sort(key=len, reverse=True)
    return buckets


def build_entities(
    clean_text: str,
    emoji_map: dict[str, int],
    index: Optional[dict[str, list[str]]] = None,
) -> list[MessageEntityCustomEmoji]:
    """Custom-emoji entities for every mapped emoji occurrence in `clean_text`.

    Offsets/lengths are in UTF-16 units. Longest keys are matched first so
    multi-codepoint emoji (e.g. ⚙️) win over any single-codepoint prefix.
    Pure — no I/O, unit-testable with fake document ids. `index` is the
    precompiled bucket map from :func:`compile_emoji_map`; without one it is
    built on the fly, which is correct but costs the sort per call.
    """
    if not emoji_map:
        return []
    buckets = index if index is not None else compile_emoji_map(emoji_map)
    entities: list[MessageEntityCustomEmoji] = []
    offset = 0
    i = 0
    n = len(clean_text)
    while i < n:
        match = None
        for key in buckets.get(clean_text[i], ()):
            if clean_text.startswith(key, i):
                match = key
                break
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


def enrich(
    text: str,
    emoji_map: dict[str, int],
    index: Optional[dict[str, list[str]]] = None,
) -> tuple[str, Optional[list]]:
    """Parse Markdown `text` and inject custom-emoji entities.

    Returns ``(clean_text, entities)`` ready for ``formatting_entities=``, or
    ``(text, None)`` when there is nothing to inject (no pack, or no mapped
    emoji present) — callers then send the original text via normal Markdown.
    """
    if not emoji_map:
        return text, None
    buckets = index if index is not None else compile_emoji_map(emoji_map)
    # Cheap pre-check: a card with no mapped emoji at all is the common case for
    # plain replies, and testing membership of the first characters beats the
    # ~170 substring scans `any(k in text for k in emoji_map)` used to do.
    if not any(ch in buckets for ch in text):
        return text, None
    clean, md_entities = markdown.parse(text)
    custom = build_entities(clean, emoji_map, buckets)
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
    return with_vs16_variants(emap)
