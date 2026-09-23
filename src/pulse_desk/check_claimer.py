"""Presses wallet-bot checks for our accounts; the rules live in check_claims.

:func:`on_message` is called from every account's live handlers *before* the
shared dedupe — ``state.remember_message`` lets one account process a message
for all of them, while here each account decides for itself — and before the
ping pipeline, so a claim never waits on classification or SQLite. It only
schedules work and returns.

A claim is ``messages.startBot`` with the check's code (what the «Получить»
button does), then the wallet bot's answer is read from its private chat. The
answer is polled instead of awaited through ``client.conversation``, which
races Telethon's update dispatcher (found while automating @Stickers).

The answer decides the rest. A success is journaled and announced. A
subscription condition is met: the linked channels are joined and the bot's
«проверить» pressed. A password written in the post is typed in. A captcha is
the check author's "humans only" switch and is **not** solved here: it is
mirrored to the owner as a relay card whose buttons press the same buttons in
the wallet bot, from the account that hit it.
"""
from __future__ import annotations

import asyncio
import io
import secrets
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from . import check_claims as cc
from .app_ctx import ADMIN_ID, logger, state
from .common import record_app_event, start_background_task

SETTINGS_KEY = "check_claim"
# Chats any of our accounts administers, saved so a restart does not open a
# window before the first sweep lists the dialogs again.
ADMIN_CHATS_KEY = "check_claim_admin_chats"

REPLY_TIMEOUT_SECONDS = 12.0
CLICK_TIMEOUT_SECONDS = 8.0
POLL_FIRST_DELAY = 0.4
POLL_MAX_DELAY = 2.0
# A bot often answers in two messages (a notice, then the result).
SETTLE_SECONDS = 0.8
JOIN_PAUSE_SECONDS = 1.0
RELAY_TTL = timedelta(minutes=30)
# How long a check that asked for a password waits for its author's next post.
PASSWORD_WAIT = timedelta(minutes=10)
SEEN_LIMIT = 5000

_saved_admin_chats: list[int] = []
# (session, bot key) -> the wallet bot's input peer, filled by warm_up: the press
# then needs no lookup at all, not even the session file's entity table.
_bot_peers: dict[tuple[str, str], Any] = {}
# (session, bot key) -> (lock, the loop it belongs to); see _lock_for.
_bot_locks: dict[tuple[str, str], tuple[asyncio.Lock, Any]] = {}


def _lock_for(session: str, bot_key: str) -> asyncio.Lock:
    """One conversation at a time per account and wallet bot.

    Four checks for one account within five seconds (it happened, 23.09) would
    otherwise interleave in the bot's chat, and one check's reply could be read
    as another's. Personal checks lose nothing by waiting — nobody else can take
    them — and two general checks at the very same second are rare.
    """
    loop = asyncio.get_running_loop()
    entry = _bot_locks.get((session, bot_key))
    if entry is None or entry[1] is not loop:
        entry = (asyncio.Lock(), loop)
        _bot_locks[(session, bot_key)] = entry
    return entry[0]


# ---- bookkeeping ----------------------------------------------------------
def _first(seen: "OrderedDict[str, None]", key: str) -> bool:
    """True the first time ``key`` is seen; the dict stays bounded."""
    if key in seen:
        return False
    seen[key] = None
    while len(seen) > SEEN_LIMIT:
        seen.popitem(last=False)
    return True


def _config() -> dict[str, Any]:
    return state.check_claim_cfg or cc.normalize_config(None)


def _own_user_ids() -> set[int]:
    ids = set(state.connected_user_ids)
    if ADMIN_ID:
        ids.add(int(ADMIN_ID))
    return ids


def client_for(session: str) -> Any:
    for client in state.clients:
        if getattr(client, "_session_name_custom", "") == session:
            return client
    return None


def session_for_username(username: str) -> Optional[str]:
    """The online account whose live @username this is (names change; sessions do not)."""
    wanted = (username or "").lstrip("@").lower()
    for name, account in state.accounts_state.items():
        if account.get("status") == "online" and (account.get("username") or "").lower() == wanted:
            return name
    return None


def _account_label(session: str, account: Optional[dict]) -> str:
    account = account or {}
    if account.get("display"):
        return str(account["display"])
    return f"@{account['username']}" if account.get("username") else session


def _from_wallet_bot(message: Any) -> bool:
    if getattr(message, "sender_id", None) in state.check_bot_ids:
        return True
    sender = getattr(message, "sender", None)
    username = (getattr(sender, "username", "") or "").lower()
    return bool(getattr(sender, "bot", False)) and username in cc.BOT_ALIASES


