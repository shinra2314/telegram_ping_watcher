"""🔇 Игнорируемые чаты: где упоминания не отслеживаются вовсе.

`igc` — список; `igc:add:<ping_id>` — кнопка на карточке упоминания, берёт чат
из пинга; `igc:del:<chat_id>` — вернуть чат из списка;
`igc:del:<chat_id>:<ping_id>` — то же с карточки, кнопка на ней переключается
обратно. Только владелец.
"""
from __future__ import annotations

from typing import Any, Optional

from telethon import Button

from database import get_ping_by_id

from ... import ignored_chats
from ...app_ctx import logger
from ...common import record_app_event
from ..chrome import empty, header
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV


def ignore_button(ping_id: int) -> Any:
    """The card button; kept here so the card and the handler agree on the data."""
    return Button.inline("🔇 Не следить за чатом", f"igc:add:{int(ping_id)}".encode())


def card(cfg: dict[str, str], note: str = "") -> str:
    lines = [f"{note}\n" if note else "", header("🔇", "Игнор-чаты", "Настройки › Уведомления")]
    if not cfg:
        lines.append(empty("Список пуст — отслеживаются все чаты."))
    for chat_id, title in cfg.items():
        lines.append(f"• {title} · `{chat_id}`")
    lines += [DIV, "__Сообщения из этих чатов не читаются: ни пингов, ни карточек, ни копий друзьям. "
                   "Добавить чат — кнопка «🔇 Не следить за чатом» на карточке упоминания.__"]
    return "\n".join(line for line in lines if line)


def keyboard(cfg: dict[str, str]) -> list[list[Button]]:
    rows = [[Button.inline(f"↩️ Вернуть: {title[:28]}", f"igc:del:{chat_id}".encode())]
            for chat_id, title in cfg.items()]
    rows.append([Button.inline("⬅️ Уведомления", b"st_n"), Button.inline("🔄 Обновить", b"igc")])
    return rows


async def _show(click: Click, note: str = "") -> None:
    cfg = await ignored_chats.load()
    await safe_edit(click.event, card(cfg, note), buttons=keyboard(cfg))


async def _swap_card_button(click: Click, new_row: list[Button]) -> None:
    """Replace the card's igc button, keeping every other button as it was."""
    try:
        message = await click.event.get_message()
        rows: list[list[Button]] = []
        for row in message.buttons or []:
            kept = []
            for button in row:
                data: Optional[bytes] = getattr(button, "data", None)
                if data and data.startswith(b"igc:"):
                    continue
                url = getattr(button, "url", None)
                kept.append(Button.url(button.text, url) if url else Button.inline(button.text, data or b"noop"))
            if kept:
                rows.append(kept)
        rows.append(new_row)
        await click.event.edit(buttons=rows)
    except Exception:
        logger.debug("Could not swap the ignore button on a ping card", exc_info=True)


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "add":
        ping_id = click.int_arg(2)
        ping = await get_ping_by_id(ping_id) if ping_id is not None else None
        if not ping or ping.get("chat_id") is None:
            await click.event.answer("Пинг не найден", alert=True)
            return
        chat_id, title = int(ping["chat_id"]), str(ping.get("chat") or ping["chat_id"])
        await ignored_chats.save(ignored_chats.add(await ignored_chats.load(), chat_id, title))
        await record_app_event("INFO", "notifications", "Chat ignored", {"chat_id": chat_id, "chat": title})
        await click.event.answer(f"🔇 {title[:60]} больше не отслеживается")
        await _swap_card_button(click, [Button.inline("↩️ Снова следить за чатом",
                                                      f"igc:del:{chat_id}:{ping_id}".encode())])
        return
    if action == "del":
        chat_id = click.int_arg(2)
        if chat_id is None:
            await click.event.answer("Некорректная команда", alert=True)
            return
        cfg = await ignored_chats.load()
        title = cfg.get(str(chat_id), str(chat_id))
        await ignored_chats.save(ignored_chats.remove(cfg, chat_id))
        await record_app_event("INFO", "notifications", "Chat watched again", {"chat_id": chat_id, "chat": title})
        ping_id = click.int_arg(3)
        if ping_id is not None:
            await click.event.answer(f"👀 {title[:60]} снова отслеживается")
            await _swap_card_button(click, [ignore_button(ping_id)])
            return
        await _show(click, f"👀 {title} снова отслеживается.")
        return
    await _show(click)


def register(router: CallbackRouter) -> None:
    router.group("igc", admin=True)(handle)
