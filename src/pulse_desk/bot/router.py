"""Callback dispatch: a registry instead of a 600-line if-chain.

``callback_handler`` used to decide everything by walking one linear chain of
``if data == ... / if data.startswith(...)`` branches, which meant every new
section had to be spliced into the middle of one function and the gating was
copy-pasted per branch. This module holds the same decision as data:

* ``exact`` — the whole callback string (``menu_main``, ``noop``)
* ``group`` — a family sharing one leading token (``gw:f:date:0:2`` →  ``gw``,
  ``blockmember_42`` → ``blockmember``). The separator is part of the family,
  so the colon and the legacy underscore forms register the same way.

Dispatch order is exact → colon family → underscore family, which is what keeps
a bare ``sal`` (its own screen) distinct from ``sal:m:2026-09`` (a view inside
it) without either handler knowing about the other.

Everything here is pure: no Telethon, no I/O. ``Click`` carries what a handler
needs about the press, and the gates (`feature`, `admin`) are declared at
registration so the denial wording stays identical across sections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

# Wording is load-bearing: a guest must not be able to tell a section they lack
# a grant for from one that does not exist, so these two differ on purpose.
FEATURE_DENIED = "Раздел закрыт владельцем"
ADMIN_ONLY = "Только владелец"


@dataclass
class Click:
    """One callback-query press, resolved down to what a handler needs."""

    event: Any
    data: str
    role: str
    perms: dict
    has_feature: Callable[[dict, str], bool]
    seg: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.seg = self.data.split(":")

    @property
    def sender_id(self) -> int:
        return self.event.sender_id

    def arg(self, index: int, default: str = "") -> str:
        """Colon segment by position, or ``default`` when the button is short."""
        return self.seg[index] if len(self.seg) > index else default

    def int_arg(self, index: int) -> Optional[int]:
        """Colon segment as an int, or None — malformed data is never fatal."""
        try:
            return int(self.seg[index])
        except (ValueError, IndexError):
            return None

    def tail(self, sep: str = "_") -> str:
        """Everything after the first separator (``hidebc_<token>`` → token)."""
        return self.data.partition(sep)[2]

    def feature_ok(self, code: str) -> bool:
        return self.role == "admin" or self.has_feature(self.perms, code)


Handler = Callable[[Click], Awaitable[None]]


@dataclass
class _Route:
    handler: Handler
    feature: Optional[str] = None
    admin: bool = False

    def denial(self, click: Click) -> Optional[str]:
        """The alert to show instead of running, or None when allowed."""
        if self.admin and click.role != "admin":
            return ADMIN_ONLY
        if self.feature and not click.feature_ok(self.feature):
            return FEATURE_DENIED
        return None


class CallbackRouter:
    """Registry of callback handlers, matched by exact string or by family."""

    def __init__(self) -> None:
        self._exact: dict[str, _Route] = {}
        self._families: dict[str, dict[str, _Route]] = {":": {}, "_": {}}

    # ---- registration ---------------------------------------------------
    def exact(self, *names: str, feature: Optional[str] = None, admin: bool = False):
        """Register for callback data equal to any of ``names``."""
        def decorator(handler: Handler) -> Handler:
            route = _Route(handler, feature=feature, admin=admin)
            for name in names:
                self._exact[name] = route
            return handler
        return decorator

    def group(self, *names: str, sep: str = ":", feature: Optional[str] = None,
              admin: bool = False):
        """Register for ``name`` itself and for ``name`` + ``sep`` + anything."""
        if sep not in self._families:
            raise ValueError(f"unsupported separator: {sep!r}")
        def decorator(handler: Handler) -> Handler:
            route = _Route(handler, feature=feature, admin=admin)
            for name in names:
                self._families[sep][name] = route
            return handler
        return decorator

    # ---- lookup ---------------------------------------------------------
    def resolve(self, data: str) -> Optional[_Route]:
        """The route that owns this callback string, or None."""
        route = self._exact.get(data)
        if route is not None:
            return route
        for sep, table in self._families.items():
            route = table.get(data.split(sep, 1)[0])
            if route is not None:
                return route
        return None

    async def dispatch(self, click: Click) -> bool:
        """Run the owning handler. False means nothing claimed this button.

        A False return is what lets the caller keep the legacy chain (and its
        "кнопка устарела" fallback) as the last word.
        """
        route = self.resolve(click.data)
        if route is None:
            return False
        denial = route.denial(click)
        if denial:
            await click.event.answer(denial, alert=True)
            return True
        await route.handler(click)
        return True
