"""🧾 Чеки: автозабор чеков xRocket / CryptoBot (см. check_claimer).

`ck` — экран; `ck:m:<mode>` — режим (claim / watch / off); `ck:a:<i>` — аккаунт
ловит / не ловит (индекс в `account_rows()`: тот же порядок при отрисовке и при
нажатии). Карточка-«рука» — капча, пароль, непонятный ответ бота:
`ck:b:<token>:<r>:<c>` — нажать эту кнопку бота от аккаунта, `ck:t:<token>` —
ответить текстом, `ck:r:<token>` — начать чек заново, `ck:n:<token>` — к
следующему аккаунту, застрявшему на том же чеке. Только владелец.
"""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from telethon import Button

from database import get_check_claim_stats, get_recent_check_claims

from ... import check_claimer
from ... import check_claims as cc
from ...app_ctx import state
from ...common import record_app_event
from ..chrome import empty, header
from ..pending import InputRejected, prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV

MODE_LABELS = {"claim": "🎯 Ловить", "watch": "👀 Смотреть", "off": "⏸ Выкл"}


def account_rows() -> list[tuple[str, str]]:
    """(session, label) for every configured account, in a stable order."""
    names = list(state.session_names) or list(state.accounts_state)
    rows = []
    for name in sorted(dict.fromkeys(names), key=str.lower):
        account = state.accounts_state.get(name) or {}
        rows.append((name, f"@{account['username']}" if account.get("username") else name))
    return rows


def _when(value: Optional[str]) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m %H:%M")
    except (TypeError, ValueError):
        return "—"


def card(cfg: dict[str, Any], accounts: list[tuple[str, str]], recent: list[dict],
         stats_all: dict[str, Any], stats_day: dict[str, Any], note: str = "") -> str:
    catching = [label for name, label in accounts if name not in cfg["disabled"]]
    lines = [f"{note}\n" if note else "", header("🧾", "Чеки", "Настройки › Чеки"),
             f"Режим: **{MODE_LABELS[cfg['mode']]}**",
             f"Ловят: {len(catching)} из {len(accounts)} аккаунтов"]
    day = stats_day.get("outcomes") or {}
    if day:
        lines.append("За сутки: " + " · ".join(
            f"{cc.OUTCOME_LABELS.get(name, name)} {count}" for name, count in sorted(day.items(), key=lambda kv: -kv[1])
        ))
    totals = cc.amount_totals(stats_all.get("claimed") or [])
    if totals:
        lines.append("Всего забрано: " + " · ".join(f"{value} {currency}" for currency, value in totals.items()))
    lines.append(DIV)
    if not recent:
        lines.append(empty("Чеков пока не было."))
    for row in recent:
        who = row.get("account") or "—"
        chat = str(row.get("chat") or "")[:32]
        speed = f" · ⚡{row['press_ms']}мс" if row.get("press_ms") is not None else ""
        lines.append(f"{cc.OUTCOME_LABELS.get(row['outcome'], row['outcome'])} · "
                     f"{_when(row.get('updated_at') or row.get('created_at'))} · "
                     f"{row.get('amount') or '—'} · {who} · {chat}{speed}")
    lines += [DIV, "__Жмёт сам: каждый аккаунт, увидевший чек, а личный чек «для @…» — только адресат. "
                   "Чеки ваших аккаунтов и владельца не трогает. Капчу не решает: присылает её сюда "
                   "кнопками, ответ нажимаете вы.__"]
    return "\n".join(line for line in lines if line)


