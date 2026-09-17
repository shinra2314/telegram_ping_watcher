"""Free-text capture: the bot's stand-in for a conversation handler.

Telethon has no ``ConversationHandler``, so a screen that needs typing arms one
entry in ``state.bot_pending_inputs`` (keyed by sender) and the catch-all
message handler consumes it. Both halves used to be closures inside
``init_bot``, which is why no section outside that function could ask for input.

A kind is declared once, with its prompt *and* its consumer, by the section that
owns it — so adding "type a number here" to a new screen is a local change
rather than an edit to two central if-chains. ``admin=True`` (the default) is
enforced here at consume time, because the consumer that trusts it is the one
being called.

Clean chat: every input used to leave four messages behind — the screen with
the button, the «✍️» prompt, the typed answer and the result. The entry now
remembers the ids of what the exchange produced (``cleanup``) and of the screen
it was armed from; once the consumer has answered, those are deleted, so only
the result stays. A consumer that cannot accept the text raises
:class:`InputRejected` instead of answering — the prompt stays armed and the
user simply types again (``MAX_ATTEMPTS``), rather than being sent back through
the menu.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from typing import Awaitable, Callable, Iterable, Optional

from telethon import Button

from ..app_ctx import state

log = logging.getLogger("pulse_desk")

# Past this, an armed prompt is stale: the user tapped a button, walked away,
# and their next unrelated message must not be read as an answer.
PENDING_TTL_SECONDS = 300

# Wrong answers accepted before the prompt gives up.
MAX_ATTEMPTS = 3

# The cancel button every prompt carries. `st_x` clears the pending entry for
# members as well as the owner, so it is the one settings callback that is not
# owner-only.
CANCEL_BUTTON = [[Button.inline("✖️ Отмена", b"st_x")]]

# kind -> what to ask for
INPUT_PROMPTS: dict[str, str] = {}
# kind -> (consumer, admin-only)
Consumer = Callable[..., Awaitable[None]]
_CONSUMERS: dict[str, tuple[Consumer, bool]] = {}
# Kinds whose originating screen must survive the exchange: they are armed from
# cards that can be notifications (a win card, the roulette nudge), and deleting
# those would lose the notification itself.
_KEEP_SCREEN: set[str] = set()


class InputRejected(Exception):
    """Raised by a consumer when the typed text is not acceptable.

    The message is shown to the user and the prompt stays armed.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def register_prompt(kind: str, prompt: str, consumer: Optional[Consumer] = None,
                    admin: bool = True, keep_screen: bool = False) -> str:
    """Declare an input kind and return it, so callers can use the constant.

    ``consumer(event, pending, raw)`` receives the armed entry (its ``scope``
    carries whatever the screen needed to remember) and the stripped text.
    ``keep_screen=True`` leaves the screen the prompt was opened from in place.
    """
    INPUT_PROMPTS[kind] = prompt
    if consumer is not None:
        _CONSUMERS[kind] = (consumer, admin)
    if keep_screen:
        _KEEP_SCREEN.add(kind)
    else:
        _KEEP_SCREEN.discard(kind)
    return kind


def pending_expired(pending: dict, now: Optional[datetime] = None) -> bool:
    armed = pending.get("armed_at")
    return (not isinstance(armed, datetime)
            or ((now or datetime.now()) - armed).total_seconds() > PENDING_TTL_SECONDS)


def expired_senders(pending: dict[int, dict], now: Optional[datetime] = None) -> list[int]:
    return [sender for sender, entry in pending.items() if pending_expired(entry, now)]


def sweep_pending() -> list[dict]:
    """Drop armed prompts nobody answered; returns the dropped entries.

    The TTL used to be checked only when that same user next sent text, so a
    user who tapped "добавить" and walked away stayed in the dict for the life
    of the process. The ``bot-janitor`` job calls this every minute and deletes
    the prompts the returned entries point at.
    """
    pending = state.bot_pending_inputs
    return [entry for sender in expired_senders(pending)
            if (entry := pending.pop(sender, None)) is not None]


def take_pending(sender_id: int) -> Optional[dict]:
    """Pop this sender's armed prompt, if any."""
    return state.bot_pending_inputs.pop(sender_id, None)


def clear_pending(sender_id: int) -> Optional[dict]:
    return state.bot_pending_inputs.pop(sender_id, None)


def _message_id(message) -> Optional[int]:
    value = getattr(message, "id", None)
    return value if isinstance(value, int) else None


def _screen_id(event) -> Optional[int]:
    """The message whose button armed the prompt (callback events only)."""
    if getattr(event, "data", None) is None:
        return None
    value = getattr(event, "message_id", None)
    return value if isinstance(value, int) else None


