"""Зарплаты из книги «Учет розыгрышей» — раздел, которого для чужих не существует.

Кто видит раздел, решает **метка ключа**, а не грант-код: метка, совпадающая с
аккаунтом на листе «Настройки», открывает раздел для этого одного аккаунта.
Грант-кода здесь нет намеренно — пустой ``permissions`` у легаси-ключа означает
«разрешить всё», и такой код открыл бы чужую зарплату каждому старому ключу.
Поэтому же отказ звучит как «Неизвестная команда»: подобранный вручную колбэк
должен выглядеть опечаткой, а не закрытой дверью.

Книга доступна только на чтение (``state.salary_book`` обновляет джоб
``salary-sync``), так что ни один экран здесь ничего не пишет.
"""
from __future__ import annotations

from typing import Optional

from database import get_bot_member

from ... import salary as salary_book_api
from ...app_ctx import state
from ..cards import (
    salary_analytics_card, salary_card, salary_off_card, salary_owner_card, salary_top_card,
)
from ..keyboards import (
    back_home, salary_accounts_keyboard, salary_analytics_keyboard, salary_keyboard,
    salary_member_keyboard, salary_top_keyboard,
)
from ..reply import safe_edit
from ..router import CallbackRouter, Click

UNKNOWN = "Неизвестная команда"
OWNER_ONLY_VIEWS = ("list", "who", "wan")


async def account_of(sender_id: int) -> Optional[str]:
    """Аккаунт книги, привязанный к ключу этого человека (или None)."""
    book = state.salary_book
    if book is None:
        return None
    return salary_book_api.salary_account_for(await get_bot_member(sender_id), book)


async def visible(sender_id: int, role: str) -> bool:
    """Существует ли раздел зарплат для этого человека.

    Ключ без метки — и ключ с меткой, которой нет в книге, — не должен даже
    догадываться, что раздел есть: ни кнопки, ни ответа на команду.
    """
    if state.salary_book is None:
        return False
    if role == "admin":
        return True
    return await account_of(sender_id) is not None


def month_of(book, requested: Optional[str]) -> str:
    """Запрошенный месяц, если он есть в книге, иначе текущий."""
    if requested and requested in salary_book_api.month_keys(book):
        return requested
    return salary_book_api.current_month(book)


async def render(sender_id: int, role: str, month: Optional[str] = None):
    """Стартовый экран раздела: владельцу — все, парню — только он сам."""
    book = state.salary_book
    if book is None:
        return salary_off_card(), back_home()
    key = month_of(book, month)
    months = salary_book_api.month_keys(book)
    if role == "admin":
        return (salary_owner_card(salary_book_api.owner_overview(book, key)),
                salary_keyboard(key, months, is_owner=True))
    account = await account_of(sender_id)
    if not account:
        return None, None
    return salary_card(salary_book_api.account_analytics(book, account, key)), salary_keyboard(key, months)


async def render_top(sender_id: int, role: str, month: Optional[str] = None):
    book = state.salary_book
    if book is None:
        return salary_off_card(), back_home()
    key = month_of(book, month)
    mine = None if role == "admin" else await account_of(sender_id)
    return (salary_top_card(salary_book_api.top_for_month(book, key), key, mine),
            salary_top_keyboard(key, salary_book_api.month_keys(book)))


async def render_analytics(account: str, month: Optional[str] = None, index: Optional[int] = None):
    book = state.salary_book
    if book is None:
        return salary_off_card(), back_home()
    key = month_of(book, month)
    data = salary_book_api.account_analytics(book, account, key)
    if index is None:
        return salary_analytics_card(data), salary_analytics_keyboard(key, salary_book_api.month_keys(book))
    return salary_analytics_card(data), salary_member_keyboard(key, index, analytics=True)


async def render_member(index: int, month: Optional[str] = None, analytics: bool = False):
    """Карточка одного парня глазами владельца; аккаунт адресуется индексом."""
    book = state.salary_book
    if book is None:
        return salary_off_card(), back_home()
    names = book.names
    if not (0 <= index < len(names)):
        return None, None
    if analytics:
        return await render_analytics(names[index], month, index)
    key = month_of(book, month)
    data = salary_book_api.account_analytics(book, names[index], key)
    return salary_card(data), salary_member_keyboard(key, index)


async def render_accounts(month: Optional[str] = None, page: int = 0):
    book = state.salary_book
    if book is None:
        return salary_off_card(), back_home()
    key = month_of(book, month)
    return (salary_owner_card(salary_book_api.owner_overview(book, key)),
            salary_accounts_keyboard(key, book.names, page))


async def handle_view(click: Click) -> None:
    """`sal:<вид>:<месяц>[:<аргумент>]` — месяц едет в самой кнопке."""
    event = click.event
    if not await visible(click.sender_id, click.role):
        await event.answer(UNKNOWN, alert=True)
        return
    view = click.arg(1, "m")
    month = click.arg(2) or None
    arg = click.arg(3)
    if view in OWNER_ONLY_VIEWS and click.role != "admin":
        await event.answer("Только владелец", alert=True)
        return
    try:
        index = int(arg) if arg else 0
    except ValueError:
        await event.answer("Некорректная команда", alert=True)
        return
    if view == "top":
        text, kb = await render_top(click.sender_id, click.role, month)
    elif view == "an":
        account = await account_of(click.sender_id)
        if not account:
            await event.answer(UNKNOWN, alert=True)
            return
        text, kb = await render_analytics(account, month)
    elif view == "list":
        text, kb = await render_accounts(month, index)
    elif view in ("who", "wan"):
        text, kb = await render_member(index, month, analytics=view == "wan")
    else:
        text, kb = await render(click.sender_id, click.role, month)
    if text is None:
        await event.answer(UNKNOWN, alert=True)
        return
    await safe_edit(event, text, buttons=kb)


async def handle_home(click: Click) -> None:
    """Голая `sal` — кнопка раздела с главного экрана."""
    if not await visible(click.sender_id, click.role):
        await click.event.answer(UNKNOWN, alert=True)
        return
    text, kb = await render(click.sender_id, click.role)
    if text is None:
        await click.event.answer(UNKNOWN, alert=True)
        return
    await safe_edit(click.event, text, buttons=kb)


def register(router: CallbackRouter) -> None:
    router.group("sal")(handle_view)
    router.exact("sal")(handle_home)
