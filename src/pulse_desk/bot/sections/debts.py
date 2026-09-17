"""Долги: выигранное, но ещё не забранное.

Перенос вкладки «Долги» из веба. Там это была очередь с сегментами, «горячим»
долгом сверху, чекбоксами для массового «забрал» и линзой по аккаунтам. Здесь
то же самое, но отметки для массового действия живут в ``state.bot_debt_marks``:
десяток id в 64 байта callback-данных не влезает.

Владельческий раздел. Гостю он не открывается даже грант-кодом: это учёт денег
владельца, а свои выигрыши держатель ключа и так видит в ленте розыгрышей.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from telethon import Button

from database import get_debt_board, get_duplicates, get_ping_by_id

from ...app_ctx import state
from ...ping_actions import apply_ping_meta
from ...prize_value import in_uah, totals, value_by_account
from ..chrome import chip, empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from .market import latest_snapshot
from ..undo import remember, snapshot, undo_row
from ..views import DIV, fmt_dt, is_openable_link

PAGE_SIZE = 8
BOARD_LIMIT = 180
CRITICAL_SCORE = 90
HIGH_SCORE = 70

# Сегменты очереди — те же, что были кнопками над списком в вебе.
SEGMENTS: tuple[tuple[str, str], ...] = (
    ("a", "Все"),
    ("c", "Критично"),
    ("h", "Высокий"),
    ("n", "Свежие"),
)
SEGMENT_LABEL = dict(SEGMENTS)


def score_of(row: dict[str, Any]) -> int:
    try:
        return int(row.get("priority_score") or 0)
    except (TypeError, ValueError):
        return 0


def segment_rows(rows: Sequence[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    """Строки одного сегмента. Незнакомый код — это «Все», а не пустой экран."""
    if code == "c":
        return [r for r in rows if score_of(r) >= CRITICAL_SCORE]
    if code == "h":
        return [r for r in rows if HIGH_SCORE <= score_of(r) < CRITICAL_SCORE]
    if code == "n":
        return [r for r in rows if str(r.get("status") or "") == "new"]
    return list(rows)


def total_value(rows: Sequence[dict[str, Any]]) -> float:
    """Сумма оценок приза. Оценка есть не у каждой строки — это нормально."""
    total = 0.0
    for row in rows:
        try:
            total += float(row.get("estimated_value") or 0)
        except (TypeError, ValueError):
            continue
    return total


def hottest(rows: Sequence[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Долг, которым стоит заняться первым: приоритет, затем свежесть."""
    if not rows:
        return None
    return max(rows, key=lambda r: (score_of(r), str(r.get("detected_at") or "")))


def marks(sender_id: int) -> set[int]:
    # Touched on every use so the janitor can drop a selection left for hours.
    state.bot_debt_marks_touched[sender_id] = datetime.now()
    return state.bot_debt_marks.setdefault(sender_id, set())


def row_label(row: dict[str, Any], marked: bool) -> str:
    flag = "☑️" if marked else ("🔴" if score_of(row) >= CRITICAL_SCORE else "•")
    when = fmt_dt(row.get("detected_at"))
    return f"{flag} {when} {row.get('chat') or '?'}"[:48]


def money_lines(by_account: dict[str, dict[str, float]], snapshot: Optional[dict[str, Any]],
                limit: int = 5) -> list[str]:
    """«💵 Незабрано»: сумма призов по аккаунтам в $ и ₴ (см. prize_value)."""
    if not by_account:
        return []
    total = totals(by_account)
    uah = in_uah(total["usd"], snapshot)
    head = f"💵 **Незабрано:** `${total['usd']:,.2f}`".replace(",", " ")
    if uah:
        head += f" · `₴{uah:,.0f}`".replace(",", " ")
    if total["unpriced"]:
        head += f"\n__без оценки (скины, боты): {int(total['unpriced'])}__"
    lines = [head]
    ranked = sorted(by_account.items(), key=lambda kv_: (-kv_[1]["usd"], -kv_[1]["unpriced"]))
    for account, bucket in ranked[:limit]:
        extra = f" + {int(bucket['unpriced'])} б/о" if bucket["unpriced"] else ""
        lines.append(f"• @{account}: `${bucket['usd']:,.2f}`{extra}".replace(",", " "))
    if len(ranked) > limit:
        lines.append(f"__…ещё {len(ranked) - limit} — «💵 По аккаунтам»__")
    return lines


