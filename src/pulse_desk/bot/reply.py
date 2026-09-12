"""Getting a card in front of the user, whichever channel still works.

Telethon's message API is full of ways to fail silently — an edit on a message
older than the edit window, an ``answer()`` that is a no-op because the first
``edit`` already answered the query, a card that renders one way with the
Aperture custom-emoji pack and another way without it. Every handler goes
through these three functions so that handling is written once.

Lifted out of ``service.py`` so section modules can reply without importing the
bot's entry point (which imports them).
"""
from __future__ import annotations

from contextlib import suppress

from telethon import events
from telethon.errors import (
    ChatWriteForbiddenError,
    MessageIdInvalidError,
    MessageNotModifiedError,
)

try:  # Telethon splits these across versions; missing ones must not break import.
    from telethon.errors import MessageEditTimeExpiredError
except ImportError:  # pragma: no cover
    class MessageEditTimeExpiredError(Exception):  # type: ignore[no-redef]
        pass

try:
    from telethon.errors import MessageTooLongError
except ImportError:  # pragma: no cover
    class MessageTooLongError(Exception):  # type: ignore[no-redef]
        pass

# The edit failed because the message is gone or too old to touch — the content
# is still worth showing, so these degrade to a fresh message.
EDIT_GONE_ERRORS = (MessageIdInvalidError, MessageEditTimeExpiredError)
# The edit can never succeed as written; say so rather than retrying.
EDIT_FATAL_ERRORS = (MessageTooLongError, ChatWriteForbiddenError)

from ..app_ctx import logger, state
from .emoji import enrich


def is_callback(event) -> bool:
    """True for a callback query — the only event kind that can be answered.

    Duck-typed on the callback payload rather than the Telethon class, so the
    error paths below can be exercised with a plain double.
    """
    if isinstance(event, events.CallbackQuery.Event):
        return True
    return getattr(event, "data", None) is not None and hasattr(event, "answer")


async def tell(event, text: str) -> None:
    """Get `text` in front of the user whichever channel still works.

    Telethon fires ``answer()`` as a background task on the first
    ``edit``/``respond`` and sets ``_answered``, after which a later
    ``answer(alert=True)`` returns without an RPC — so a handler that
    failed *after* its first edit used to show the user nothing at all.
    """
    if is_callback(event) and not getattr(event, "_answered", False):
        with suppress(Exception):
            await event.answer(text, alert=True)
            return
    with suppress(Exception):
        await event.respond(text)


async def safe_edit(event, *args, **kwargs) -> None:
    """Edit the callback message, ignoring 'not modified' errors.

    Injects Aperture custom emoji into the text when a pack is resolved;
    with no pack the text is sent unchanged (normal Markdown).
    """
    if (args and isinstance(args[0], str) and state.custom_emoji_map
            and "formatting_entities" not in kwargs):
        clean, ents = enrich(args[0], state.custom_emoji_map, state.custom_emoji_index)
        if ents is not None:
            args = (clean,) + tuple(args[1:])
            kwargs["formatting_entities"] = ents
            kwargs["parse_mode"] = None
    try:
        await event.edit(*args, **kwargs)
    except MessageNotModifiedError:
        await event.answer()
    except EDIT_GONE_ERRORS:
        # Message deleted, or older than Telegram's edit window. The card
        # itself is still useful, so send it rather than losing the click.
        logger.warning("Cannot edit bot message (gone/expired); resending")
        with suppress(Exception):
            await event.respond(*args, **kwargs)
    except EDIT_FATAL_ERRORS as exc:
        logger.warning("Bot message edit rejected: %s", exc)
        await tell(event, "⚠️ Не получилось показать карточку. Откройте раздел заново.")


async def respond_rich(event, text, **kwargs):
    """``event.respond`` with Aperture custom emoji injected when available."""
    if state.custom_emoji_map and "formatting_entities" not in kwargs:
        clean, ents = enrich(text, state.custom_emoji_map, state.custom_emoji_index)
        if ents is not None:
            return await event.respond(clean, formatting_entities=ents,
                                       parse_mode=None, **kwargs)
    return await event.respond(text, **kwargs)