async def delete_quietly(chat_id, message_ids: Iterable[Optional[int]]) -> int:
    """Best-effort delete; a message already gone or too old is not an error.

    Telegram lets a bot delete messages in a private chat only while they are
    younger than 48 hours, and every id here is minutes old.
    """
    ids = sorted({int(mid) for mid in message_ids if isinstance(mid, int)})
    client = state.bot_client
    if not ids or client is None or chat_id is None:
        return 0
    try:
        await client.delete_messages(chat_id, ids)
    except Exception:
        log.debug("Could not delete bot chat messages %s", ids, exc_info=True)
        return 0
    return len(ids)


async def prompt_pending(event, kind: str, scope: Optional[str] = None) -> None:
    """Ask for text and arm the capture for this sender."""
    for stale in sweep_pending():
        await delete_quietly(stale.get("chat_id"), stale.get("cleanup") or [])
    previous = clear_pending(event.sender_id)
    if previous:
        # A second prompt replaces the first: its «✍️» message goes too.
        await delete_quietly(previous.get("chat_id"), previous.get("cleanup") or [])
    # Armed only once the prompt is actually on screen: writing first left the
    # user silently armed under the wrong `kind` if the respond failed.
    prompt = await event.respond(f"✍️ {INPUT_PROMPTS[kind]}", buttons=CANCEL_BUTTON)
    state.bot_pending_inputs[event.sender_id] = {
        "kind": kind,
        "scope": scope,
        "armed_at": datetime.now(),
        "chat_id": getattr(event, "chat_id", None),
        "cleanup": [mid for mid in (_message_id(prompt),) if mid is not None],
        "screen_id": _screen_id(event),
        "attempts": 0,
    }
    with suppress(Exception):
        await event.answer()


def cleanup_ids(pending: dict, answer_id: Optional[int], *, success: bool) -> list[int]:
    """What to delete once an exchange ends.

    Always the prompt/error messages and the typed answer; on success also the
    originating screen, unless its kind keeps it — the consumer has just sent
    the fresh version of that screen.
    """
    ids = [mid for mid in (pending.get("cleanup") or []) if isinstance(mid, int)]
    if isinstance(answer_id, int):
        ids.append(answer_id)
    screen = pending.get("screen_id")
    if success and isinstance(screen, int) and pending.get("kind") not in _KEEP_SCREEN:
        ids.append(screen)
    return ids


async def consume(event, role: str, pending: dict) -> None:
    """Hand the typed text to whoever armed the prompt.

    Silence is the right answer for an unknown or owner-only kind: the entry is
    already popped, so the next message starts clean, and a member who somehow
    holds an owner prompt learns nothing from it.
    """
    entry = _CONSUMERS.get(pending.get("kind"))
    if entry is None:
        return
    consumer, admin_only = entry
    if admin_only and role != "admin":
        return
    chat_id = pending.get("chat_id") or getattr(event, "chat_id", None)
    answer_id = _message_id(getattr(event, "message", None))
    try:
        await consumer(event, pending, (event.message.text or "").strip())
    except InputRejected as rejected:
        await _retry(event, pending, rejected, chat_id, answer_id)
        return
    await delete_quietly(chat_id, cleanup_ids(pending, answer_id, success=True))


async def _retry(event, pending: dict, rejected: InputRejected, chat_id, answer_id) -> None:
    attempts = int(pending.get("attempts") or 0) + 1
    # The previous error and the wrong answer go now; the prompt itself stays
    # until the exchange ends.
    old = cleanup_ids(pending, answer_id, success=False)
    prompt_ids = [mid for mid in (pending.get("cleanup") or [])[:1] if isinstance(mid, int)]
    if attempts >= MAX_ATTEMPTS:
        await delete_quietly(chat_id, old)
        await event.respond(f"{rejected.message}\n\n⌛ Ввод отменён после {MAX_ATTEMPTS} попыток — "
                            "откройте раздел заново.")
        return
    await delete_quietly(chat_id, [mid for mid in old if mid not in prompt_ids])
    reply = await event.respond(
        f"{rejected.message}\n✍️ Пришлите ещё раз (попытка {attempts + 1} из {MAX_ATTEMPTS}).",
        buttons=CANCEL_BUTTON,
    )
    state.bot_pending_inputs[event.sender_id] = {
        **pending,
        "armed_at": datetime.now(),
        "attempts": attempts,
        "cleanup": prompt_ids + [mid for mid in (_message_id(reply),) if mid is not None],
    }


async def cancel_pending(event) -> None:
    """The «✖️ Отмена» button: disarm and remove the prompt, keep the screen."""
    pending = clear_pending(event.sender_id)
    ids = list((pending or {}).get("cleanup") or [])
    own = getattr(event, "message_id", None)
    if isinstance(own, int):
        ids.append(own)
    await delete_quietly(getattr(event, "chat_id", None) or (pending or {}).get("chat_id"), ids)
