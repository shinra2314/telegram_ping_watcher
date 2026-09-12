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
"""
from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Optional

from telethon import Button

from ..app_ctx import state

# Past this, an armed prompt is stale: the user tapped a button, walked away,
# and their next unrelated message must not be read as an answer.
PENDING_TTL_SECONDS = 300

# The cancel button every prompt carries. `st_x` clears the pending entry for
# members as well as the owner, so it is the one settings callback that is not
# owner-only.
CANCEL_BUTTON = [[Button.inline("✖️ Отмена", b"st_x")]]

# kind -> what to ask for
INPUT_PROMPTS: dict[str, str] = {}
# kind -> (consumer, admin-only)
Consumer = Callable[..., Awaitable[None]]
_CONSUMERS: dict[str, tuple[Consumer, bool]] = {}


def register_prompt(kind: str, prompt: str, consumer: Optional[Consumer] = None,
                    admin: bool = True) -> str:
    """Declare an input kind and return it, so callers can use the constant.

    ``consumer(event, pending, raw)`` receives the armed entry (its ``scope``
    carries whatever the screen needed to remember) and the stripped text.
    """
    INPUT_PROMPTS[kind] = prompt
    if consumer is not None:
        _CONSUMERS[kind] = (consumer, admin)
    return kind


def pending_expired(pending: dict) -> bool:
    armed = pending.get("armed_at")
    return (not isinstance(armed, datetime)
            or (datetime.now() - armed).total_seconds() > PENDING_TTL_SECONDS)


def sweep_pending() -> None:
    """Drop armed prompts nobody answered.

    The TTL used to be checked only when that same user next sent text, so a
    user who tapped "добавить" and walked away stayed in the dict for the life
    of the process.
    """
    pending = state.bot_pending_inputs
    for sender in [s for s, p in pending.items() if pending_expired(p)]:
        pending.pop(sender, None)


def take_pending(sender_id: int) -> Optional[dict]:
    """Pop this sender's armed prompt, if any."""
    return state.bot_pending_inputs.pop(sender_id, None)


def clear_pending(sender_id: int) -> None:
    state.bot_pending_inputs.pop(sender_id, None)


async def prompt_pending(event, kind: str, scope: Optional[str] = None) -> None:
    """Ask for text and arm the capture for this sender."""
    sweep_pending()
    # Armed only once the prompt is actually on screen: writing first left the
    # user silently armed under the wrong `kind` if the respond failed.
    await event.respond(f"✍️ {INPUT_PROMPTS[kind]}", buttons=CANCEL_BUTTON)
    state.bot_pending_inputs[event.sender_id] = {
        "kind": kind, "scope": scope, "armed_at": datetime.now(),
    }
    await event.answer()


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
    await consumer(event, pending, (event.message.text or "").strip())
