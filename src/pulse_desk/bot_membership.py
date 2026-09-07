"""Who a Telegram user is to this bot: their role and their grants.

Lifted out of ``bot/service.py`` so that surfaces other than the bot's own
handlers — the Mini App — resolve access through exactly the same rules
instead of a second, drifting copy. Nothing here touches the bot client, so a
FastAPI router can import it freely.

``resolve_member_access`` takes its collaborators as arguments. That keeps the
decision logic testable without a database, and lets a caller substitute its
own membership lookup or schedule decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from .access_control import resolve_access, window_from_row
from .app_ctx import ADMIN_ID, settings, state
from .bot_permissions import full_permissions, parse_permissions


def admin_chat_ids(admin_id: str, extra_chats: str) -> set[int]:
    """Owner chat ids: the configured admin plus any comma-separated extras.

    Junk entries are dropped rather than raising — a typo in the env var must
    not take the bot's whole permission check down.
    """
    ids: set[int] = set()
    for chunk in [admin_id, *(extra_chats or "").split(",")]:
        chunk = (chunk or "").strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            continue
    return ids


def configured_admin_ids() -> set[int]:
    """`admin_chat_ids` fed from settings — the form callers normally want."""
    return admin_chat_ids(str(ADMIN_ID or ""), settings.bot_admin_chats or "")


async def access_decision(sender_id: int, member: dict) -> tuple[bool, str, Optional[datetime]]:
    """Cached schedule decision for a member: (allowed, reason, until_utc).

    The source of truth is computed here, not read from a stored flag, so access
    stays correct even when the scheduler loop is down. The cache only skips
    repeat SQL until the next window boundary.
    """
    from database import list_access_windows

    now = datetime.now(timezone.utc)
    cached = state.access_cache.get(sender_id)
    if cached and now < cached[1]:
        return cached[0], cached[2], cached[1]
    rows = await list_access_windows(sender_id)
    decision = resolve_access(member, [window_from_row(r) for r in rows], now)
    until = decision.until or (now + timedelta(minutes=1))
    state.access_cache[sender_id] = (decision.allowed, until, decision.reason)
    return decision.allowed, decision.reason, until


async def resolve_member_access(
    sender_id: int,
    *,
    admin_ids: Optional[set[int]] = None,
    get_member: Optional[Callable[[int], Awaitable[Optional[dict]]]] = None,
    touch: Optional[Callable[[int], Awaitable[Any]]] = None,
    decide: Optional[Callable[[int, dict], Awaitable[tuple]]] = None,
) -> tuple[Optional[str], dict]:
    """Resolve a Telegram user to ``(role, grants)``.

    ``role`` is 'admin', 'viewer'/'premium', or None for no access. Admins
    bypass both the schedule and the grants; members are gated by their access
    windows (see access_control.resolve_access) and carry the grants copied
    from the key they joined with.
    """
    if admin_ids is None:
        admin_ids = configured_admin_ids()
    if get_member is None or touch is None:
        from database import get_bot_member, touch_bot_member

        get_member = get_member or get_bot_member
        touch = touch or touch_bot_member
    decide = decide or access_decision

    if sender_id in admin_ids:
        return "admin", full_permissions()
    member = await get_member(sender_id)
    if not member or member.get("blocked"):
        return None, full_permissions()
    await touch(sender_id)
    allowed, _reason, _until = await decide(sender_id, member)
    if not allowed:
        return None, full_permissions()
    return member.get("role") or "viewer", parse_permissions(member.get("permissions"))
