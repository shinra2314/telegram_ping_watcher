"""Курсы: хитмап рынка картинкой, период одной кнопкой.

Веб рисовал восемь карточек монет, четыре графика Chart.js и таблицу в шесть
колонок. В сообщении это не живёт, а на картинке — ровно то же самое: площадь
плитки это капитализация, цвет — движение за период. Карточка общая с
дайджестом (`bot/render/screens`), так что рынок везде выглядит одинаково.

Период меняет только окно снимков, из которых считается движение: «за 24 ч» —
это первый и последний снимок суток, а не поле `usd_24h_change` у биржи.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from telethon import Button

from database import get_market_history

from ..media import cache_key, render_cached, show_screen
from ..render.screens import build_market_card, market_tiles, uah_line
from ..router import CallbackRouter, Click
from ..views import DIV

FEATURE = "market"
# code -> (часы окна, подпись). Больше недели смысла не имеет: `market-monitor`
# пишет снимок раз в MARKET_POLL_SECONDS и старые чистятся по ретеншну.
PERIODS: tuple[tuple[str, int, str], ...] = (
    ("d", 24, "за 24 ч"),
    ("w", 24 * 7, "за неделю"),
)
PERIOD_LABEL = {code: label for code, _hours, label in PERIODS}
PERIOD_HOURS = {code: hours for code, hours, _label in PERIODS}
DEFAULT_PERIOD = "d"
# Снимков в окне: по одному на опрос, с запасом на частый поллинг.
WINDOW_LIMIT = 400


async def snapshots_for(period: str) -> list[dict]:
    hours = PERIOD_HOURS.get(period, PERIOD_HOURS[DEFAULT_PERIOD])
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = await get_market_history(limit=WINDOW_LIMIT, since_iso=since)
    if not rows:
        # Окно пустое (свежая установка, долгий простой) — покажем хоть что-то.
        rows = await get_market_history(limit=2)
    return rows


async def latest_snapshot() -> Optional[dict]:
    """Newest market snapshot, or None while the market loop is cold."""
    rows = await get_market_history(limit=1)
    return rows[0] if rows else None


async def render_rates(period: str = DEFAULT_PERIOD) -> str:
    """Текстовый вариант — он же подпись под картинкой."""
    snapshots = await snapshots_for(period)
    tiles = market_tiles(snapshots)
    if not tiles:
        return "💹 **Курсы**\n" + DIV + "\n📉 __Данные пока недоступны.__"
    ranked = sorted(tiles, key=lambda t: -t["pct"])
    lines = [f"💹 **Курсы** · {PERIOD_LABEL.get(period, '')}".strip(), DIV]
    lines.append(f"📈 {ranked[0]['ticker']} `{ranked[0]['pct']:+.2f}%`   "
                 f"📉 {ranked[-1]['ticker']} `{ranked[-1]['pct']:+.2f}%`")
    uah = uah_line(snapshots)
    if uah:
        lines.append(f"🇺🇦 {uah}")
    return "\n".join(lines)


def keyboard(period: str) -> list[list[Button]]:
    return [
        [Button.inline(f"▸{label}" if code == period else label, f"mk:p:{code}".encode())
         for code, _hours, label in PERIODS],
        [Button.inline("💱 Конвертер", b"cv")],
        [Button.inline("⬅️ Домой", b"menu_main"),
         Button.inline("🔄 Обновить", f"mk:p:{period}".encode())],
    ]


async def render(period: str = DEFAULT_PERIOD) -> tuple[str, list[list[Button]], Optional[str]]:
    snapshots = await snapshots_for(period)
    newest = (snapshots[0] or {}).get("fetched_at_iso") if snapshots else None
    image = await render_cached(
        f"market_{period}", cache_key(newest, len(snapshots)),
        lambda path: build_market_card(snapshots, Path(path), PERIOD_LABEL.get(period, "")),
    )
    return await render_rates(period), keyboard(period), image


async def handle(click: Click) -> None:
    period = click.arg(2, DEFAULT_PERIOD) if click.arg(1) == "p" else DEFAULT_PERIOD
    if period not in PERIOD_HOURS:
        period = DEFAULT_PERIOD
    text, kb, image = await render(period)
    await show_screen(click.event, text, buttons=kb, image=image)


def register(router: CallbackRouter) -> None:
    router.exact("menu_market", feature=FEATURE)(handle)
    router.group("mk", feature=FEATURE)(handle)