# ---- entry point ----------------------------------------------------------
def on_message(client: Any, session: str, account: dict, message: Any, *, live: bool = True) -> None:
    """Look at one message for one account. Never raises, never awaits.

    ``live=False`` is the morning catch-up of unread mentions: what it replays
    may have been pressed before a restart, so those claims ask the journal first.
    """
    try:
        _dispatch(client, session, account, message, live)
    except Exception:
        logger.exception("Check claimer failed on message %s", getattr(message, "id", None))


def _dispatch(client: Any, session: str, account: dict, message: Any, live: bool = True) -> None:
    received = asyncio.get_running_loop().time()
    cfg = _config()
    if cfg["mode"] == "off":
        return
    _maybe_password(message)
    info = cc.find_check(message)
    if info is None:
        _note_unreadable(message)
        return
    if getattr(message, "is_private", False) and _from_wallet_bot(message):
        # The wallet bot's own chat: a check it shows before anyone met it in a
        # chat was just created by this account. A code already met in a chat
        # is the bot talking about a claim instead.
        for link in info.links:
            if link.key not in state.check_chat_codes:
                _first(state.own_check_codes, link.key)
        return
    ours = bool(info.addressee) and session_for_username(info.addressee) is not None
    if not cc.is_fresh(getattr(message, "date", None), personal=ours):
        return
    if info.dead:
        # The bot already wrote «активирован» on the post: nothing left to press.
        for link in info.links:
            _first(state.dead_check_codes, link.key)
        return
    sender_id = getattr(message, "sender_id", None)
    out = bool(getattr(message, "out", False))
    if cc.own_sender(sender_id, out, _own_user_ids(), state.own_admin_chat_ids) or any(
        link.key in state.own_check_codes for link in info.links
    ):
        for link in info.links:
            _first(state.own_check_codes, link.key)
            if _first(state.check_seen, f"own|{link.key}"):
                start_background_task(f"check-own:{link.key}", _journal_own(message, info, link))
        return
    for link in info.links:
        _first(state.check_chat_codes, link.key)
    if info.addressee:
        # A personal check names its taker: that account presses it, whichever
        # of ours happened to receive the post.
        target = session_for_username(info.addressee)
        target_client = client if target == session else client_for(target) if target else None
        if target_client is None:
            return
        _launch(target_client, target, state.accounts_state.get(target) or account, message, info, cfg, live, received)
        return
    _launch(client, session, account, message, info, cfg, live, received)
    # The first of ours to see a general check presses for all of them: an
    # account whose own copy of the update is late — or that is not in this chat
    # at all — does not wait for it, and still gets its share of a multi-check.
    for other_client, other_session, other_account in _online_accounts():
        if other_session != session:
            _launch(other_client, other_session, other_account, message, info, cfg, live, received)


def _online_accounts():
    for other in list(state.clients):
        name = getattr(other, "_session_name_custom", "")
        account = state.accounts_state.get(name) or {}
        if name and account.get("status") == "online":
            yield other, name, account


def _note_unreadable(message: Any) -> None:
    """A post with a wallet-bot button that did not read as a check: logged once.

    A Mini App link or a new code format would otherwise make the claimer go
    quiet with nothing in the log to say why.
    """
    if getattr(message, "reply_markup", None) is None:
        return
    links = [(label, url) for url, label in cc.message_links(message) if cc.WALLET_LINK_RE.search(url)]
    if links and _first(state.check_seen, f"unreadable|{getattr(message, 'chat_id', None)}|{getattr(message, 'id', None)}"):
        logger.info("Wallet-bot button not read as a check in %s/%s: %s | %r",
                    getattr(message, "chat_id", None), getattr(message, "id", None), links[:3],
                    cc._text(message)[:80])


def _launch(client: Any, session: str, account: dict, message: Any, info: cc.CheckInfo, cfg: dict,
            live: bool = True, received: Optional[float] = None) -> None:
    if session in cfg["disabled"]:
        return
    for link in info.links:
        if link.key in state.dead_check_codes:
            continue  # one of ours was already told «уже активирован»
        if cfg["mode"] == "watch":
            if _first(state.check_seen, f"watch|{link.key}"):
                start_background_task(f"check-watch:{link.key}", _announce_watch(message, info, link, session, account))
            continue
        if _first(state.check_seen, f"{session}|{link.key}"):
            start_background_task(f"check-claim:{session}:{link.key}",
                                  claim(client, session, account, message, info, link, live=live,
                                        received=received))


