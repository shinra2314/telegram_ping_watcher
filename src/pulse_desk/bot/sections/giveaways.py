"""Лента розыгрышей: фильтры, выбор аккаунта, карточка.

Всё состояние списка едет в самой кнопке (``GiveawayFilter`` → ``gw:f:…``,
≤64 байт), поэтому сервер ничего не помнит между нажатиями, а ⬅️ из карточки
возвращает ровно на тот же экран. Аккаунт адресуется **индексом** в списке,
который отрисовка и клик обязаны собрать одинаково — отсюда один
``visible_accounts`` на оба пути.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from database import (
    get_giveaway_board, get_ping_by_id, giveaway_account_counts,
)

from ... import watch_settings as ws
from ...app_ctx import state
from ...bot_permissions import account_mentioned, accounts_allowed, full_permissions
from ...giveaway_ops import GiveawayActionError, analyze, cleanup_candidates, leave_channel
from ...giveaway_ops import refresh_profile, skip
from ...ping_actions import UnknownStatus, action_for_giveaway, apply_ping_meta
from ..cards import feed_badge, giveaway_accounts_card, giveaway_card, giveaways_header
from ..keyboards import (
    cleanup_keyboard, giveaway_accounts_keyboard, giveaway_card_keyboard,
    giveaway_feed_keyboard, giveaway_status_keyboard, giveaway_tidy_cb, leave_confirm_keyboard,
)
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..undo import remember, snapshot, undo_row
from ..views import DIV, GiveawayFilter, account_label, fmt_dt, paginate, parse_giveaway_filter

FEATURE = "giveaways"
PAGE_SIZE = 8


def visible_accounts(perms: dict) -> list[str]:
    """Tracked usernames this key may see, '@' stripped.

    The account filter addresses these by index, so render and click must
    rebuild the exact same list — hence one helper, no local sorting.
    """
    names = [name.lstrip("@") for name in (state.ping_usernames or [])]
    whitelist = {n.lower() for n in (perms.get("accounts") or [])}
    return [n for n in names if not whitelist or n.lower() in whitelist]


async def render_feed(filt: Optional[GiveawayFilter] = None, perms: Optional[dict] = None,
                      is_admin: bool = False, removing: bool = False):
    perms = perms or full_permissions()
    filt = filt or GiveawayFilter()
    page = max(1, filt.page)
    accounts = visible_accounts(perms)
    account = accounts[filt.account] if 0 <= filt.account < len(accounts) else None
    # Fetch the whole prefix up to the requested page (+1 row to detect a next
    # one). Any filter applied below eats rows, so widen the window whenever one
    # is active — an account-scoped key always has one.
    narrowed = bool(perms.get("accounts")) or filt.wins or account is not None
    window = PAGE_SIZE * page + 1
    board = await get_giveaway_board(
        limit=window * (8 if narrowed else 1), sort=filt.db_sort, include_outbox=False
    )
    stats = board.get("stats") or {}
    need = (board.get("buckets") or {}).get("need_action") or []
    need = [r for r in need if accounts_allowed(perms, r.get("mentions"))]
    if filt.wins:
        need = [r for r in need if r.get("is_win")]
    if account is not None:
        need = [r for r in need if account_mentioned(r.get("mentions"), account)]
    rows, has_more = paginate(need, page, PAGE_SIZE)
    if not rows and page > 1:
        # The last row of the last page was just removed — show the one before.
        page -= 1
        rows, has_more = paginate(need, page, PAGE_SIZE)
    items = []
    for r in rows:
        when = fmt_dt(r.get("date") if filt.sort == "p" else r.get("detected_at"))
        badge = "🗑" if r.get("deleted_at") else feed_badge(r.get("priority_label"))
        label = f"{badge} {when} {r.get('chat') or '?'}"
        items.append((int(r["id"]), label[:48]))
    # Header shows the real queue depth, not just what fits on this page.
    total = (
        len(need) if narrowed
        else int((board.get("bucket_totals") or {}).get("need_action") or len(need))
    )
    text = giveaways_header(stats, total, filt, account_label(accounts, filt.account))
    if removing:
        text += ("\n\n✖ **Уборка:** нажмите запись — она уйдёт из очереди в «Закрыто». "
                 "Ошиблись — «↩️ Отменить» 30 с.")
    return (
        text,
        giveaway_feed_keyboard(items, page, has_more, state=filt, accounts=accounts,
                               is_admin=is_admin, removing=removing),
    )


async def render_accounts(filt: Optional[GiveawayFilter] = None, page: int = 0,
                          perms: Optional[dict] = None):
    """Account picker: every tracked username with its open wins/giveaways."""
    perms = perms or full_permissions()
    filt = filt or GiveawayFilter()
    accounts = visible_accounts(perms)
    counts = await giveaway_account_counts()
    return (
        giveaway_accounts_card(counts, accounts),
        giveaway_accounts_keyboard(accounts, counts, state=filt, page=page),
    )


async def open_card(ping_id: int, perms: Optional[dict] = None,
                    filt: Optional[GiveawayFilter] = None, is_admin: bool = False):
    ping = await get_ping_by_id(ping_id)
    if not ping:
        return None
    if perms is not None and not accounts_allowed(perms, ping.get("mentions")):
        return None
    return giveaway_card(ping), giveaway_card_keyboard(
        ping_id, filt or GiveawayFilter(), is_admin=is_admin, link=ping.get("link"))


async def _show(click: Click, rendered) -> None:
    text, kb = rendered
    await safe_edit(click.event, text, buttons=kb, link_preview=False)


async def handle(click: Click) -> None:
    seg = click.seg
    view = click.arg(1)
    if view == "feed":
        # Legacy page-only callback, still live on older messages.
        page = click.int_arg(2)
        await _show(click, await render_feed(
            GiveawayFilter(page=max(1, page or 1)), perms=click.perms,
            is_admin=click.role == "admin"))
        return
    if view == "f":
        await _show(click, await render_feed(parse_giveaway_filter(seg[2:]), perms=click.perms,
                                  is_admin=click.role == "admin"))
        return
    if view == "a":
        # `gw:a:<sort>:<wins>:<account>:<picker page>` — pick an account.
        filt = parse_giveaway_filter(seg[2:5])
        picker_page = click.int_arg(5) or 0
        await _show(click, await render_accounts(filt, picker_page, perms=click.perms))
        return
    if view == "open":
        pid = click.int_arg(2)
        if pid is None:
            await click.event.answer("Некорректная команда", alert=True)
            return
        # The list state rides along so ⬅️ returns to the same filter.
        res = await open_card(pid, click.perms, parse_giveaway_filter(seg[3:]),
                              is_admin=click.role == "admin")
        if res is None:
            await click.event.answer("Розыгрыш не найден", alert=True)
            return
        await _show(click, res)
        return
    if view in ("an", "pr", "sk", "cl", "lv", "lvgo", "ss", "sv", "x", "rm"):
        await _handle_action(click, view)
        return
    await handle_menu(click)


async def _handle_action(click: Click, view: str) -> None:
    """Действия владельца: разбор, профиль канала, отказ, уборка очереди, выход из канала."""
    if click.role != "admin":
        await click.event.answer("Только владелец", alert=True)
        return
    event = click.event
    if view == "cl":
        await _show_cleanup(click, click.int_arg(2) or 0)
        return
    if view == "x":
        await _show(click, await render_feed(parse_giveaway_filter(click.seg[2:]), perms=click.perms,
                                             is_admin=True, removing=True))
        return
    target = click.int_arg(2)
    if target is None:
        await event.answer("Некорректная команда", alert=True)
        return
    if view == "rm":
        await _remove(click, target, parse_giveaway_filter(click.seg[3:]))
        return
    if view == "ss":
        ping = await get_ping_by_id(target)
        if not ping:
            await event.answer("Розыгрыш не найден", alert=True)
            return
        await safe_edit(event, giveaway_card(ping),
                        buttons=giveaway_status_keyboard(target, ping.get("giveaway_status")),
                        link_preview=False)
        return
    if view == "sv":
        code = click.arg(3)
        before = await snapshot([target])
        try:
            await apply_ping_meta(target, giveaway_status=code,
                                  action_status=action_for_giveaway(code))
        except UnknownStatus:
            await event.answer("Неизвестный статус", alert=True)
            return
        token = remember(click.sender_id, before, "статус", f"gw:open:{target}")
        await event.answer("Сохранено")
        res = await open_card(target, click.perms, GiveawayFilter(), is_admin=True)
        if res is not None:
            text, kb = res
            await _show(click, (text, undo_row(token, "статус") + kb))
        return
    if view == "lv":
        # Выход необратим — сначала подтверждение, как у рестарта и удаления ключа.
        item = (state.bot_cleanup_cache.get("items") or {}).get(target)
        await safe_edit(event, leave_confirm_text(target, item), buttons=leave_confirm_keyboard(target))
        return
    try:
        if view == "an":
            candidate = await analyze(target)
            await event.answer(f"Разобрано · score {candidate.get('score', '—')}")
        elif view == "pr":
            await refresh_profile(target)
            await event.answer("Профиль канала обновлён")
        elif view == "sk":
            await skip(target)
            await event.answer("Отмечено: не участвуем")
        elif view == "lvgo":
            item = (state.bot_cleanup_cache.get("items") or {}).get(target) or {}
            left = await leave_channel(target, item.get("accounts") or None)
            await event.answer(f"Вышли из канала: аккаунтов {left}", alert=True)
            await _show_cleanup(click, 0)
            return
    except GiveawayActionError as exc:
        await event.answer(str(exc)[:180], alert=True)
        return
    if view == "sk":
        await _show(click, await render_feed(perms=click.perms, is_admin=True))
        return
    res = await open_card(target, click.perms, GiveawayFilter(), is_admin=True)
    if res is not None:
        await _show(click, res)


async def _remove(click: Click, target: int, filt: GiveawayFilter) -> None:
    """Тап в режиме уборки: закрыть запись и перерисовать ту же страницу с отменой."""
    before = await snapshot([target])
    if not before:
        await click.event.answer("Розыгрыш не найден", alert=True)
        return
    await apply_ping_meta(target, giveaway_status="closed", action_status="closed")
    token = remember(click.sender_id, before, "убрано", giveaway_tidy_cb(filt).decode())
    await click.event.answer("Убрано из очереди")
    text, kb = await render_feed(filt, perms=click.perms, is_admin=True, removing=True)
    await _show(click, (text, undo_row(token, "убрано") + kb))


def leave_confirm_text(chat_id: int, item: Optional[dict] = None) -> str:
    item = item or {}
    lines = ["🚪 **Выйти из канала?**", DIV, f"Канал: **{item.get('title') or chat_id}**"]
    accounts = item.get("accounts") or []
    if accounts:
        lines.append("👤 Выйдут: " + ", ".join(f"`{a}`" for a in accounts))
    else:
        lines.append("👤 Выйдет аккаунт действий")
    if item.get("inactive_days") is not None:
        lines.append(f"🕐 Молчит: `{item.get('inactive_days')}` дн")
    if int(item.get("wins") or 0):
        lines.append(f"🏆 **Побед за 90 дней: {item['wins']}** — канал платил, подумайте ещё раз.")
    lines.append("\n__Действие необратимо: вернуться в приватный канал можно только по новой "
                 "ссылке-приглашению.__")
    return "\n".join(lines)


def cleanup_text(data: dict) -> str:
    items = data.get("candidates") or []
    safe_count = sum(1 for i in items if not int(i.get("wins") or 0))
    lines = [
        "🧹 **Мёртвые каналы**", DIV,
        f"🛰 Аккаунтов проверено: `{data.get('accounts', 0)}`",
        f"🕐 Молчат дольше: `{data.get('inactive_days', '—')}` дн",
        f"📦 Найдено: `{len(items)}` · без побед за {data.get('win_days', 90)} дн: `{safe_count}`",
    ]
    if data.get("warning"):
        lines.append(f"⚠️ {data['warning']}")
    lines.append("\n__Сначала каналы без побед. 🏆 — канал уже платил. Выход — из всех аккаунтов, что в нём сидят.__")
    return "\n".join(lines)


async def _show_cleanup(click: Click, page: int) -> None:
    """Каналы, которые давно молчат: кандидаты на выход."""
    cache = state.bot_cleanup_cache
    fresh = cache.get("at") and datetime.now() - cache["at"] < timedelta(minutes=10)
    if page and fresh:
        # Paging must not re-walk every account's dialogs.
        items = sorted(cache["items"].values(),
                       key=lambda i: (int(i.get("wins") or 0) > 0, -int(i.get("inactive_days") or 0)))
        data = {"candidates": items, "accounts": len({a for i in items for a in i.get("accounts", [])}),
                "inactive_days": ws.GIVEAWAY_INACTIVE_CHANNEL_DAYS, "win_days": 90}
    else:
        data = await cleanup_candidates()
    items = data.get("candidates") or []
    await safe_edit(click.event, cleanup_text(data),
                    buttons=cleanup_keyboard(items, page), link_preview=False)


async def handle_menu(click: Click) -> None:
    await _show(click, await render_feed(perms=click.perms, is_admin=click.role == "admin"))


def register(router: CallbackRouter) -> None:
    router.group("gw", feature=FEATURE)(handle)
    router.exact("menu_giveaways", feature=FEATURE)(handle_menu)
