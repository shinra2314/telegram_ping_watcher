"""`/win <ссылка> [@аккаунт]` — record a win the detector missed.

Detection has blind spots: the nightly shutdown, a winners list posted as a
picture, a bot that DMs the result. The owner sees the win elsewhere and used
to have no way to put it on the debts board. The message is read by whichever
connected account can see it; when none can (a private chat the accounts are
not in), the win is still recorded from the link and the named account, so the
debt exists even without its text.

The row goes through the same save and state helpers as a detected ping, so
the board, the Obsidian note and the analytics see no difference — except the
note «добавлено вручную».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .app_ctx import logger, state
from .common import now_iso, record_app_event

MANUAL_NOTE = "✍️ Добавлено вручную"

# https://t.me/<username>/<id>, …/<username>/<topic>/<id>, https://t.me/c/<internal>/<id>
_PUBLIC = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?!c/)([A-Za-z0-9_]{4,64})/(?:\d+/)?(\d+)")
_PRIVATE = re.compile(r"(?:https?://)?(?:t|telegram)\.me/c/(\d+)/(?:\d+/)?(\d+)")


@dataclass(frozen=True)
class MessageRef:
    peer: Any          # "username" or -100… chat id
    message_id: int
    link: str


@dataclass(frozen=True)
class ManualWinResult:
    ok: bool
    message: str
    ping_id: Optional[int] = None


def parse_message_link(raw: str) -> Optional[MessageRef]:
    text = (raw or "").strip()
    match = _PRIVATE.search(text)
    if match:
        internal, message_id = match.groups()
        return MessageRef(int(f"-100{internal}"), int(message_id),
                          f"https://t.me/c/{internal}/{message_id}")
    match = _PUBLIC.search(text)
    if match:
        username, message_id = match.groups()
        return MessageRef(username, int(message_id), f"https://t.me/{username}/{message_id}")
    return None


def parse_accounts(args: list[str], tracked: list[str]) -> tuple[list[str], list[str]]:
    """(tracked accounts named in args, names that are not tracked)."""
    known = {str(u).strip().lstrip("@").lower(): str(u).strip().lstrip("@") for u in tracked if u}
    found: list[str] = []
    unknown: list[str] = []
    for arg in args:
        name = arg.strip().strip(",").lstrip("@")
        if not name or "/" in name or "." in name:
            continue
        if name.lower() in known:
            found.append(known[name.lower()])
        else:
            unknown.append(name)
    return list(dict.fromkeys(found)), unknown


def build_manual_record(ref: MessageRef, base: Optional[dict[str, Any]],
                        accounts: list[str]) -> dict[str, Any]:
    """The ping row: what the message gave us, plus the accounts the owner named."""
    record = dict(base or {})
    mentions = list(dict.fromkeys(list(record.get("mentions") or []) + accounts))
    record.update({
        "mentions": mentions,
        "link": record.get("link") or ref.link,
        "chat": record.get("chat") or (f"@{ref.peer}" if isinstance(ref.peer, str) else str(ref.peer)),
        "chat_id": record.get("chat_id") if record.get("chat_id") is not None
        else (ref.peer if isinstance(ref.peer, int) else None),
        "message_id": record.get("message_id") or ref.message_id,
        "text": record.get("text") or f"{MANUAL_NOTE}: {ref.link}",
        "chat_type": record.get("chat_type") or "channel",
        "detected_at": now_iso(),
        "is_win": True,
        "note": MANUAL_NOTE,
    })
    if record["chat_type"] not in ("channel", "group"):
        # The debts board lists channel and group wins only; a manual win is a
        # debt wherever it came from.
        record["chat_type"] = "channel"
    return record


async def _fetch(ref: MessageRef) -> tuple[Any, Any]:
    """(client, message) from the first connected account that can read it."""
    for client in list(state.clients):
        try:
            message = await client.get_messages(ref.peer, ids=ref.message_id)
        except Exception:
            logger.debug("Account cannot read %s/%s", ref.peer, ref.message_id, exc_info=True)
            continue
        if message:
            return client, message
    return None, None


async def record_manual_win(raw_link: str, account_args: list[str]) -> ManualWinResult:
    from database import get_ping_by_message_ref, save_ping, update_ping_meta
    from telegram_ping_watcher import message_to_record

    from .ping_pipeline import apply_giveaway_state, apply_priority, get_message_chat_type

    ref = parse_message_link(raw_link)
    if ref is None:
        return ManualWinResult(False, "❌ Нужна ссылка на пост: `https://t.me/канал/123`.")
    accounts, unknown = parse_accounts(account_args, state.ping_usernames)
    if unknown:
        return ManualWinResult(False, "❌ Не отслеживаются: " + ", ".join(f"@{u}" for u in unknown))

    client, message = await _fetch(ref)
    base = None
    if message is not None:
        base = await message_to_record(client, message, state.ping_regex, state.ping_usernames,
                                       require_mentions=False, tracked_ids=state.ping_user_ids or None)
        try:
            (base or {})["chat_type"] = await get_message_chat_type(client, message)
        except Exception:
            pass
    record = build_manual_record(ref, base, accounts)
    if not record["mentions"]:
        return ManualWinResult(
            False,
            "❌ В посте нет отслеживаемого аккаунта — укажите, кто выиграл: "
            "`/win <ссылка> @аккаунт`.",
        )
    apply_giveaway_state(record)
    if record.get("giveaway_status") == "missed":
        record["giveaway_status"] = ""
    apply_priority(record)
    record["action_status"] = "claim_prize"

    existing = await get_ping_by_message_ref(record.get("chat_id"), record.get("message_id"))
    ping_id = await save_ping(record)
    if not ping_id:
        return ManualWinResult(False, "❌ Не удалось сохранить запись.")
    # save_ping's update branch keeps the old statuses — a manual win is a debt.
    await update_ping_meta(int(ping_id), giveaway_status="", action_status="claim_prize")
    # The owner typed it in: no 🏆 card is owed for it (see ping_notify).
    from database import mark_ping_notified

    await mark_ping_notified(int(ping_id), win=True)
    from .analytics import invalidate_analytics_cache
    from .ping_pipeline import link_win_copies

    await link_win_copies(int(ping_id))
    invalidate_analytics_cache()
    await record_app_event("INFO", "manual", "Win recorded manually", {
        "ping_id": ping_id, "link": record["link"], "accounts": record["mentions"],
        "read": message is not None, "existed": existing is not None,
    })
    where = "текст прочитан" if message is not None else "пост недоступен аккаунтам — записан по ссылке"
    verb = "обновлена" if existing else "добавлена"
    return ManualWinResult(
        True,
        f"🏆 Победа {verb}: {', '.join('@' + a for a in record['mentions'])}\n"
        f"💬 {record['chat']} · __{where}__",
        int(ping_id),
    )
