from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, TypeVar

try:
    from telethon.errors import AuthKeyDuplicatedError, ServerError
except Exception:  # pragma: no cover - Telethon is a runtime dependency.
    AuthKeyDuplicatedError = None  # type: ignore[assignment]
    ServerError = None  # type: ignore[assignment]

try:
    from telethon.errors import (
        ChannelPrivateError,
        ChatForbiddenError,
        ChannelInvalidError,
    )
except Exception:  # pragma: no cover - Telethon is a runtime dependency.
    ChannelPrivateError = None  # type: ignore[assignment]
    ChatForbiddenError = None  # type: ignore[assignment]
    ChannelInvalidError = None  # type: ignore[assignment]


AUTH_KEY_DUPLICATED_STATUS = "session_ip_conflict"

_AUTH_KEY_DUPLICATED_MARKERS = (
    "AUTH_KEY_DUPLICATED",
    "used under two different IP addresses",
    "auth key duplicated",
)


def is_auth_key_duplicated(exc: BaseException) -> bool:
    if AuthKeyDuplicatedError is not None and isinstance(exc, AuthKeyDuplicatedError):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker.lower() in text for marker in _AUTH_KEY_DUPLICATED_MARKERS)


def auth_key_duplicated_message(session_name: str) -> str:
    return (
        f"Telegram session '{session_name}' was invalidated because the same .session file "
        "was used from two IP addresses at the same time. Stop every other copy of this app, "
        "move this broken .session file out of the active session directory, then sign in "
        "again. On another IP, create a separate session file instead of reusing this one."
    )


_CHANNEL_INACCESSIBLE_MARKERS = (
    "channel specified is private",
    "you lack permission to access it",
    "you were banned from it",
    "channel_private",
    "channel_invalid",
    "chat_forbidden",
)


def is_channel_inaccessible(exc: BaseException) -> bool:
    """Permanent access failures for a channel (private, banned, invalid).

    These do not resolve on retry, so callers should skip the channel quietly
    instead of logging a WARNING on every sweep.
    """
    for cls in (ChannelPrivateError, ChatForbiddenError, ChannelInvalidError):
        if cls is not None and isinstance(exc, cls):
            return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in _CHANNEL_INACCESSIBLE_MARKERS)


TRANSIENT_RPC_MAX_RETRIES = 3
TRANSIENT_RPC_BASE_DELAY_SECONDS = 2.0
TRANSIENT_RPC_MAX_DELAY_SECONDS = 30.0

_TRANSIENT_RPC_MARKERS = (
    "rpc_call_fail",
    "rpc_mcget_fail",
    "telegram is having internal issues",
    "history_get_failed",
    "persistent_timestamp_outdated",
)

_T = TypeVar("_T")


def is_transient_rpc_error(exc: BaseException) -> bool:
    """Server-side Telegram failures (RPC_CALL_FAIL etc.) that resolve on retry.
    FloodWaitError is intentionally excluded — callers handle it separately."""
    if ServerError is not None and isinstance(exc, ServerError):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_RPC_MARKERS)


def transient_rpc_delay_seconds(attempt: int) -> float:
    delay = TRANSIENT_RPC_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1))
    return min(delay, TRANSIENT_RPC_MAX_DELAY_SECONDS)


async def call_rpc_resilient(
    fn: Callable[[], Awaitable[_T]],
    *,
    retries: int = TRANSIENT_RPC_MAX_RETRIES,
    logger: Optional[Any] = None,
    label: str = "telegram-rpc",
) -> _T:
    attempt = 0
    while True:
        try:
            return await fn()
        except Exception as exc:
            if not is_transient_rpc_error(exc) or attempt >= retries:
                raise
            attempt += 1
            delay = transient_rpc_delay_seconds(attempt)
            if logger is not None:
                logger.warning(
                    "Transient Telegram error in %s (%s); retry %s/%s in %.0fs",
                    label, exc, attempt, retries, delay,
                )
            await asyncio.sleep(delay)


async def iter_messages_resilient(
    client: Any,
    entity: Any,
    *,
    retries: int = TRANSIENT_RPC_MAX_RETRIES,
    logger: Optional[Any] = None,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """client.iter_messages that survives transient Telegram server errors
    (RpcCallFailError and friends) by resuming below the last yielded message.
    Assumes the default newest-to-oldest iteration order (no reverse=True).
    FloodWaitError and other non-transient errors propagate to the caller."""
    limit = kwargs.pop("limit", None)
    max_id = int(kwargs.pop("max_id", 0) or 0)
    yielded = 0
    attempt = 0
    while True:
        remaining = None if limit is None else max(int(limit) - yielded, 0)
        if remaining == 0:
            return
        try:
            async for message in client.iter_messages(entity, limit=remaining, max_id=max_id, **kwargs):
                yielded += 1
                attempt = 0
                message_id = int(getattr(message, "id", 0) or 0)
                if message_id:
                    max_id = message_id
                yield message
            return
        except Exception as exc:
            if not is_transient_rpc_error(exc) or attempt >= retries:
                raise
            attempt += 1
            delay = transient_rpc_delay_seconds(attempt)
            if logger is not None:
                logger.warning(
                    "Transient Telegram error while iterating messages (%s); retry %s/%s in %.0fs",
                    exc, attempt, retries, delay,
                )
            await asyncio.sleep(delay)

