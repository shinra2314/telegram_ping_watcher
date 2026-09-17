"""Currency/crypto converter: landing card, preset pairs, free-text queries.

Reads only the newest ``market_history`` snapshot, so a conversion costs no
network call. Gated by the same ``market`` grant as the rates screen.
"""
from __future__ import annotations

from ...converter import (
    USAGE_HINT, Query, parse_query, render_conversion, render_converter_home,
    supported_text,
)
from ..keyboards import conversion_keyboard, converter_keyboard
from ..pending import InputRejected, prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV
from .market import latest_snapshot

FEATURE = "market"


async def render_home() -> tuple[str, list]:
    return render_converter_home(await latest_snapshot()), converter_keyboard()


async def render_result(query: Query) -> tuple[str, list]:
    snapshot = await latest_snapshot()
    return (
        render_conversion(snapshot, query),
        conversion_keyboard(query.amount, query.src, query.dst),
    )


async def _consume_query(event, pending: dict, raw: str) -> None:
    # Open to every role — the converter is gated by the `market` feature at the
    # button and the command, not by ownership.
    query = parse_query(raw)
    if query is None:
        raise InputRejected(f"❌ **Не понял запрос.**\n{USAGE_HINT}\n{DIV}\n{supported_text()}")
    text, buttons = await render_result(query)
    await event.respond(text, buttons=buttons)


QUERY_INPUT = register_prompt(
    "convert", f"Что пересчитать?\n{USAGE_HINT}", _consume_query, admin=False)


async def handle(click: Click) -> None:
    if click.data == "cv:in":
        await prompt_pending(click.event, QUERY_INPUT)
        return
    if len(click.seg) == 5 and click.seg[1] == "p":
        try:
            amount = float(click.seg[2])
        except ValueError:
            amount = 1.0
        text, kb = await render_result(Query(amount, click.seg[3], click.seg[4]))
    else:
        text, kb = await render_home()
    await safe_edit(click.event, text, buttons=kb)


def register(router: CallbackRouter) -> None:
    router.group("cv", feature=FEATURE)(handle)
