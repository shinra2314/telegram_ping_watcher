"""Telethon stand-ins for bot tests.

Nothing in ``tests/`` used to construct a fake Telethon *event*, so handler
behaviour — the error paths in particular — was untestable. These doubles model
the one piece of Telethon semantics those paths turn on: ``edit``/``respond``
answer the callback query as a side effect and set ``_answered``, after which a
later ``answer()`` is a silent no-op. That is exactly why an error raised after
a handler's first edit used to reach the user as nothing at all.
"""
from __future__ import annotations

from typing import Any, Optional


class FakeEvent:
    """A callback-query or message event, recording what the handler sent.

    ``raises`` maps a method name ('edit', 'respond', 'answer', 'delete') to an
    exception instance to raise the next time it is called, so a test can make
    exactly one step of a handler fail.
    """

    def __init__(
        self,
        *,
        sender_id: int = 1,
        data: Optional[bytes] = None,
        text: str = "",
        raises: Optional[dict[str, BaseException]] = None,
        is_callback: bool = True,
    ) -> None:
        self.sender_id = sender_id
        self.data = data
        self.message = _FakeMessage(text)
        self._answered = False
        self._is_callback = is_callback
        self._raises = dict(raises or {})
        self.edits: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.deleted = 0

    def _maybe_raise(self, name: str) -> None:
        exc = self._raises.pop(name, None)
        if exc is not None:
            raise exc

    async def edit(self, *args: Any, **kwargs: Any) -> None:
        # Telethon answers the query before the edit RPC, so the answer happens
        # even when the edit itself goes on to fail.
        self._answered = True
        self._maybe_raise("edit")
        self.edits.append({"args": args, "kwargs": kwargs})

    async def respond(self, *args: Any, **kwargs: Any) -> None:
        self._answered = True
        self._maybe_raise("respond")
        self.responses.append({"args": args, "kwargs": kwargs})

    async def answer(self, text: str = "", alert: bool = False) -> None:
        self._maybe_raise("answer")
        if self._answered:
            # Matches Telethon: the query is already answered, so this is a no-op.
            return
        self._answered = True
        self.answers.append({"text": text, "alert": alert})

    async def delete(self) -> None:
        self._maybe_raise("delete")
        self.deleted += 1

    async def get_sender(self) -> Any:
        self._maybe_raise("get_sender")
        return type("Sender", (), {"id": self.sender_id, "username": "tester"})()

    # -- assertions helpers -------------------------------------------------
    @property
    def answer_texts(self) -> list[str]:
        return [a["text"] for a in self.answers]

    @property
    def response_texts(self) -> list[str]:
        return [str(r["args"][0]) if r["args"] else "" for r in self.responses]

    @property
    def edit_texts(self) -> list[str]:
        return [str(e["args"][0]) if e["args"] else "" for e in self.edits]


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.buttons: list = []


class FakeBot:
    """Minimal Telethon client: records sends, hands out message ids."""

    def __init__(self, *, connected: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.deleted: list[tuple[int, list[int]]] = []
        self._next_id = 100
        self._connected = connected
        # Exception raised by the next send_message call, if set.
        self.send_error: Optional[BaseException] = None

    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, value: bool) -> None:
        self._connected = value

    async def connect(self) -> bool:
        return self._connected

    async def send_message(
        self, target: Any, message: str, buttons: Any = None,
        link_preview: bool = False, file: Any = None,
    ) -> Any:
        if self.send_error is not None:
            exc, self.send_error = self.send_error, None
            raise exc
        self._next_id += 1
        self.sent.append({
            "target": target, "message": message,
            "buttons": buttons, "file": file,
        })
        return type("Sent", (), {"id": self._next_id})()

    async def edit_message(self, *args: Any, **kwargs: Any) -> Any:
        self.edited.append({"args": args, "kwargs": kwargs})
        return type("Sent", (), {"id": self._next_id})()

    async def delete_messages(self, peer: Any, message_ids: list[int]) -> None:
        self.deleted.append((peer, list(message_ids)))