# ---- the claim --------------------------------------------------------------
async def claim(client: Any, session: str, account: dict, message: Any, info: cc.CheckInfo,
                link: cc.CheckLink, *, announce: bool = True, live: bool = True,
                received: Optional[float] = None) -> str:
    """Press one check from one account; returns the outcome.

    The press goes out before anything else — no lock, no lookup, no SQLite.
    Only what follows (reading the answer, a subscription, a password) waits
    its turn in the account's conversation with the bot.
    """
    if not live and await _tried_before(session, link):
        return "skipped"
    outcome, reply, action, bot, press_ms = "error", "", None, None, None
    loop = asyncio.get_running_loop()
    try:
        bot = await _bot_peer(client, session, link.bot)
        sent_id = await _press_start(client, bot, link.code)
        press_ms = int((loop.time() - (received if received is not None else loop.time())) * 1000)
        async with _lock_for(session, link.bot):
            replies = await _await_replies(client, bot, sent_id, link.code)
            outcome, reply, action = _judge(replies)
            if outcome == "subscribe":
                outcome, reply, action = await _subscribe(client, bot, link, replies, action)
            if outcome == "password" and info.password:
                outcome, reply, action = await _answer(client, bot, info.password)
    except Exception as exc:
        outcome, reply = "error", _error_text(exc)
        logger.warning("Check %s from %s failed: %s", link.key, session, exc)
    if outcome in DEAD_OUTCOMES:
        _first(state.dead_check_codes, link.key)
    ctx = await _context(message, info, link, session, account)
    ctx["press_ms"] = press_ms
    claim_id = await _journal(ctx, outcome, reply)
    logger.info("Check %s in %s by %s: %s (pressed %s ms after the update, post %s)",
                ctx["amount"] or link.code, ctx["chat"], ctx["account"], outcome, press_ms,
                _update_lag(message))
    if not announce:
        return outcome
    if outcome == "claimed":
        await _notify_claimed(ctx)
    elif outcome in cc.NEEDS_HAND and bot is not None and action is not None:
        if outcome == "password":
            _await_password(message, client, session, account, bot, link, ctx, claim_id)
        await open_relay(ctx, client, bot, action, reply, outcome, claim_id)
    return outcome


# ---- a password posted after the check ------------------------------------------
def _await_password(message: Any, client: Any, session: str, account: dict, bot: Any,
                    link: cc.CheckLink, ctx: dict, claim_id: int) -> None:
    """Remember a check stuck on «введите пароль»: its author often posts the
    password a moment later, as a separate message."""
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        return
    state.check_awaiting_password.setdefault(chat_id, []).append({
        "sender_id": getattr(message, "sender_id", None), "since": datetime.now(), "client": client,
        "session": session, "account": account, "bot": bot, "link": link, "ctx": ctx, "claim_id": claim_id,
    })


def _maybe_password(message: Any) -> None:
    """A new post in a chat where a check waits for its password, by the check's author."""
    waiting = state.check_awaiting_password.get(getattr(message, "chat_id", None))
    if not waiting:
        return
    now = datetime.now()
    waiting[:] = [entry for entry in waiting if now - entry["since"] <= PASSWORD_WAIT]
    sender = getattr(message, "sender_id", None)
    mine = [entry for entry in waiting if entry["sender_id"] == sender]
    password = cc.follow_up_password(cc._text(message)) if mine else ""
    if not password:
        return
    # Every account gets the same update: the password is typed once per waiting check.
    if not _first(state.check_seen, f"pw|{getattr(message, 'chat_id', None)}|{getattr(message, 'id', None)}"):
        return
    for entry in mine:
        waiting.remove(entry)
        start_background_task(f"check-password:{entry['session']}:{entry['link'].key}",
                              _type_password(entry, password))


async def _type_password(entry: dict, password: str) -> None:
    from database import update_check_claim

    client, bot, link, ctx = entry["client"], entry["bot"], entry["link"], entry["ctx"]
    try:
        async with _lock_for(entry["session"], link.bot):
            outcome, reply, _action = await _answer(client, bot, password)
    except Exception as exc:
        outcome, reply = "error", _error_text(exc)
    if outcome in DEAD_OUTCOMES:
        _first(state.dead_check_codes, link.key)
    if entry.get("claim_id"):
        with suppress(Exception):
            await update_check_claim(entry["claim_id"], outcome, reply)
    logger.info("Check %s: password from the next post typed by %s: %s", link.key, ctx["account"], outcome)
    if outcome == "claimed":
        await _notify_claimed(ctx)


async def _press_start(client: Any, bot: Any, code: str) -> Optional[int]:
    """Send startBot with the code; returns the id of our «/start <code>»."""
    from telethon.tl.functions.messages import StartBotRequest

    return _sent_id(await client(StartBotRequest(bot=bot, peer=bot, start_param=code)))


async def _start(client: Any, bot: Any, code: str) -> list:
    return await _await_replies(client, bot, await _press_start(client, bot, code), code)


async def _bot_peer(client: Any, session: str, bot_key: str) -> Any:
    peer = _bot_peers.get((session, bot_key))
    if peer is None:
        peer = await client.get_input_entity(cc.BOT_USERNAMES[bot_key])
        _bot_peers[(session, bot_key)] = peer
    return peer


