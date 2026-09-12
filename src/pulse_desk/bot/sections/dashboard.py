"""Пульт: та же сводка, что была вкладкой «Дашборд», одной картинкой.

Двенадцать счётчиков, тренд по дням и прогресс скана в тексте сообщения —
столбик цифр, который с телефона не читается. Поэтому экран рисуется; текстовая
сводка остаётся запасным вариантом и уходит в дело, когда Pillow недоступен
или рендер не удался.

«Что разобрать» — не украшение: каждая строка списка получает кнопку, которая
открывает ленту уже с нужным фильтром. В вебе это делал `data-focus-kind`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from telethon import Button

from ...analytics import build_analytics
from ...app_ctx import state
from ...dashboard import collect_dashboard
from ..media import cache_key, render_cached, show_screen
from ..render.screens import build_dashboard_card
from ..router import CallbackRouter, Click
from ..views import DIV, FeedFilter
from .home import render_summary

FEATURE = "stats"

# Куда ведёт строка «что разобрать». Ключ — `kind` из
# dashboard.build_dashboard_summary; в вебе то же решал `data-focus-kind`.
# Разбор упоминаний живёт в ленте, а решение по розыгрышу — на доске, поэтому
# цели разные: одинаковая кнопка «Розыгрыши» дважды подряд ничего не значит.
FOCUS_TARGETS: dict[str, tuple[str, bytes]] = {
    "new": ("🆕 Новые", FeedFilter(status="n").cb()),
    "important": ("🔥 Важные", FeedFilter(type="i").cb()),
    "giveaway-action": ("🎁 К действию", b"menu_giveaways"),
    "manual": ("🛡 Ручная проверка", b"menu_giveaways"),
    "scan": ("🔄 Скан", b"menu_scan"),
    "accounts": ("🛰 Аккаунты", b"menu_status"),
    "scan-error": ("🛰 Статус", b"menu_status"),
    "events": ("📜 Логи", b"menu_logs"),
}
# Эти цели владельческие: гостю такая кнопка откроет только отказ.
OWNER_FOCUS = {"scan", "accounts", "scan-error", "events"}


def _card_key(summary: dict[str, Any], analytics: dict[str, Any], note: str = "") -> str:
    """Ключ кэша: те же числа — тот же файл, рисуем один раз."""
    counts = summary.get("counts") or {}
    scan = summary.get("scan_progress") or {}
    daily = analytics.get("daily") or []
    return cache_key(
        summary.get("health_level"),
        *(counts.get(k) for k in sorted(counts)),
        scan.get("percent"), scan.get("running"),
        len(daily), (daily[-1] if daily else None), note,
    )


def _last_scan_note() -> str:
    """Когда скан закончился в прошлый раз — подпись вместо пустого прогресса."""
    finished = state.last_scan_finished_at
    if not finished:
        return "не было"
    return f"{finished:%d.%m %H:%M}"


def keyboard(summary: dict[str, Any], is_admin: bool) -> list[list[Button]]:
    """Кнопки пульта: разбор по фокусам плюс переходы в соседние разделы."""
    focus: list[Button] = []
    seen: set[bytes] = set()
    for item in (summary.get("attention") or []):
        kind = str(item.get("kind") or "")
        target = FOCUS_TARGETS.get(kind)
        if not target or (kind in OWNER_FOCUS and not is_admin):
            continue
        label, data = target
        # Два сигнала могут вести в одно место — кнопка нужна одна.
        if data in seen:
            continue
        seen.add(data)
        focus.append(Button.inline(label, data))
    rows = [focus[i:i + 2] for i in range(0, len(focus), 2)]
    nav = [Button.inline("🕐 Лента", FeedFilter().cb())]
    if b"menu_giveaways" not in seen:
        nav.append(Button.inline("🎁 Розыгрыши", b"menu_giveaways"))
    rows.append(nav)
    if is_admin:
        extra = [b for b in (Button.inline("🛰 Статус", b"menu_status"),
                             Button.inline("🔄 Скан", b"menu_scan"))
                 if b.data not in seen]
        if extra:
            rows.append(extra)
    rows.append([
        Button.inline("⬅️ Домой", b"menu_main"),
        Button.inline("🔄 Обновить", b"menu_summary"),
    ])
    return rows


def caption(summary: dict[str, Any]) -> str:
    """Подпись под картинкой: заголовок и то, что требует решения."""
    lines = [f"🛰 **{summary.get('headline') or 'Пульт'}**", DIV]
    for item in (summary.get("attention") or [])[:4]:
        lines.append(f"• {item.get('title')}: `{item.get('value')}`")
    return "\n".join(lines)


async def render(is_admin: bool = True) -> tuple[str, list[list[Button]], Optional[str]]:
    """Текст, кнопки и путь к картинке (None — раздел покажется текстом)."""
    summary = await collect_dashboard(include_problems=is_admin)
    analytics = await build_analytics()
    note = _last_scan_note()
    image = await render_cached(
        "dashboard", _card_key(summary, analytics, note),
        lambda path: build_dashboard_card(summary, analytics, Path(path), scan_note=note),
    )
    text = caption(summary) if image else await render_summary()
    return text, keyboard(summary, is_admin), image


async def handle(click: Click) -> None:
    text, kb, image = await render(is_admin=click.role == "admin")
    await show_screen(click.event, text, buttons=kb, image=image)


def register(router: CallbackRouter) -> None:
    # Заменяет прежний текстовый `menu_summary`: это тот же раздел, просто
    # нарисованный. Грант тот же, что был у сводки.
    router.exact("menu_summary", feature=FEATURE)(handle)
