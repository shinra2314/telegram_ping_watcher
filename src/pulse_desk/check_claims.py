"""Wallet-bot checks posted in chats: what is one, whose is it (pure rules).

A check from xRocket or CryptoBot is a deep link — ``t.me/<bot>?start=<code>`` —
usually on a URL button «Получить 0.1 USDT» under a message sent through the
bot's inline mode. Pressing that button is ``messages.startBot`` with the code,
which is all :mod:`check_claimer` does. Everything here decides *whether* to:
which links are checks, how much, for whom, and whether one of our own accounts
made it — the owner's rule (23.09.2026) is that those are never claimed.

No Telethon I/O: a message is read through its attributes only, so the rules
unit-test against plain stand-ins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

# Username in the link (lowercase) -> canonical bot key. CryptoBot answers at
# both @send (its inline name, the one on checks) and @CryptoBot.
BOT_ALIASES = {"xrocket": "xrocket", "send": "send", "cryptobot": "send"}
# Where startBot goes, per canonical key.
BOT_USERNAMES = {"xrocket": "xrocket", "send": "send"}
BOT_LABELS = {"xrocket": "🚀 xRocket", "send": "👛 CryptoBot"}

# Past this a check is history: the morning catch-up replays posts from the
# night, and every one of those is long claimed.
MAX_AGE_SECONDS = 30 * 60
# A personal check («для @наш») waits for its addressee — nobody else can take
# it — so the one sent at 3 a.m. while the PC was off is still worth a press.
PERSONAL_MAX_AGE_SECONDS = 7 * 24 * 3600

MODES = ("claim", "watch", "off")
DEFAULT_MODE = "claim"

LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/([A-Za-z0-9_]{4,32})\?start=([A-Za-z0-9_-]{4,128})",
    re.IGNORECASE,
)
# Any link to a wallet bot — to notice a post whose check link LINK_RE cannot read.
WALLET_LINK_RE = re.compile(r"(?:t|telegram)\.(?:me|dog)/(?:xrocket|send|cryptobot)(?![A-Za-z0-9_])", re.IGNORECASE)
_ANY_URL_RE = re.compile(r"(?:https?://|(?:t|telegram)\.(?:me|dog)/)\S+", re.IGNORECASE)
# The noun, verb-safe: «чек», «чеки», «мультичек», not «чекать» / «человечек».
CHECK_WORD_RE = re.compile(
    r"(?<![а-яёa-z])(?:мульти)?чек(?:и|а|у|ом|ов|ами)?(?![а-яёa-z])|cheque",
    re.IGNORECASE,
)
CLAIM_LABEL_RE = re.compile(r"получить|забрать|активир|receive|claim|activate", re.IGNORECASE)
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])")
ADDRESSEE_RE = re.compile(r"(?:для|for)\s+@([A-Za-z0-9_]{4,32})", re.IGNORECASE)
PASSWORD_RE = re.compile(
    r"(?:парол[ьея]|password)\s*[:：=—–-]\s*[«\"'`]?([^\s«»\"'`]{1,64})"
    r"|🔑\s*[:：=—–-]?\s*[«\"'`]?([^\s«»\"'`]{1,64})",
    re.IGNORECASE,
)

# The bot's answer to startBot, first match wins. Order matters: «чек уже
# активирован» must be `gone` before «активирован» can read as a success, and a
# success with an ad for the bot's channel must not read as «subscribe».
_OUTCOME_RULES = [
    ("own", r"сво(?:й|его|ему|ём)\s+(?:собственн\w+\s+)?чек|your\s+own\s+(?:check|cheque)"),
    ("not_for_you", r"не\s+для\s+вас|предназначен\w*\s+(?:для\s+)?друг|другому\s+пользовател"
                    r"|not\s+(?:meant\s+|intended\s+)?for\s+you|for\s+another\s+user"),
    ("gone", r"уже\s+(?:был\w*\s+)?(?:активир|получ|использ|забра)|не\s+найден|недействител|истёк|истек"
             r"|законч|исчерпан|активаций\s+(?:больше\s+)?нет|разобран|удал[её]н"
             r"|already\s+(?:been\s+)?(?:activated|claimed|used|received)|not\s+found|invalid|expired"
             r"|no\s+(?:more\s+)?activations"),
    ("claimed", r"вы\s+(?:успешно\s+)?(?:получил|активировал)|получил[аи]?\s+\+?\d|получено(?![а-яё])"
                r"|зачислен|успешно\s+(?:активир|получ|зачисл)"
                r"|you(?:'ve|\s+have)?\s+(?:successfully\s+)?(?:received|got|claimed|activated)"
                r"|successfully\s+(?:activated|claimed|received)"),
    ("premium", r"premium|премиум"),
    ("captcha", r"капч|captcha|не\s+робот|not\s+a\s+robot"),
    ("password", r"парол|password"),
    ("subscribe", r"подпиш|подписат|subscribe|вступите"),
]
_OUTCOME_RES = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _OUTCOME_RULES]

OUTCOME_LABELS = {
    "claimed": "✅ забрал",
    "gone": "⌛ не успел",
    "not_for_you": "🚫 не нам",
    "own": "🙈 свой чек",
    "premium": "💎 только Premium",
    "captcha": "🧩 капча",
    "password": "🔑 пароль",
    "subscribe": "📢 подписка",
    "unknown": "❔ непонятный ответ",
    "error": "⚠️ ошибка",
    "watch": "👀 поймал бы",
}
# Outcomes that end the attempt with nothing for a human to do.
FINAL_OUTCOMES = {"claimed", "gone", "not_for_you", "own", "premium", "error"}
# What the bot answered that only a person can get past.
NEEDS_HAND = {"captcha", "password", "unknown"}

# Joining for a check: at most this many channels, and never a bot.
MAX_JOINS = 3
_WALLET_USERNAMES = {"xrocket", "send", "cryptobot", "wallet"}
JOIN_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/"
    r"(?:(?:joinchat/|\+)([A-Za-z0-9_-]{6,})|([A-Za-z][A-Za-z0-9_]{3,31}))/?(?:[?#].*)?$",
    re.IGNORECASE,
)
RECHECK_LABEL_RE = re.compile(
    r"провер|подписал|готово|продолж|получить|активир|check|done|continue|receive|claim",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CheckLink:
    bot: str  # canonical key: "xrocket" / "send"
    code: str
    label: str = ""  # text of the button it came from

    @property
    def key(self) -> str:
        return f"{self.bot}|{self.code}"


@dataclass
class CheckInfo:
    links: list[CheckLink] = field(default_factory=list)
    amount: str = ""  # «0.1 USDT», "" when the post does not say
    addressee: str = ""  # lowercase username without @; "" = anyone
    password: str = ""  # written in the post, for checks behind one


def _text(message: Any) -> str:
    return getattr(message, "raw_text", None) or getattr(message, "message", None) or ""


def message_links(message: Any) -> list[tuple[str, str]]:
    """(url, label) pairs: URL buttons, then hidden text links, then plain text."""
    found: list[tuple[str, str]] = []
    markup = getattr(message, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if isinstance(url, str) and url:
                found.append((url, str(getattr(button, "text", "") or "")))
    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if isinstance(url, str) and url:
            found.append((url, ""))
    found.extend((match.group(0), "") for match in LINK_RE.finditer(_text(message)))
    return found


def code_is_check(bot: str, code: str) -> bool:
    """CryptoBot checks are ``CQ…`` (its invoices ``IV…``); xRocket invoices ``inv…``."""
    if bot == "send":
        return code.startswith("CQ")
    if bot == "xrocket":
        return not code.lower().startswith("inv")
    return False


def parse_amount(sources: Iterable[str]) -> str:
    """«0.1 USDT» from the first source that names one; links are ignored."""
    for source in sources:
        clean = _ANY_URL_RE.sub(" ", source or "")
        match = AMOUNT_RE.search(clean)
        if match:
            return f"{match.group(1).replace(',', '.')} {match.group(2)}"
    return ""


def addressee(text: str) -> str:
    match = ADDRESSEE_RE.search(text or "")
    return match.group(1).lower() if match else ""


def post_password(text: str) -> str:
    match = PASSWORD_RE.search(text or "")
    return (match.group(1) or match.group(2) or "") if match else ""


def find_check(message: Any) -> Optional[CheckInfo]:
    """The checks a message offers, or None when it is not a check post.

    A link alone is not enough — wallet bots have referral links too — so the
    post must also read like a check: the word «чек» or a «Получить…» button.
    """
    pairs = message_links(message)
    if not pairs:
        return None
    links: list[CheckLink] = []
    seen: set[str] = set()
    for url, label in pairs:
        match = LINK_RE.search(url)
        if not match:
            continue
        bot = BOT_ALIASES.get(match.group(1).lower())
        code = match.group(2)
        if not bot or not code_is_check(bot, code):
            continue
        link = CheckLink(bot, code, label)
        if link.key in seen:
            continue
        seen.add(link.key)
        links.append(link)
    if not links:
        return None
    text = _text(message)
    labels = [link.label for link in links if link.label]
    if not (CHECK_WORD_RE.search(text) or any(CLAIM_LABEL_RE.search(label) for label in labels)):
        return None
    return CheckInfo(
        links=links,
        amount=parse_amount([*labels, text]),
        addressee=addressee(text),
        password=post_password(text),
    )


def own_sender(sender_id: Optional[int], out: bool, own_user_ids: set[int], own_admin_chats: set[int]) -> bool:
    """True when one of our accounts posted it.

    ``out`` is per receiving account: its own message, also when it posted as a
    channel or as an anonymous admin. The others see such a post as sent by the
    chat itself — hence the admin-chat set (a chat only its admins post as).
    """
    if out:
        return True
    if sender_id is None:
        return False
    if sender_id in own_user_ids:
        return True
    return sender_id < 0 and sender_id in own_admin_chats


def classify_reply(text: str) -> str:
    for name, regex in _OUTCOME_RES:
        if regex.search(text or ""):
            return name
    return "unknown"


def join_targets(urls: Iterable[str]) -> list[tuple[str, str]]:
    """Channels a bot asks to join: ("public", username) / ("invite", hash)."""
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url in urls:
        match = JOIN_RE.match((url or "").strip())
        if not match:
            continue
        if match.group(1):
            target = ("invite", match.group(1))
        else:
            name = match.group(2)
            if name.lower() in _WALLET_USERNAMES or name.lower().endswith("bot"):
                continue
            target = ("public", name)
        if target[1].lower() in seen:
            continue
        seen.add(target[1].lower())
        targets.append(target)
        if len(targets) >= MAX_JOINS:
            break
    return targets


def recheck_button(rows: Any) -> Optional[tuple[int, int]]:
    """(row, column) of the bot's «проверить подписку»-style callback button."""
    for i, row in enumerate(rows or []):
        for j, button in enumerate(row or []):
            if getattr(button, "data", None) is not None and RECHECK_LABEL_RE.search(getattr(button, "text", "") or ""):
                return i, j
    return None


