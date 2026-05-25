from __future__ import annotations

try:
    from telethon.errors import AuthKeyDuplicatedError
except Exception:  # pragma: no cover - Telethon is a runtime dependency.
    AuthKeyDuplicatedError = None  # type: ignore[assignment]


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