def _update_lag(message: Any) -> str:
    """How old the post was when we got it (whole seconds: that is Telegram's resolution)."""
    from datetime import timezone

    posted = getattr(message, "date", None)
    if not isinstance(posted, datetime):
        return "?"
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return f"{(datetime.now(timezone.utc) - posted).total_seconds():.0f} s old"


async def _tried_before(session: str, link: cc.CheckLink) -> bool:
    from database import has_check_claim

    try:
        return await has_check_claim(session, link.bot, link.code)
    except Exception:
        return False


def _sent_id(result: Any) -> Optional[int]:
    """Id of our own «/start <code>» message, from the updates startBot returns."""
    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        if message is not None and getattr(message, "out", False) and isinstance(getattr(message, "id", None), int):
            return message.id
    for update in updates:
        if type(update).__name__ == "UpdateMessageID" and isinstance(getattr(update, "id", None), int):
            return update.id
    return None


def _incoming(batch: Any, base: Optional[int]) -> list:
    if base is None:
        return []  # without our own message older chatter cannot be told apart
    return sorted((m for m in batch or [] if not getattr(m, "out", False) and m.id > base), key=lambda m: m.id)


# Answers that mean the check is over for every one of our accounts. «Premium
# only» is not among them: some of ours have Premium.
DEAD_OUTCOMES = {"gone", "invoice"}

# Our messages further apart than this are not one burst of presses.
BURST_SECONDS = 20


def _replies_for(batch: Any, base: Optional[int]) -> list:
    """The bot's answer to our message ``base``.

    Presses go out at once, so two checks for one account can leave «/start A»,
    «/start B», answer A, answer B in the chat. A bot answers in order: within a
    burst of our messages with no answer between them, the k-th of ours gets the
    k-th answer after the burst. Alone, ours gets everything up to our next one.
    """
    if base is None:
        return []
    ordered = sorted(batch or [], key=lambda m: m.id)
    index = next((i for i, m in enumerate(ordered) if m.id == base), None)
    if index is None:
        return _incoming(batch, base)
    anchor = getattr(ordered[index], "date", None)

    def same_burst(m: Any) -> bool:
        when = getattr(m, "date", None)
        return (not isinstance(when, datetime) or not isinstance(anchor, datetime)
                or abs((when - anchor).total_seconds()) <= BURST_SECONDS)

    start = index
    while start > 0 and getattr(ordered[start - 1], "out", False) and same_burst(ordered[start - 1]):
        start -= 1
    end = index
    while end + 1 < len(ordered) and getattr(ordered[end + 1], "out", False) and same_burst(ordered[end + 1]):
        end += 1
    answers = []
    for m in ordered[end + 1:]:
        if getattr(m, "out", False):
            break
        answers.append(m)
    if start == end:
        return answers
    k = index - start
    return answers[k:k + 1]


def _own_message_id(batch: Any, marker: str) -> Optional[int]:
    ids = [m.id for m in batch or []
           if getattr(m, "out", False) and (not marker or marker in (getattr(m, "raw_text", "") or ""))]
    return max(ids) if ids else None


async def _await_replies(client: Any, bot: Any, after_id: Optional[int], marker: str = "",
                         timeout: Optional[float] = None) -> list:
    """The bot's messages after ours once they stop arriving; [] on silence."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (REPLY_TIMEOUT_SECONDS if timeout is None else timeout)
    delay, base = POLL_FIRST_DELAY, after_id
    while True:
        await asyncio.sleep(delay)
        batch = await client.get_messages(bot, limit=20)
        if base is None:
            base = _own_message_id(batch, marker)
        incoming = _replies_for(batch, base)
        if incoming:
            await asyncio.sleep(SETTLE_SECONDS)
            return _replies_for(await client.get_messages(bot, limit=20), base) or incoming
        if loop.time() >= deadline:
            return []
        delay = min(delay * 1.5, POLL_MAX_DELAY)


async def _await_change(client: Any, bot: Any, message: Any, old_text: str, old_edit: Any,
                        timeout: Optional[float] = None) -> list:
    """After a button press: new bot messages, or the pressed one edited in place."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (CLICK_TIMEOUT_SECONDS if timeout is None else timeout)
    delay = POLL_FIRST_DELAY
    while True:
        await asyncio.sleep(delay)
        newer = _incoming(await client.get_messages(bot, limit=10, min_id=message.id), message.id)
        if newer:
            await asyncio.sleep(SETTLE_SECONDS)
            return _incoming(await client.get_messages(bot, limit=10, min_id=message.id), message.id) or newer
        again = await client.get_messages(bot, ids=message.id)
        if again is not None and ((getattr(again, "raw_text", "") or "") != old_text
                                  or getattr(again, "edit_date", None) != old_edit):
            return [again]
        if loop.time() >= deadline:
            return []
        delay = min(delay * 1.5, POLL_MAX_DELAY)