def normalize_config(raw: Any) -> dict[str, Any]:
    """``{"mode": claim|watch|off, "disabled": [session…]}``; absent → claim, all."""
    cfg = raw if isinstance(raw, dict) else {}
    mode = cfg.get("mode") if cfg.get("mode") in MODES else DEFAULT_MODE
    disabled = sorted({str(name).strip() for name in cfg.get("disabled") or [] if str(name).strip()})
    return {"mode": mode, "disabled": disabled}


def is_fresh(posted: Optional[datetime], now: Optional[datetime] = None, *, personal: bool = False) -> bool:
    if posted is None:
        return True
    now = now or datetime.now(timezone.utc)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    limit = PERSONAL_MAX_AGE_SECONDS if personal else MAX_AGE_SECONDS
    return (now - posted).total_seconds() <= limit


def amount_totals(amounts: Iterable[str]) -> dict[str, str]:
    """Sum «0.1 USDT»-style amounts per currency; anything unreadable is skipped."""
    totals: dict[str, Decimal] = {}
    for amount in amounts:
        parts = (amount or "").split()
        if len(parts) != 2:
            continue
        try:
            value = Decimal(parts[0])
        except InvalidOperation:
            continue
        totals[parts[1]] = totals.get(parts[1], Decimal(0)) + value
    return {currency: format(value.normalize(), "f") for currency, value in totals.items()}
