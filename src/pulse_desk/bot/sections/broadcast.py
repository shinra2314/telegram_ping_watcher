"""Рассылки друзьям: модерация заявок, отметки участия, «скрыть у всех».

Заявка захватывается одним UPDATE (``claim_pending_broadcast``), поэтому две
нажатые кнопки не разошлют её дважды. Обратная сторона: если отправка после
захвата упадёт, строка сгорит — помечена approved, не доставлена, повторить
нечем. Отсюда явный ``release_pending_broadcast`` в обработчике ошибки.
"""
from __future__ import annotations

from contextlib import suppress

from telethon import Button

from database import (
    cancel_pending_sends, claim_pending_broadcast, delete_broadcast_messages,
    get_broadcast_messages, get_ping_by_id, release_pending_broadcast, set_member_engagement,
)
from telethon.errors import FloodWaitError

from ...app_ctx import logger, state
from ...bot_notify import edit_pending_admin_card, execute_pending_broadcast
from ...common import record_app_event
from ..reply import safe_edit
from ..router import CallbackRouter, Click


async def handle_moderation(click: Click) -> None:
    """`bc:ok|no:<id>` — карточка модерации у владельца."""
    event = click.event
    pb_id = click.int_arg(2)
    if pb_id is None or click.arg(1) not in ("ok", "no"):
        await event.answer("Некорректная команда", alert=True)
        return
    approve = click.arg(1) == "ok"
    row = await claim_pending_broadcast(pb_id, "approved" if approve else "rejected", click.sender_id)
    if row is None:
        await event.answer("Уже обработано")
        return
    if approve:
        # The claim above is one-shot, so a failure here would burn the row:
        # marked approved, never delivered, no way to retry.
        try:
            count, token = await execute_pending_broadcast(row)
            await edit_pending_admin_card(row, f"✅ Разослано друзьям ({count})",
                                          token=token, delivered_count=count)
        except Exception as exc:
            await release_pending_broadcast(pb_id)
            logger.exception("Approved broadcast %s failed to send", pb_id)
            await record_app_event("ERROR", "broadcast",
                                   "Approved broadcast failed, returned to queue",
                                   {"id": pb_id, "error": str(exc)})
            await event.answer("⚠️ Не отправилось — заявка возвращена в очередь, попробуйте ещё раз.",
                               alert=True)
            return
        await event.answer(f"📣 Разослано: {count}")
        await record_app_event("INFO", "broadcast", "Pending broadcast approved",
                               {"id": pb_id, "delivered": count})
        return
    footer = "🚫 Рассылка отклонена"
    bc_token = row.get("bc_token") or None
    premium_count = 0
    if bc_token:
        premium_count = len(await get_broadcast_messages(bc_token))
        if premium_count:
            footer += f" · ⚡ премиум уже получили: {premium_count}"
    await edit_pending_admin_card(row, footer,
                                  token=bc_token if premium_count else None,
                                  delivered_count=premium_count)
    await event.answer("Отклонено")
    await record_app_event("INFO", "broadcast", "Pending broadcast rejected", {"id": pb_id})


async def handle_engagement(click: Click) -> None:
    """`bcm:in|skip:<ping>` — «участвую / пропустил» на копии у друга."""
    event = click.event
    pid = click.int_arg(2)
    if pid is None or click.arg(1) not in ("in", "skip"):
        await event.answer("Некорректная команда", alert=True)
        return
    action = "joined" if click.arg(1) == "in" else "skipped"
    await set_member_engagement(click.sender_id, pid, action)
    await record_app_event("INFO", "engagement", "Member engagement recorded",
                           {"tg_id": click.sender_id, "ping_id": pid, "action": action})
    chosen = "✅ Участвую" if action == "joined" else "⏭ Пропустил"
    with suppress(Exception):
        # Keep the link button, freeze the choice row on the member's copy.
        ping = await get_ping_by_id(pid)
        new_buttons: list[list[Button]] = []
        link = (ping or {}).get("link")
        if link and not str(link).startswith("нет "):
            new_buttons.append([Button.url("Открыть в Telegram", link)])
        other = "bcm:skip" if action == "joined" else "bcm:in"
        new_buttons.append([
            Button.inline(f"● {chosen}", data=b"noop"),
            Button.inline("изменить", data=f"{other}:{pid}"),
        ])
        await event.edit(buttons=new_buttons)
    await event.answer("Записал: участвуете 🎯" if action == "joined" else "Ок, пропускаем")


async def handle_hide(click: Click) -> None:
    """`hidebc_<token>` — убрать разосланную копию у всех получателей."""
    event = click.event
    token = click.tail()
    rows = await get_broadcast_messages(token)
    # Copies still waiting on a per-key delay are dropped before they ever
    # reach the member.
    cancelled = await cancel_pending_sends(token)
    if not rows and not cancelled:
        await event.answer("Уже скрыто или устарело")
        return
    # One API call per recipient used to run unbatched and with no flood
    # handling, so hiding a wide broadcast could trip a wait halfway through and
    # lose the rest. Group by peer, delete in one call each, and stop cleanly if
    # Telegram throttles us.
    by_peer: dict[int, list[int]] = {}
    for row in rows:
        by_peer.setdefault(int(row["tg_id"]), []).append(int(row["message_id"]))
    hidden = 0
    failed = 0
    for tg_id, message_ids in by_peer.items():
        try:
            await state.bot_client.delete_messages(tg_id, message_ids)
            hidden += len(message_ids)
        except FloodWaitError as exc:
            failed += len(message_ids)
            logger.warning("Flood wait (%ss) while hiding broadcast copies", exc.seconds)
            break
        except Exception as exc:
            failed += len(message_ids)
            logger.warning("Failed to delete broadcast copy for %s: %s", tg_id, exc)
    await delete_broadcast_messages(token)
    # Swap the hide button for a confirmation, keeping the rest of the keyboard.
    try:
        msg = await event.get_message()
        new_rows: list[list[Button]] = []
        for btn_row in (msg.buttons or []):
            rebuilt = []
            for btn in btn_row:
                if btn.data == event.data:
                    rebuilt.append(Button.inline(f"✅ Скрыто у друзей ({hidden + cancelled})", data=b"noop"))
                elif btn.url:
                    rebuilt.append(Button.url(btn.text, btn.url))
                elif btn.data:
                    rebuilt.append(Button.inline(btn.text, btn.data))
            if rebuilt:
                new_rows.append(rebuilt)
        if new_rows:
            await safe_edit(event, buttons=new_rows)
    except Exception:
        logger.debug("Could not update hide button after broadcast hide", exc_info=True)
    note = f"Скрыто у {hidden} друзей"
    if cancelled:
        note += f", отменено до отправки: {cancelled}"
    if failed:
        note += f", не удалось: {failed}"
    await event.answer(note, alert=bool(failed))


def register(router: CallbackRouter) -> None:
    router.group("bc", admin=True)(handle_moderation)
    router.group("bcm")(handle_engagement)
    router.group("hidebc", sep="_", admin=True)(handle_hide)
