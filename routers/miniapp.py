"""The Mini App: its page and the JSON it reads.

Mounted on a SEPARATE ASGI app (see src/pulse_desk/miniapp_server.py), because
the tunnel that publishes it forwards a whole origin. Nothing else lives on
that port.

Every JSON route authenticates the caller by verifying the ``initData`` that
Telegram handed the page, then resolves role and grants through
``bot_membership`` — the same rules the bot's own handlers use, so a key that
cannot see giveaways in the bot cannot see them here either.

The feed reuses ``GiveawayFilter`` rather than re-deriving the sort mapping, so
the panel and the bot's ``gw:f:`` callbacks can never drift apart.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

import database
from pulse_desk.app_ctx import BOT_TOKEN, settings, state
from pulse_desk.bot.views import ALL_ACCOUNTS, GiveawayFilter
from pulse_desk.bot_membership import resolve_member_access
from pulse_desk.bot_permissions import account_mentioned, accounts_allowed, has_feature
from pulse_desk.converter import CRYPTO, FIAT, convert, fmt_amount, fmt_money, snapshot_time
from pulse_desk.miniapp_auth import InitDataError, verify

router = APIRouter()

APP_DIR = Path(settings.static_dir) / "app"
FEED_PAGE_SIZE = 8
# Fiat lines under the coin list on the rates screen.
FIAT_LINES = ("UAH", "EUR", "PLN", "RUB")


class Caller:
    """The authenticated Mini App user: their Telegram id, role and grants."""

    def __init__(self, tg_id: int, role: str, perms: dict, name: str):
        self.tg_id = tg_id
        self.role = role
        self.perms = perms
        self.name = name

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may(self, feature: str) -> bool:
        return self.is_admin or has_feature(self.perms, feature)

    def require(self, feature: str) -> None:
        if not self.may(feature):
            raise HTTPException(status_code=403, detail="Этот раздел вам не открыт")


async def current_caller(
    x_telegram_init_data: Optional[str] = Header(default=None),
) -> Caller:
    """Resolve the caller from the initData header, or refuse.

    Verified on every request rather than exchanged for a session: HMAC is
    cheap, and a stateless check cannot expire under a panel left open on a
    phone in a way the page has no way to notice.
    """
    try:
        user = verify(x_telegram_init_data or "", BOT_TOKEN)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail="Откройте панель заново из бота") from exc
    role, perms = await resolve_member_access(user.tg_id)
    if role is None:
        raise HTTPException(status_code=403, detail="Доступ к боту закрыт")
    return Caller(user.tg_id, role, perms, user.first_name or user.username)


def visible_accounts(caller: Caller) -> list[str]:
    """Tracked usernames this caller may see, '@' stripped.

    Mirrors ``giveaway_accounts`` in bot/service.py, order included — the feed
    addresses an account by its index into this list, so render and click have
    to rebuild it identically.
    """
    names = [name.lstrip("@") for name in (state.ping_usernames or [])]
    whitelist = {n.lower() for n in (caller.perms.get("accounts") or [])}
    return [n for n in names if not whitelist or n.lower() in whitelist]


async def _need_action(caller: Caller, filt: GiveawayFilter, limit: int) -> list[dict[str, Any]]:
    """The 'needs action' bucket, narrowed to what this caller may see."""
    board = await database.get_giveaway_board(limit=limit, sort=filt.db_sort)
    rows = [
        row for row in (board.get("buckets") or {}).get("need_action") or []
        if accounts_allowed(caller.perms, row.get("mentions"))
    ]
    if filt.wins:
        rows = [r for r in rows if r.get("is_win")]
    accounts = visible_accounts(caller)
    if 0 <= filt.account < len(accounts):
        picked = accounts[filt.account]
        rows = [r for r in rows if account_mentioned(r.get("mentions"), picked)]
    return rows


def _feed_row(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "chat": row.get("chat") or "?",
        "detected_at": row.get("detected_at"),
        "date": row.get("date"),
        "is_win": bool(row.get("is_win")),
        "deleted": bool(row.get("deleted_at")),
        "priority": row.get("priority_label"),
        "link": row.get("link"),
    }


@router.get("/")
@router.get("/app")
async def app_page() -> FileResponse:
    """The Mini App shell. Unauthenticated on purpose: the HTML holds no data."""
    return FileResponse(APP_DIR / "index.html")


@router.get("/api/app/home")
async def home(caller: Caller = Depends(current_caller)) -> dict:
    """Counters for the home cards, plus which cards the caller may open."""
    rows: list[dict[str, Any]] = []
    if caller.may("giveaways"):
        rows = await _need_action(caller, GiveawayFilter(), limit=400)
    return {
        "name": caller.name,
        "role": caller.role,
        "sections": {
            "giveaways": caller.may("giveaways"),
            "market": caller.may("market"),
        },
        "counters": {
            "giveaways": len(rows),
            "wins": len([r for r in rows if r.get("is_win")]),
        },
    }


@router.get("/api/app/giveaways")
async def giveaways(
    caller: Caller = Depends(current_caller),
    sort: str = Query("d", pattern="^[dp]$"),
    wins: bool = Query(False),
    account: int = Query(ALL_ACCOUNTS),
    page: int = Query(1, ge=1),
) -> dict:
    """The giveaway feed, filtered exactly as the bot's ``gw:f:`` callback does.

    ``account`` is an index into ``visible_accounts`` (-1 for all), matching
    ``GiveawayFilter``. Any active filter eats rows, so the board window is
    widened whenever one is set — an account-scoped key always has one.
    """
    caller.require("giveaways")
    filt = GiveawayFilter(sort=sort, wins=wins, account=account, page=page)
    accounts = visible_accounts(caller)
    narrowed = bool(caller.perms.get("accounts")) or wins or 0 <= account < len(accounts)
    window = FEED_PAGE_SIZE * page + 1
    rows = await _need_action(caller, filt, limit=window * (8 if narrowed else 1))
    start = FEED_PAGE_SIZE * (page - 1)
    slice_ = rows[start:start + FEED_PAGE_SIZE + 1]
    return {
        "items": [_feed_row(r) for r in slice_[:FEED_PAGE_SIZE]],
        "has_more": len(slice_) > FEED_PAGE_SIZE,
        "page": page,
        "total": len(rows),
        "accounts": accounts,
        "account": account,
        "sort": sort,
        "wins": wins,
    }


@router.get("/api/app/giveaways/{ping_id}")
async def giveaway_card(ping_id: int, caller: Caller = Depends(current_caller)) -> dict:
    """One giveaway, refused when it names an account the caller was not granted."""
    caller.require("giveaways")
    row = await database.get_ping_by_id(ping_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if not accounts_allowed(caller.perms, row.get("mentions")):
        raise HTTPException(status_code=403, detail="Эта запись не про ваш аккаунт")
    card = _feed_row(row)
    card["text"] = row.get("text") or ""
    card["sender"] = row.get("sender")
    return card


async def _latest_snapshot() -> Optional[dict[str, Any]]:
    history = await database.get_market_history(limit=1)
    return history[0] if history else None


@router.get("/api/app/market")
async def market(caller: Caller = Depends(current_caller)) -> dict:
    """The newest stored snapshot, formatted for the rates screen — no network.

    The snapshot is a flat ``{coingecko_id: {usd, usd_24h_change, …}}`` map with
    a ``_fiat`` block alongside, which is why the coin quote is read off the
    snapshot itself rather than a nested 'prices' key.
    """
    caller.require("market")
    snapshot = await _latest_snapshot()
    if not snapshot:
        return {"updated": "", "coins": [], "fiat": []}
    coins = []
    for code, (coingecko_id, emoji) in CRYPTO.items():
        quote = snapshot.get(coingecko_id) or {}
        usd = quote.get("usd")
        if usd is None:
            continue
        coins.append({
            "code": code,
            "emoji": emoji,
            "usd_text": fmt_money(float(usd), "USD"),
            "change": quote.get("usd_24h_change"),
        })
    fiat = []
    for code in FIAT_LINES:
        value = convert(snapshot, 1.0, "USD", code)
        if value is not None:
            fiat.append({"code": code, "flag": FIAT[code][1], "text": fmt_money(value, code)})
    return {"updated": snapshot_time(snapshot), "coins": coins, "fiat": fiat}


@router.get("/api/app/convert")
async def convert_pair(
    caller: Caller = Depends(current_caller),
    amount: float = Query(1.0, gt=0),
    src: str = Query(..., min_length=2, max_length=8),
    dst: str = Query(..., min_length=2, max_length=8),
) -> dict:
    """Convert `amount` from `src` to `dst` off the newest snapshot — no network."""
    caller.require("market")
    snapshot = await _latest_snapshot()
    src_code, dst_code = src.upper(), dst.upper()
    value = convert(snapshot, amount, src_code, dst_code) if snapshot else None
    if value is None:
        raise HTTPException(status_code=422, detail="Такую пару пока не посчитать")
    return {
        "amount": amount,
        "src": src_code,
        "dst": dst_code,
        "value": value,
        "from_text": fmt_amount(amount, src_code),
        "to_text": fmt_money(value, dst_code),
        "updated": snapshot_time(snapshot),
    }