def board_card(board: dict[str, Any], rows: Sequence[dict[str, Any]], code: str,
               selected: int, money: Optional[list[str]] = None) -> str:
    stats = board.get("stats") or {}
    value = total_value(rows)
    lines = [
        header("💰", "Долги", "Домой › Долги"),
        f"{kv('📦', 'В очереди', len(rows))}   {kv('🆕', 'Новых', stats.get('new', 0))}",
        f"{kv('🔴', 'Критичных', stats.get('critical', 0))}   "
        f"{kv('💵', 'Оценка', f'{value:,.0f}'.replace(',', ' ') if value else '—')}",
        f"🔽 __Сегмент: {SEGMENT_LABEL.get(code, 'Все')}__",
    ]
    if money:
        lines += [DIV, *money]
    top = hottest(rows)
    if top:
        lines.append(DIV)
        lines.append(f"🔥 **Горячий:** {top.get('chat') or '?'} · {chip(f'score {score_of(top)}')}")
        text = str(top.get("text") or "").strip().replace("\n", " ")
        if text:
            lines.append(f"__{text[:160]}__")
    if selected:
        lines.append(f"\n☑️ Отмечено: `{selected}`")
    if not rows:
        lines.append(empty("Под этот сегмент ничего не попало."))
    return "\n".join(lines)


def board_keyboard(rows: Sequence[dict[str, Any]], code: str, page: int,
                   has_more: bool, marked: set[int]) -> list[list[Button]]:
    buttons: list[list[Button]] = [
        [Button.inline(row_label(row, int(row["id"]) in marked),
                       f"db:open:{row['id']}:{code}:{page}".encode())]
        for row in rows
    ]
    buttons.append([
        Button.inline(f"▸{label}" if c == code else label, f"db:s:{c}:1".encode())
        for c, label in SEGMENTS
    ])
    if page > 1 or has_more:
        pager: list[Button] = []
        if page > 1:
            pager.append(Button.inline("◀️", f"db:s:{code}:{page - 1}".encode()))
        pager.append(Button.inline(f"· {page} ·", b"noop"))
        if has_more:
            pager.append(Button.inline("▶️", f"db:s:{code}:{page + 1}".encode()))
        buttons.append(pager)
    if marked:
        buttons.append([
            Button.inline(f"✅ Забрал ({len(marked)})", f"db:go:{code}:{page}".encode()),
            Button.inline("✖️ Снять отметки", f"db:clr:{code}:{page}".encode()),
        ])
    buttons.append([
        Button.inline("💵 По аккаунтам", f"db:v:{code}:{page}".encode()),
        Button.inline("🗒 Заметка", b"menu_obsidian"),
        Button.inline("🔄 Обновить", f"db:s:{code}:{page}".encode()),
    ])
    buttons.append([Button.inline("⬅️ Домой", b"menu_main")])
    return buttons


def debt_card(row: dict[str, Any], copies: Optional[Sequence[dict[str, Any]]] = None) -> str:
    lines = [
        header("💰", "Долг", f"Долги › #{row.get('id')}"),
        f"📅 `{fmt_dt(row.get('detected_at'))}`  ·  {row.get('chat') or '?'}",
        f"{chip(f'score {score_of(row)}')} {chip(str(row.get('action_status') or 'claim_prize'))}",
        DIV,
        str(row.get("text") or "—")[:700],
    ]
    value = row.get("estimated_value")
    if value:
        lines.append(f"💵 Оценка: `{value}`")
    if row.get("link"):
        lines.append(f"🔗 {row['link']}")
    if copies:
        lines.append(DIV)
        lines.append(f"📎 **Тот же пост ещё в {len(copies)} чатах** __(склеено, статус общий)__")
        lines += [f"• {str(c.get('chat') or '?')[:40]} · `{fmt_dt(c.get('detected_at'))}`" for c in copies[:5]]
    return "\n".join(lines)


def card_keyboard(row: dict[str, Any], code: str, page: int,
                  marked: bool) -> list[list[Button]]:
    ping_id = int(row.get("id") or 0)
    rows: list[list[Button]] = [[
        Button.inline("✅ Забрал", f"db:c:{ping_id}:{code}:{page}".encode()),
        Button.inline("🚫 Скам", f"db:x:{ping_id}:{code}:{page}".encode()),
    ], [
        Button.inline("☑️ Снять отметку" if marked else "⬜ Отметить",
                      f"db:m:{ping_id}:{code}:{page}".encode()),
    ]]
    if is_openable_link(row.get("link")):
        rows.append([Button.url("Написать в Telegram", str(row["link"]))])
    rows.append([
        Button.inline("⬅️ К списку", f"db:s:{code}:{page}".encode()),
        Button.inline("🔄 Обновить", f"db:open:{ping_id}:{code}:{page}".encode()),
    ])
    return rows


async def load(code: str, page: int) -> tuple[dict, list[dict], bool]:
    board = await get_debt_board(state.ping_usernames, limit=BOARD_LIMIT)
    rows = segment_rows(board.get("rows") or [], code)
    start = (max(1, page) - 1) * PAGE_SIZE
    window = rows[start:start + PAGE_SIZE]
    return board, window, len(rows) > start + PAGE_SIZE