def _judge(replies: list, extra: str = "") -> tuple[str, str, Any]:
    """(outcome, the bot's text, the message a next step would act on)."""
    parts = [extra] + [getattr(m, "raw_text", "") or "" for m in replies]
    text = "\n".join(part for part in parts if part).strip()
    if not text and not replies:
        return "error", "бот не ответил", None
    action = next((m for m in reversed(replies) if getattr(m, "buttons", None)), replies[-1] if replies else None)
    return cc.classify_reply(text), text, action


async def _press(client: Any, bot: Any, message: Any, row: int, column: int) -> tuple[str, str, Any]:
    old_text, old_edit = getattr(message, "raw_text", "") or "", getattr(message, "edit_date", None)
    answer = await message.click(row, column)
    alert = getattr(answer, "message", None)
    replies = await _await_change(client, bot, message, old_text, old_edit)
    return _judge(replies, extra=alert if isinstance(alert, str) else "")


async def _answer(client: Any, bot: Any, text: str) -> tuple[str, str, Any]:
    sent = await client.send_message(bot, text)
    return _judge(await _await_replies(client, bot, getattr(sent, "id", None), text))


async def _subscribe(client: Any, bot: Any, link: cc.CheckLink, replies: list, action: Any) -> tuple[str, str, Any]:
    """Join what the bot asks for (≤ 3 channels), then have it check again."""
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    text = "\n".join(getattr(m, "raw_text", "") or "" for m in replies)
    targets = cc.join_targets(url for m in replies for url, _label in cc.message_links(m))
    joined: list[str] = []
    for kind, value in targets:
        try:
            await client(ImportChatInviteRequest(value) if kind == "invite" else JoinChannelRequest(value))
            joined.append(value)
        except Exception as exc:
            if type(exc).__name__ == "UserAlreadyParticipantError":
                joined.append(value)
            else:
                logger.info("Check %s: could not join %s: %s", link.key, value, exc)
        await asyncio.sleep(JOIN_PAUSE_SECONDS)
    if not joined:
        return "subscribe", text, action
    await record_app_event("INFO", "checks", "Joined channels for a check", {"check": link.key, "channels": joined})
    position = cc.recheck_button(getattr(action, "buttons", None)) if action is not None else None
    if position is not None:
        return await _press(client, bot, action, *position)
    return _judge(await _start(client, bot, link.code))


def _error_text(exc: BaseException) -> str:
    seconds = getattr(exc, "seconds", None)
    if isinstance(seconds, int):
        return f"FloodWait {seconds} с"
    return (str(exc) or type(exc).__name__)[:300]


# ---- journal and cards --------------------------------------------------------
def _post_link(chat: Any, message: Any) -> str:
    from telegram_ping_watcher import build_message_link

    try:
        link = build_message_link(chat, message)
    except Exception:
        return ""
    return link if link.startswith("http") else ""


async def _context(message: Any, info: cc.CheckInfo, link: cc.CheckLink, session: str,
                   account: Optional[dict]) -> dict[str, Any]:
    from telegram_ping_watcher import display_name

    chat = getattr(message, "chat", None)
    if chat is None:
        with suppress(Exception):
            chat = await message.get_chat()
    return {
        "bot": link.bot,
        "code": link.code,
        "session": session,
        "account": _account_label(session, account) if session else "",
        "chat_id": getattr(message, "chat_id", None),
        "chat": display_name(chat) if chat is not None else str(getattr(message, "chat_id", "") or ""),
        "message_id": getattr(message, "id", None),
        "link": _post_link(chat, message),
        "amount": info.amount,
    }


async def _journal(ctx: dict[str, Any], outcome: str, reply: str) -> int:
    from database import record_check_claim

    try:
        return await record_check_claim({**ctx, "outcome": outcome, "reply": reply})
    except Exception:
        logger.warning("Could not journal check %s|%s", ctx.get("bot"), ctx.get("code"), exc_info=True)
        return 0


async def _journal_own(message: Any, info: cc.CheckInfo, link: cc.CheckLink) -> None:
    ctx = await _context(message, info, link, "", None)
    await _journal(ctx, "own", "")
    logger.info("Own check %s in %s left alone", ctx["amount"] or link.code, ctx["chat"])


def _where(ctx: dict[str, Any], account: str) -> str:
    who = f"👤 {account} · " if account else ""
    return f"{who}{cc.BOT_LABELS.get(ctx['bot'], ctx['bot'])}\n💬 {ctx['chat']}"


def _post_button(ctx: dict[str, Any]) -> Optional[list[list[Any]]]:
    from telethon import Button

    return [[Button.url("↗️ К посту", ctx["link"])]] if ctx.get("link") else None


