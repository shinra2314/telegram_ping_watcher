"""Pure visual components for the bot UI — the shared style vocabulary.

No Telethon events, no I/O. Reused by every renderer so the look stays
consistent. Foundation for the Phase 3 renderer rework.
"""
from __future__ import annotations

from typing import Optional, Union

from .views import DIV

_DOTS = {"online": "🟢", "offline": "🔴", "degraded": "🟡"}


def header(icon: str, title: str, crumb: Optional[str] = None) -> str:
    """Section banner: CAPS title, optional breadcrumb subtitle, divider."""
    lines = [f"{icon} **{title.upper()}**"]
    if crumb:
        lines.append(f"__{crumb}__")
    lines.append(DIV)
    return "\n".join(lines)


def kv(icon: str, label: str, value: object) -> str:
    """Key-value row with a monospaced value."""
    return f"{icon} {label}: `{value}`"


def dot(status: Union[bool, str]) -> str:
    """Status indicator: 🟢 online / 🔴 offline / 🟡 anything else."""
    if status is True:
        return "🟢"
    if status is False:
        return "🔴"
    return _DOTS.get(str(status).lower(), "🟡")


def empty(text: str) -> str:
    """Uniform empty-state line."""
    return f"📭 __{text}__"
