"""Chats the owner told the watcher to leave alone (pure rules + state).

Groups became watched on 2026-09-16, and with them the owner's own friend
chats, where a nickname in passing is not news. A chat on this list is skipped
before anything is read: no ping, no card, no member copy, no catch-up.

The whole list is the ``ignored_chats`` settings key — ``{chat_id: title}``
with the chat id in Telethon's marked form (``-100…`` for supergroups and
channels), the same form ``pings.chat_id`` stores. ``state.ignored_chat_ids``
mirrors it for the pipeline, which checks it on every incoming message.
"""
from __future__ import annotations

from typing import Any

from .app_ctx import state

SETTINGS_KEY = "ignored_chats"


def normalize(raw: Any) -> dict[str, str]:
    """``{str(chat_id): title}``; anything malformed is dropped."""
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, title in raw.items():
        try:
            chat_id = int(key)
        except (TypeError, ValueError):
            continue
        out[str(chat_id)] = str(title or chat_id)
    return out


def add(cfg: dict[str, str], chat_id: int, title: str) -> dict[str, str]:
    return {**cfg, str(int(chat_id)): str(title or chat_id)}


def remove(cfg: dict[str, str], chat_id: int) -> dict[str, str]:
    return {key: value for key, value in cfg.items() if key != str(int(chat_id))}


def ids(cfg: dict[str, str]) -> set[int]:
    return {int(key) for key in cfg}


def apply(cfg: dict[str, str]) -> None:
    state.ignored_chat_ids = ids(cfg)


def is_ignored(chat_id: Any) -> bool:
    try:
        return chat_id is not None and int(chat_id) in state.ignored_chat_ids
    except (TypeError, ValueError):
        return False


async def load() -> dict[str, str]:
    from database import get_setting

    return normalize(await get_setting(SETTINGS_KEY, None))


async def save(cfg: dict[str, str]) -> None:
    """Save and apply at once: a saved list the pipeline does not see yet is a lie."""
    from database import set_setting

    await set_setting(SETTINGS_KEY, cfg)
    apply(cfg)