async def _notify_claimed(ctx: dict[str, Any]) -> None:
    from .bot_notify import send_admin_bot_message

    speed = f"\n⚡ нажал через {ctx['press_ms']} мс" if ctx.get("press_ms") is not None else ""
    text = f"🧾 **Чек забран** · {ctx['amount'] or 'сумма не указана'}\n{_where(ctx, ctx['account'])}{speed}"
    with suppress(Exception):
        await send_admin_bot_message(text, buttons=_post_button(ctx))


async def _announce_watch(message: Any, info: cc.CheckInfo, link: cc.CheckLink, session: str,
                          account: Optional[dict]) -> None:
    from .bot_notify import send_admin_bot_message

    ctx = await _context(message, info, link, session, account)
    await _journal({**ctx, "session": ""}, "watch", "")
    text = (f"👀 **Поймал бы чек** · {ctx['amount'] or 'сумма не указана'}\n{_where(ctx, ctx['account'])}\n\n"
            "__Режим «только смотреть» — ⚙️ Настройки → 🧾 Чеки.__")
    with suppress(Exception):
        await send_admin_bot_message(text, buttons=_post_button(ctx), kind="system")


# ---- relay: the owner answers what only a person can --------------------------
_RELAY_HEADS = {
    "captcha": "🧩 **Чеку нужна капча**",
    "password": "🔑 **Чек просит пароль**",
}


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def relay_text(relay: dict[str, Any], reply: str, outcome: str, note: str = "") -> str:
    ctx, step = relay["ctx"], relay["step"]
    head = _RELAY_HEADS.get(outcome, "❔ **Бот чека ответил непонятно**")
    lines = [f"{head} · {ctx['amount'] or 'сумма не указана'}", _where(ctx, step["account"])]
    if note:
        lines += ["", note]
    if reply:
        lines += ["", "```", _clip(reply.replace("```", "'''"), 500), "```"]
    lines += ["", "__Нажмите ответ — скрипт нажмёт его от этого аккаунта. Текстом — «✍️ Ответить».__"]
    return "\n".join(lines)


def relay_buttons(relay: dict[str, Any], action: Any) -> list[list[Any]]:
    """The wallet bot's own buttons, mirrored, plus the relay's controls."""
    from telethon import Button

    token = relay["token"]
    rows: list[list[Any]] = []
    for i, row in enumerate(getattr(action, "buttons", None) or []):
        line = []
        for j, button in enumerate(row or []):
            label = (getattr(button, "text", "") or "·")[:40]
            if cc.money_out(label):
                continue  # a stray tap on the card must never pay from the account
            if getattr(button, "data", None) is not None:
                line.append(Button.inline(label, f"ck:b:{token}:{i}:{j}".encode()))
            elif getattr(button, "url", None):
                line.append(Button.url(label, button.url))
        if line:
            rows.append(line)
    rows = rows[:8]
    rows.append([Button.inline("✍️ Ответить", f"ck:t:{token}".encode()),
                 Button.inline("🔁 Повторить", f"ck:r:{token}".encode())])
    tail = []
    if relay["queue"]:
        tail.append(Button.inline(f"▶️ Следующий аккаунт ({len(relay['queue'])})", f"ck:n:{token}".encode()))
    if relay["ctx"].get("link"):
        tail.append(Button.url("↗️ К посту", relay["ctx"]["link"]))
    if tail:
        rows.append(tail)
    return rows


_DONE_HEADS = {
    "claimed": "🧾 **Чек забран**",
    "gone": "⌛ **Чек уже забрали**",
    "not_for_you": "🚫 **Чек не для этого аккаунта**",
    "own": "🙈 **Это свой чек**",
    "premium": "💎 **Чек только для Premium**",
    "invoice": "🧾 **Это счёт на оплату, не чек**",
    "subscribe": "📢 **Подписаться не вышло**",
    "error": "⚠️ **Не получилось**",
}
# Nothing more to try on this account: the card closes.
_SETTLED = {"claimed", "gone", "not_for_you", "own", "premium", "invoice"}


def relay_screen(relay: dict[str, Any], outcome: str, reply: str, action: Any, note: str = "") -> tuple[str, list]:
    """The card after an owner's answer: the bot's next question, or the result."""
    from telethon import Button

    if outcome in cc.NEEDS_HAND and action is not None:
        return relay_text(relay, reply, outcome, note), relay_buttons(relay, action)
    ctx, token = relay["ctx"], relay["token"]
    head = _DONE_HEADS.get(outcome, cc.OUTCOME_LABELS.get(outcome, outcome))
    lines = [f"{head} · {ctx['amount'] or 'сумма не указана'}", _where(ctx, relay["step"]["account"])]
    if note:
        lines += ["", note]
    if reply:
        lines += ["", "```", _clip(reply.replace("```", "'''"), 400), "```"]
    settled = outcome in _SETTLED
    if settled and not relay["queue"]:
        state.check_relays.pop(token, None)
    buttons: list[list[Any]] = []
    if not settled:
        buttons.append([Button.inline("🔁 Повторить", f"ck:r:{token}".encode())])
    if relay["queue"]:
        buttons.append([Button.inline(f"▶️ Следующий аккаунт ({len(relay['queue'])})", f"ck:n:{token}".encode())])
    if ctx.get("link"):
        buttons.append([Button.url("↗️ К посту", ctx["link"])])
    return "\n".join(lines), buttons