async def _show_board(click: Click, code: str, page: int, undo_token: Optional[str] = None,
                      undo_label: str = "") -> None:
    board, window, has_more = await load(code, page)
    selected = marks(click.sender_id)
    rows = segment_rows(board.get("rows") or [], code)
    snapshot = await latest_snapshot()
    money = money_lines(value_by_account(board.get("rows") or [], state.ping_usernames, snapshot), snapshot)
    await safe_edit(click.event,
                    board_card(board, rows, code, len(selected), money),
                    buttons=undo_row(undo_token, undo_label)
                    + board_keyboard(window, code, page, has_more, selected),
                    link_preview=False)


async def _show_card(click: Click, ping_id: int, code: str, page: int) -> None:
    row = await get_ping_by_id(ping_id)
    if not row:
        await click.event.answer("Запись не найдена", alert=True)
        return
    await safe_edit(click.event, debt_card(row, await get_duplicates(ping_id)),
                    buttons=card_keyboard(row, code, page, ping_id in marks(click.sender_id)),
                    link_preview=False)


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "s":
        await _show_board(click, click.arg(2, "a"), click.int_arg(3) or 1)
        return
    if action == "clr":
        marks(click.sender_id).clear()
        await click.event.answer("Отметки сняты")
        await _show_board(click, click.arg(2, "a"), click.int_arg(3) or 1)
        return
    if action == "go":
        await _claim_marked(click, click.arg(2, "a"), click.int_arg(3) or 1)
        return
    if action == "v":
        await _show_values(click, click.arg(2, "a"), click.int_arg(3) or 1)
        return
    ping_id = click.int_arg(2)
    code = click.arg(3, "a")
    page = click.int_arg(4) or 1
    if ping_id is None:
        await _show_board(click, "a", 1)
        return
    if action == "open":
        await _show_card(click, ping_id, code, page)
        return
    if action == "m":
        selected = marks(click.sender_id)
        if ping_id in selected:
            selected.discard(ping_id)
            await click.event.answer("Отметка снята")
        else:
            selected.add(ping_id)
            await click.event.answer(f"Отмечено: {len(selected)}")
        await _show_card(click, ping_id, code, page)
        return
    if action in ("c", "x"):
        status = "claimed" if action == "c" else "scam"
        token = remember(click.sender_id, await snapshot([ping_id]),
                         "забрал" if action == "c" else "скам", f"db:s:{code}:{page}")
        await apply_ping_meta(ping_id, giveaway_status=status, action_status=status)
        marks(click.sender_id).discard(ping_id)
        await click.event.answer("Забрал" if action == "c" else "Отмечено как скам")
        await _show_board(click, code, page, token, "забрал" if action == "c" else "скам")
        return
    await _show_board(click, "a", 1)


async def _claim_marked(click: Click, code: str, page: int) -> None:
    """Массовое «забрал». Веб слал по запросу на строку — здесь один проход."""
    selected = sorted(marks(click.sender_id))
    if not selected:
        await click.event.answer("Ничего не отмечено", alert=True)
        return
    token = remember(click.sender_id, await snapshot(selected), f"забрал {len(selected)}",
                     f"db:s:{code}:{page}")
    for ping_id in selected:
        await apply_ping_meta(ping_id, giveaway_status="claimed", action_status="claimed")
    marks(click.sender_id).clear()
    await click.event.answer(f"Забрано: {len(selected)}")
    await _show_board(click, code, page, token, f"забрал {len(selected)}")


async def _show_values(click: Click, code: str, page: int) -> None:
    """Все аккаунты с незабранными призами — сумма, в $ и ₴."""
    board = await get_debt_board(state.ping_usernames, limit=BOARD_LIMIT)
    snapshot = await latest_snapshot()
    by_account = value_by_account(board.get("rows") or [], state.ping_usernames, snapshot)
    lines = [header("💵", "Незабрано по аккаунтам", "Долги › Суммы")]
    lines += money_lines(by_account, snapshot, limit=50) or [empty("Незабранных побед нет.")]
    if not snapshot:
        lines.append("\n⚠️ __Курсов пока нет — суммы в других валютах не пересчитаны.__")
    lines.append("\n__Сумма — из текста поста (5 USDT, 4$, 100 грн); призы без цены считаются отдельно.__")
    await safe_edit(click.event, "\n".join(lines),
                    buttons=[[Button.inline("⬅️ К долгам", f"db:s:{code}:{page}".encode())]])


async def handle_menu(click: Click) -> None:
    await _show_board(click, "a", 1)


def register(router: CallbackRouter) -> None:
    router.group("db", admin=True)(handle)
    router.exact("menu_debts", admin=True)(handle_menu)