def keyboard(cfg: dict[str, Any], accounts: list[tuple[str, str]]) -> list[list[Button]]:
    rows = [[Button.inline(("• " if cfg["mode"] == mode else "") + label, f"ck:m:{mode}".encode())
             for mode, label in MODE_LABELS.items()]]
    toggles = [Button.inline(f"{'⛔' if name in cfg['disabled'] else '✅'} {label[:24]}", f"ck:a:{i}".encode())
               for i, (name, label) in enumerate(accounts)]
    rows += [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    rows.append([Button.inline("⬅️ Настройки", b"st"), Button.inline("🔄 Обновить", b"ck")])
    return rows


def _cfg() -> dict[str, Any]:
    return state.check_claim_cfg or cc.normalize_config(None)


async def _show(click: Click, note: str = "") -> None:
    cfg, accounts = _cfg(), account_rows()
    since = (datetime.now() - timedelta(days=1)).replace(microsecond=0).isoformat()
    text = card(cfg, accounts, await get_recent_check_claims(8), await get_check_claim_stats(),
                await get_check_claim_stats(since), note)
    await safe_edit(click.event, text, buttons=keyboard(cfg, accounts))


# ---- relay ------------------------------------------------------------------
async def _consume_reply(event, pending: dict, raw: str) -> None:
    token = pending.get("scope") or ""
    relay = state.check_relays.get(token)
    if relay is None:
        await event.respond("⌛ Карточка устарела — чек уже не ждёт ответа.")
        return
    if not raw:
        raise InputRejected("❌ Пустой ответ.")
    try:
        outcome, reply, action = await check_claimer.relay_answer(token, raw)
    except Exception as exc:
        await event.respond(f"⚠️ Не отправил: {exc}")
        return
    text, buttons = check_claimer.relay_screen(relay, outcome, reply, action)
    await event.respond(text, buttons=buttons or None)


REPLY_KIND = register_prompt(
    "ck_reply", "Ответ боту чека (пароль, код капчи) — отправлю от аккаунта как есть:",
    _consume_reply, keep_screen=True,
)


async def _relay(click: Click, action: str) -> None:
    token = click.arg(2)
    relay = state.check_relays.get(token)
    if relay is None:
        await click.event.answer("Карточка устарела — чек уже не ждёт ответа", alert=True)
        return
    if action == "t":
        await prompt_pending(click.event, REPLY_KIND, scope=token)
        return
    if action == "n":
        if await check_claimer.relay_next(token) is None:
            await click.event.answer("Больше аккаунтов нет", alert=True)
            return
        await click.event.answer("▶️ Прислал карточку следующего аккаунта")
        with suppress(Exception):
            await click.event.edit(buttons=None)
        return
    row, column = click.int_arg(3), click.int_arg(4)
    if action == "b" and (row is None or column is None):
        await click.event.answer("Некорректная кнопка", alert=True)
        return
    await click.event.answer("⏳ Жму…")
    note = ""
    try:
        if action == "b":
            outcome, reply, step = await check_claimer.relay_press(token, row, column)
        else:
            outcome, reply, step = await check_claimer.relay_retry(token)
    except Exception as exc:
        outcome, reply, step, note = "error", "", None, f"⚠️ {exc}"
    text, buttons = check_claimer.relay_screen(relay, outcome, reply, step, note)
    await safe_edit(click.event, text, buttons=buttons or None)


# ---- dispatch ---------------------------------------------------------------
async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "m":
        mode = click.arg(2)
        if mode not in cc.MODES:
            await click.event.answer("Некорректный режим", alert=True)
            return
        await check_claimer.save({**_cfg(), "mode": mode})
        await record_app_event("INFO", "checks", "Check claim mode changed", {"mode": mode})
        await _show(click, f"Режим: {MODE_LABELS[mode]}")
        return
    if action == "a":
        index, accounts = click.int_arg(2), account_rows()
        if index is None or not 0 <= index < len(accounts):
            await click.event.answer("Аккаунт не найден — обновите экран", alert=True)
            return
        name, label = accounts[index]
        disabled = set(_cfg()["disabled"]) ^ {name}
        await check_claimer.save({**_cfg(), "disabled": sorted(disabled)})
        await _show(click, f"{label}: {'не ловит' if name in disabled else 'ловит'} чеки")
        return
    if action in ("b", "t", "r", "n"):
        await _relay(click, action)
        return
    await _show(click)


def register(router: CallbackRouter) -> None:
    router.group("ck", admin=True)(handle)