async def _picture(client: Any, action: Any) -> Optional[io.BytesIO]:
    """The captcha image, re-sent by our bot (the owner cannot see the account's chat)."""
    if action is None or getattr(action, "photo", None) is None:
        return None
    try:
        data = await client.download_media(action, file=bytes)
    except Exception:
        return None
    if not data:
        return None
    picture = io.BytesIO(data)
    picture.name = "captcha.jpg"
    return picture


async def open_relay(ctx: dict[str, Any], client: Any, bot: Any, action: Any, reply: str, outcome: str,
                     claim_id: int) -> str:
    """Hand the bot's question to the owner; returns the relay token.

    One card per check: another account stopping on the same check joins the
    card's queue and is offered by «▶️ Следующий аккаунт» afterwards.
    """
    step = {"session": ctx["session"], "account": ctx["account"], "msg_id": getattr(action, "id", None),
            "claim_id": claim_id, "outcome": outcome}
    key = f"{ctx['bot']}|{ctx['code']}"
    for relay in state.check_relays.values():
        if relay["key"] == key:
            relay["queue"].append(step)
            return relay["token"]
    token = secrets.token_hex(4)
    relay = {"token": token, "key": key, "ctx": ctx, "step": step, "queue": [], "created_at": datetime.now()}
    state.check_relays[token] = relay
    await send_relay_card(relay, client, action, reply, outcome)
    return token


async def send_relay_card(relay: dict[str, Any], client: Any, action: Any, reply: str, outcome: str) -> None:
    from .bot_notify import send_admin_bot_message

    picture = await _picture(client, action)
    text = relay_text(relay, _clip(reply, 300) if picture else reply, outcome)
    with suppress(Exception):
        await send_admin_bot_message(text, buttons=relay_buttons(relay, action), file=picture)


async def _relay_target(relay: dict[str, Any]) -> tuple[Any, Any]:
    client = client_for(relay["step"]["session"])
    if client is None:
        raise RuntimeError("аккаунт не в сети")
    return client, await client.get_input_entity(cc.BOT_USERNAMES[relay["ctx"]["bot"]])


async def _relay_settle(relay: dict[str, Any], client: Any, bot: Any, outcome: str, reply: str,
                        action: Any) -> tuple[str, str, Any]:
    from database import update_check_claim

    step = relay["step"]
    if outcome == "subscribe" and action is not None:
        link = cc.CheckLink(relay["ctx"]["bot"], relay["ctx"]["code"])
        outcome, reply, action = await _subscribe(client, bot, link, [action], action)
    if action is not None and isinstance(getattr(action, "id", None), int):
        step["msg_id"] = action.id
    step["outcome"] = outcome
    if outcome in DEAD_OUTCOMES:
        _first(state.dead_check_codes, relay["key"])
    if step.get("claim_id"):
        with suppress(Exception):
            await update_check_claim(step["claim_id"], outcome, reply)
    return outcome, reply, action


async def relay_press(token: str, row: int, column: int) -> tuple[str, str, Any]:
    """Press button (row, column) of the wallet bot's message, as the owner chose."""
    relay = state.check_relays[token]
    client, bot = await _relay_target(relay)
    async with _lock_for(relay["step"]["session"], relay["ctx"]["bot"]):
        message = await client.get_messages(bot, ids=relay["step"]["msg_id"])
        if message is None:
            raise RuntimeError("сообщение бота пропало")
        rows = getattr(message, "buttons", None) or []
        if not (0 <= row < len(rows) and 0 <= column < len(rows[row] or [])):
            raise RuntimeError("у бота уже другие кнопки — нажмите «🔁 Повторить»")
        if cc.money_out(getattr(rows[row][column], "text", "")):
            # The card never shows such a button; a forged callback gets no further.
            raise RuntimeError("кнопки оплаты и перевода скрипт не нажимает")
        outcome, reply, action = await _press(client, bot, message, row, column)
        return await _relay_settle(relay, client, bot, outcome, reply, action)


async def relay_answer(token: str, text: str) -> tuple[str, str, Any]:
    """Send the owner's typed answer (a password, a captcha code) from the account."""
    relay = state.check_relays[token]
    client, bot = await _relay_target(relay)
    async with _lock_for(relay["step"]["session"], relay["ctx"]["bot"]):
        outcome, reply, action = await _answer(client, bot, text)
        return await _relay_settle(relay, client, bot, outcome, reply, action)


async def relay_retry(token: str) -> tuple[str, str, Any]:
    """Start the check again from the same account (a fresh captcha, a finished onboarding)."""
    relay = state.check_relays[token]
    client, bot = await _relay_target(relay)
    async with _lock_for(relay["step"]["session"], relay["ctx"]["bot"]):
        outcome, reply, action = _judge(await _start(client, bot, relay["ctx"]["code"]))
        return await _relay_settle(relay, client, bot, outcome, reply, action)


async def relay_next(token: str) -> Optional[dict[str, Any]]:
    """Move the card on to the next account stopped on this check; sends its card."""
    relay = state.check_relays.get(token)
    if relay is None or not relay["queue"]:
        return None
    state.check_relays.pop(token, None)
    step = relay["queue"].pop(0)
    fresh = {**relay, "token": secrets.token_hex(4), "step": step, "created_at": datetime.now()}
    state.check_relays[fresh["token"]] = fresh
    client, bot = await _relay_target(fresh)
    action = await client.get_messages(bot, ids=step["msg_id"]) if step.get("msg_id") else None
    await send_relay_card(fresh, client, action, getattr(action, "raw_text", "") or "", step["outcome"])
    return fresh


def sweep_relays(now: Optional[datetime] = None) -> int:
    now = now or datetime.now()
    for chat_id, waiting in list(state.check_awaiting_password.items()):
        waiting[:] = [entry for entry in waiting if now - entry["since"] <= PASSWORD_WAIT]
        if not waiting:
            state.check_awaiting_password.pop(chat_id, None)
    stale = [token for token, relay in state.check_relays.items() if now - relay["created_at"] > RELAY_TTL]
    for token in stale:
        state.check_relays.pop(token, None)
    return len(stale)


# ---- setup ------------------------------------------------------------------
async def warm_up(client: Any, session: str) -> None:
    """Resolve the wallet bots once per account: the first claim then needs no lookup,
    and the bots' private chats are recognised by id."""
    for key, username in cc.BOT_USERNAMES.items():
        try:
            peer = await client.get_input_entity(username)
        except Exception as exc:
            logger.debug("Could not resolve @%s for %s: %s", username, session, exc)
            continue
        _bot_peers[(session, key)] = peer
        user_id = getattr(peer, "user_id", None)
        if isinstance(user_id, int):
            state.check_bot_ids[user_id] = key


def note_dialog(entity: Any) -> None:
    """Remember a chat one of our accounts creates or administers (from the dialog list)."""
    if not (getattr(entity, "creator", False) or getattr(entity, "admin_rights", None)):
        return
    try:
        from telethon import utils

        state.own_admin_chat_ids.add(int(utils.get_peer_id(entity)))
    except Exception:
        logger.debug("Could not note admin chat %r", entity, exc_info=True)


async def persist_admin_chats() -> None:
    from database import set_setting

    current = sorted(state.own_admin_chat_ids)
    if current == _saved_admin_chats:
        return
    try:
        await set_setting(ADMIN_CHATS_KEY, current, audit=False)
    except Exception:
        logger.debug("Could not save admin chats", exc_info=True)
        return
    _saved_admin_chats[:] = current


async def load() -> None:
    from database import get_check_attempts, get_setting

    state.check_claim_cfg = cc.normalize_config(await get_setting(SETTINGS_KEY, None))
    # What was pressed before the restart stays pressed: the in-memory marks are
    # gone, and xRocket keeps editing old posts (every edit is a fresh update).
    since = (datetime.now() - timedelta(seconds=cc.PERSONAL_MAX_AGE_SECONDS)).replace(microsecond=0).isoformat()
    try:
        attempts = await get_check_attempts(since)
    except Exception:
        attempts = []
        logger.warning("Could not read past check attempts", exc_info=True)
    for row in attempts:
        key = f"{row['bot']}|{row['code']}"
        if row["session"]:
            _first(state.check_seen, f"{row['session']}|{key}")
        elif row["outcome"] == "own":
            _first(state.own_check_codes, key)
            _first(state.check_seen, f"own|{key}")
        if row["outcome"] in DEAD_OUTCOMES:
            _first(state.dead_check_codes, key)
    saved = await get_setting(ADMIN_CHATS_KEY, None)
    for value in saved if isinstance(saved, list) else []:
        with suppress(TypeError, ValueError):
            state.own_admin_chat_ids.add(int(value))
    _saved_admin_chats[:] = sorted(state.own_admin_chat_ids)


async def save(cfg: dict[str, Any]) -> dict[str, Any]:
    """Save and apply at once: a mode the live handlers do not see yet is a lie."""
    from database import set_setting

    cfg = cc.normalize_config(cfg)
    await set_setting(SETTINGS_KEY, cfg)
    state.check_claim_cfg = cfg
    return cfg
