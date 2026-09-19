"""Экраны бота, нарисованные картинкой: пульт и рынок.

Веб показывал двенадцать плиток, спарклайны и прогресс скана одновременно —
в тексте сообщения это столбик цифр, который не читается с телефона. Поэтому
такие экраны рисуются, а не печатаются.

Контракт тот же, что у ``digest_cards.build_digest_cards``: **никогда не
бросать**. Нет Pillow, нет шрифтов, каталог недоступен — возвращается ``None``,
и раздел показывает текстовый вариант. Картинка не может быть причиной, по
которой кнопка не открылась.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import math

from .canvas import (
    AMBER, CITRON, CYAN, DIM, DOWN, FLAT, H, MUTED, SS, UP, W, WHITE, Rect,
    canvas, ellipsize, empty_state, fade_rule, fit, font, footer, header, px, progress, save,
    scan_line, sparkline, squarify, stat_row, tile, tracked,
)

# Тон из dashboard.build_dashboard_summary -> цвет. Цвет здесь несёт значение
# (как на хитмапе), поэтому citron-правило тут не действует.
TONE_COLOR = {"good": UP, "warn": AMBER, "bad": DOWN, "info": CYAN}


def group(value: Any) -> str:
    """Счётчик с узким пробелом между разрядами: 128456 не читается, 128 456 — да."""
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def pct(change: float) -> str:
    """Движение в процентах. Ровный ноль печатается без знака: «-0.0%» читается
    как падение, которого не было."""
    if abs(change) < 0.05:
        return "0.0%"
    return f"{change:+.1f}%"


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _daily_series(analytics: dict[str, Any], days: int = 14) -> list[float]:
    """Хвост ежедневных счётчиков из аналитики — ряд для спарклайна."""
    daily = analytics.get("daily") or []
    series: list[float] = []
    for row in daily[-days:]:
        if isinstance(row, dict):
            series.append(float(_num(row.get("count") or row.get("total"))))
        elif isinstance(row, (int, float)):
            series.append(float(row))
    return series


def _attention_rows(img, items: Sequence[dict[str, Any]], top: int, limit: int = 4) -> None:
    """Список «что разобрать»: цветная плитка на строку, значение справа."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    row_h = px(96)
    gap = px(14)
    left, right = px(64), W * SS - px(64)
    for i, item in enumerate(items[:limit]):
        y = top + i * (row_h + gap)
        color = TONE_COLOR.get(str(item.get("tone")), CYAN)
        tile(img, (left, y, right - left, row_h), color, 0.35, marker=True)
        title = ellipsize(d, str(item.get("title") or ""), font("name", px(30)), right - left - px(220))
        d.text((left + px(22), y + px(16)), title, font=font("name", px(30)), fill=WHITE + (255,))
        text = ellipsize(d, str(item.get("text") or ""), font("label", px(19)), right - left - px(240))
        d.text((left + px(22), y + px(58)), text, font=font("label", px(19)), fill=DIM + (255,))
        value = str(item.get("value") or "")
        if value:
            face = fit(d, value, "num", px(200), px(44), px(22)) or font("num", px(22))
            d.text((right - px(22) - d.textlength(value, font=face), y + px(26)),
                   value, font=face, fill=color + (255,))


def build_dashboard_card(summary: dict[str, Any], analytics: dict[str, Any],
                         path: Path, scan_note: str = "") -> Optional[str]:
    """Пульт одной картинкой: счётчики, тренд, прогресс скана, что разобрать."""
    try:
        counts = summary.get("counts") or {}
        scan = summary.get("scan_progress") or {}
        img = canvas()
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        accent = TONE_COLOR.get(str(summary.get("health_level")), CITRON)
        header(img, "Pulse Desk", str(summary.get("headline") or "Пульт"),
               f"PD·02 // {stamp}", accent)
        stat_row(img, [
            ("всего", group(_num(counts.get("total_pings"))), WHITE),
            ("новых", group(_num(counts.get("new_pings"))), CITRON),
            ("важных", group(_num(counts.get("important"))), AMBER),
        ], px(190))
        stat_row(img, [
            ("к действию", str(_num(counts.get("giveaway_need_action"))), AMBER),
            ("ждут итога", str(_num(counts.get("giveaway_waiting_result"))), CYAN),
            ("избранное", group(_num(counts.get("favorites"))), WHITE),
        ], px(330))

        series = _daily_series(analytics)
        chart_top = px(470)
        from PIL import ImageDraw

        d = ImageDraw.Draw(img, "RGBA")
        tracked(d, (px(64), chart_top), "УПОМИНАНИЙ В ДЕНЬ", font("label", px(15)), px(2.2),
                MUTED + (255,))
        if series:
            sparkline(img, (px(64), chart_top + px(34), W * SS - px(128), px(170)), series, accent)
        else:
            d.text((px(64), chart_top + px(60)), "нет данных за период",
                   font=font("label", px(20)), fill=MUTED + (255,))
        fade_rule(img, px(64), chart_top + px(224), W * SS - px(128), accent)

        # Идёт скан — показываем, сколько пройдено. Не идёт — пустой жёлоб не
        # несёт ничего, поэтому на его месте стоит время последнего прохода.
        bar_top = px(740)
        running = bool(scan.get("running"))
        percent = _num(scan.get("percent"))
        failed = bool(scan.get("last_error"))
        tracked(d, (px(64), bar_top), "СКАН ИДЁТ" if running else "ПОСЛЕДНИЙ СКАН",
                font("label", px(15)), px(2.2), MUTED + (255,))
        value = f"{percent}%" if running else (scan_note or ("ошибка" if failed else "—"))
        face = font("num", px(24))
        d.text((W * SS - px(64) - d.textlength(value, font=face), bar_top - px(4)),
               value, font=face, fill=(AMBER if running else (DOWN if failed else DIM)) + (255,))
        if running:
            progress(img, (px(64), bar_top + px(30), W * SS - px(128), px(14)),
                     percent / 100, AMBER)
        else:
            fade_rule(img, px(64), bar_top + px(34), W * SS - px(128),
                      DOWN if failed else FLAT)

        _attention_rows(img, summary.get("attention") or [], px(830))
        footer(img, f"PD·02 // {_num(counts.get('total_channels'))} CHANNELS · "
                    f"{len(summary.get('attention') or [])} SIGNALS")
        return save(img, path)
    except Exception:  # pragma: no cover - рендер best effort
        from ...app_ctx import logger

        logger.exception("Dashboard card rendering failed; falling back to text")
        return None


def build_member_card(report: dict[str, Any], accounts: Sequence[str],
                      path: Path) -> Optional[str]:
    """Сводка держателя ключа картинкой: только его аккаунты.

    Пульт владельца сюда не годится — там прогресс скана, каналы и проблемы
    аккаунтов, то есть чужая кухня. Числа берутся из
    ``analytics.build_panel_report``, того же отчёта, по которому считает панель.
    """
    try:
        summary = report.get("summary") or {}
        img = canvas()
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        names = ", ".join(f"@{name}" for name in accounts) or "все аккаунты"
        from PIL import ImageDraw

        d = ImageDraw.Draw(img, "RGBA")
        header(img, "Pulse Desk", ellipsize(d, names, font("name", px(28)), W * SS - px(420)),
               f"PD·05 // {stamp}", CITRON)
        stat_row(img, [
            ("упоминаний", group(_num(summary.get("total"))), WHITE),
            ("побед", group(_num(summary.get("wins"))), CITRON),
            ("розыгрышей", group(_num(summary.get("giveaways"))), AMBER),
        ], px(190))
        stat_row(img, [
            ("за 24 часа", str(_num(summary.get("last_24h"))), CYAN),
            ("за 7 дней", str(_num(summary.get("last_7d"))), WHITE),
            ("win rate", f"{summary.get('win_rate', 0)}%", UP),
        ], px(330))

        series = [float(_num(row.get("total"))) for row in (report.get("daily") or [])
                  if isinstance(row, dict)]
        chart_top = px(470)
        tracked(d, (px(64), chart_top), "УПОМИНАНИЙ В ДЕНЬ", font("label", px(15)), px(2.2),
                MUTED + (255,))
        if any(series):
            sparkline(img, (px(64), chart_top + px(34), W * SS - px(128), px(170)), series, CITRON)
        else:
            d.text((px(64), chart_top + px(60)), "нет данных за период",
                   font=font("label", px(20)), fill=MUTED + (255,))
        fade_rule(img, px(64), chart_top + px(224), W * SS - px(128), CITRON)

        # «Что разобрать» у владельца — очередь действий; у гостя такой очереди
        # нет, поэтому на её месте чаты, где его упоминают чаще всего. Список
        # начинается сразу под графиком: у владельца между ними стоит полоса
        # скана, а без неё осталась бы пустая треть карточки.
        rows = [
            {"title": str(chat.get("chat") or "?"),
             "text": f"{_num(chat.get('wins'))} побед · {_num(chat.get('giveaways'))} розыгрышей",
             "value": group(_num(chat.get("count"))),
             "tone": "good" if _num(chat.get("wins")) else "info"}
            for chat in (report.get("chats") or [])[:4]
        ]
        if rows:
            tracked(d, (px(64), px(748)), "ГДЕ УПОМИНАЮТ ЧАЩЕ", font("label", px(15)), px(2.2),
                    MUTED + (255,))
            _attention_rows(img, rows, px(790))
        else:
            empty_state(img, "Упоминаний ваших аккаунтов пока нет")
        scope_note = f"{len(accounts)} ACCOUNT" + ("S" if len(accounts) != 1 else "") if accounts else "ALL ACCOUNTS"
        footer(img, f"PD·05 // {scope_note} · {int(report.get('window_days') or 30)} DAYS")
        return save(img, path)
    except Exception:  # pragma: no cover - рендер best effort
        from ...app_ctx import logger

        logger.exception("Member card rendering failed; falling back to text")
        return None


# ---------------------------------------------------------------- отчёт

def build_report_card(data: Any, path: Path) -> Optional[str]:
    """Недельный/месячный отчёт одной картинкой (report.ReportData). Никогда не бросает."""
    try:
        from PIL import ImageDraw

        from ...report import pct, short_channel, trend

        img = canvas()
        title = "Неделя" if data.kind == "week" else "Месяц"
        header(img, "Pulse Desk", f"{title} · {data.label}", f"PD·04 // {datetime.now():%d.%m.%Y}", AMBER)
        stat_row(img, [
            ("побед", group(data.wins), AMBER),
            ("забрано", pct(data.claim_rate), UP if (data.claim_rate or 0) >= 0.5 else AMBER),
            ("ждут", group(data.open_wins), CYAN),
        ], px(190))
        money = f"${data.unclaimed_usd:,.0f}".replace(",", " ") if data.unclaimed_usd else "—"
        book = (f"${data.book_won_usd:,.0f}".replace(",", " ") if data.book_won_usd is not None else "—")
        stat_row(img, [
            ("незабрано", money, CITRON),
            ("по книге", book, WHITE),
            ("розыгрышей", group(data.giveaways), CITRON),
        ], px(330))

        d = ImageDraw.Draw(img, "RGBA")
        chart_top = px(470)
        tracked(d, (px(64), chart_top), "ПОБЕД В ДЕНЬ", font("label", px(15)), px(2.2), MUTED + (255,))
        note = trend(data.wins, data.prev_wins)
        if note:
            face = font("label", px(17))
            d.text((W * SS - px(64) - d.textlength(note, font=face), chart_top - px(2)),
                   note, font=face, fill=DIM + (255,))
        if sum(data.daily_wins) and len(data.daily_wins) > 1:
            sparkline(img, (px(64), chart_top + px(34), W * SS - px(128), px(170)), data.daily_wins, AMBER)
        else:
            d.text((px(64), chart_top + px(60)), "побед за период нет",
                   font=font("label", px(20)), fill=MUTED + (255,))
        fade_rule(img, px(64), chart_top + px(224), W * SS - px(128), AMBER)

        list_top = px(740)
        tracked(d, (px(64), list_top), "КАНАЛЫ, КОТОРЫЕ ПЛАТИЛИ", font("label", px(15)), px(2.2),
                MUTED + (255,))
        if data.top_channels:
            items = [{"title": short_channel(name, 40), "text": "@" + name.split(" (@")[1].rstrip(")") if " (@" in name else "",
                      "value": f"{count}", "tone": "warn"}
                     for name, count in data.top_channels[:4]]
            _attention_rows(img, items, list_top + px(40))
        else:
            d.text((px(64), list_top + px(50)), "—", font=font("label", px(22)), fill=MUTED + (255,))
        payout = ""
        if data.book_payout_usd is not None:
            payout = f" · PAYOUT ${data.book_payout_usd:,.0f} / PAID ${(data.book_paid_usd or 0):,.0f}".replace(",", " ")
        footer(img, f"PD·04 // {data.wins} WINS · {data.claimed} CLAIMED · {data.scam} SCAM{payout}")
        return save(img, path)
    except Exception:  # pragma: no cover - рендер best effort
        from ...app_ctx import logger

        logger.exception("Report card rendering failed; falling back to text")
        return None


# ---------------------------------------------------------------- рынок
# Перенесено из digest_cards: хитмап — это экран рынка, а он одинаковый и в
# дайджесте, и в разделе «Курсы». Две копии разошлись бы на первой же правке —
# в одной из них порядок снимков уже был перепутан (get_market_history отдаёт
# новейшее первым, и `snapshots[0]` — это «сейчас», а не «сутки назад»).

# Market-cap weights used only when a snapshot predates `usd_market_cap`
# (added to the CoinGecko call in 2026-09). Rough order of magnitude — they
# decide tile area on old data, never a number printed on the card.
FALLBACK_CAP: dict[str, float] = {
    "bitcoin": 1000, "ethereum": 380, "tether": 130, "binancecoin": 90,
    "solana": 70, "the-open-network": 12, "notcoin": 2, "dogs-2": 1,
}


def market_tiles(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ticker, price, day move and cap weight per tracked coin, biggest first."""
    from ...digest import DIGEST_ASSETS, _change_pct, _price

    if not snapshots:
        return []
    latest, oldest = snapshots[0], snapshots[-1]
    tiles: list[dict[str, Any]] = []
    for asset, _emoji, ticker, digits in DIGEST_ASSETS:
        price = _price(latest, asset)
        if price is None:
            continue
        try:
            cap = float((latest.get(asset) or {}).get("usd_market_cap") or 0.0)
        except (TypeError, ValueError):
            cap = 0.0
        tiles.append({
            "ticker": ticker,
            "price": price,
            "digits": digits,
            "pct": _change_pct(latest, oldest, asset, "usd") or 0.0,
            "weight": cap or FALLBACK_CAP.get(asset, 1.0),
        })
    return damp_weights(sorted(tiles, key=lambda t: -t["weight"]))


def damp_weights(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Square-root the cap weights so every coin still gets a readable tile.

    Bitcoin's cap is ~300× TON's; laid out linearly the small coins collapse
    into slivers with no room for a ticker. The square root keeps the ranking
    and the sense of scale while compressing the range to ~17×, and a floor at
    a fifteenth of the leader guarantees the last tile can hold its label.
    """
    if not tiles:
        return tiles
    # `item`, not `tile`: that name is the canvas drawing function imported above.
    for item in tiles:
        item["weight"] = math.sqrt(max(float(item["weight"]), 0.0))
    top = max(t["weight"] for t in tiles) or 1.0
    for item in tiles:
        item["weight"] = max(item["weight"], top / 15.0)
    return tiles


def _draw_market_card(snapshots: list[dict[str, Any]], path: Path, period_label: str) -> str:
    from PIL import ImageDraw

    from ...digest import _fmt_price

    img = canvas()
    tiles = market_tiles(snapshots)
    moves = [t["pct"] for t in tiles if t["ticker"] != "USDT"]
    avg = sum(moves) / len(moves) if moves else 0.0
    accent = UP if avg > 0.05 else (DOWN if avg < -0.05 else FLAT)

    subtitle = f"Курсы {period_label}" + (f" · {len(tiles)} активов" if tiles else "")
    header(img, "КРИПТА", subtitle, f"PD·02 // {datetime.now():%d.%m.%Y}", accent)

    d = ImageDraw.Draw(img, "RGBA")
    if tiles:
        ranked = sorted(tiles, key=lambda t: -t["pct"])
        stat_row(img, [
            ("Рынок", f"{'+' if avg > 0 else ''}{avg:.2f}%", accent),
            ("Лидер", f"{ranked[0]['ticker']} {ranked[0]['pct']:+.2f}%", UP),
            ("Аутсайдер", f"{ranked[-1]['ticker']} {ranked[-1]['pct']:+.2f}%", DOWN),
        ], px(190))

    scan_line(img, px(330), accent)

    area: Rect = (px(64), px(360), W * SS - px(128), H * SS - px(360) - px(110))
    if not tiles:
        empty_state(img, "Курсы пока недоступны")
        footer(img, "PD·02 // NO DATA")
        return save(img, path)

    for item, rect in zip(tiles, squarify([t["weight"] for t in tiles], area)):
        x, y = rect[0], rect[1]
        w, h = rect[2] - px(8), rect[3] - px(8)
        if w < px(24) or h < px(24):
            continue
        pct = item["pct"]
        color = UP if pct > 0.05 else (DOWN if pct < -0.05 else FLAT)
        # 5 % is a strong day for these coins — cap the heat there.
        heat = min(1.0, abs(pct) / 5.0)
        tile(img, (x, y, w, h), color, heat, marker=False)

        pad = px(14)
        inner_w = w - 2 * pad
        ticker_font = fit(d, item["ticker"], "name", inner_w, int(min(h * 0.30, px(104))), px(13))
        if ticker_font is None:
            continue
        pct_text = f"{'+' if pct > 0 else ''}{pct:.2f}%"
        price_text = f"${_fmt_price(item['price'], item['digits'])}"
        block = [(item["ticker"], ticker_font, WHITE)]
        # A readout below ~13 px is noise on a phone screen: drop the line
        # rather than print something the eye cannot resolve.
        pct_font = fit(d, pct_text, "num", inner_w, max(px(14), int(ticker_font.size * 0.46)), px(14))
        if pct_font is not None:
            block.append((pct_text, pct_font, color))
        price_font = fit(d, price_text, "num", inner_w, max(px(13), int(ticker_font.size * 0.28)), px(13))
        if price_font is not None:
            block.append((price_text, price_font, DIM))

        heights = [f.size * 1.24 for _, f, _c in block]
        while len(block) > 1 and sum(heights) > h - 2 * pad - px(10):
            block.pop()
            heights.pop()
        ty = y + (h - sum(heights)) / 2 - px(4)
        for (text, face, fill), line_h in zip(block, heights):
            d.text((x + w / 2 - d.textlength(text, font=face) / 2, ty), text,
                   font=face, fill=fill + (255,))
            ty += line_h

        # A gauge on the tile's floor: how far this move is toward a 5 % day.
        # Inset from the keyline so it reads as an instrument scale, not a
        # thicker border — the fill alone cannot be ranked by eye.
        if h > px(78) and w > px(90) and heat >= 0.03:
            gx0, gx1 = x + pad, x + w - pad
            gy = y + h - pad
            d.rectangle([gx0, gy, gx1, gy + 4 * SS], fill=(255, 255, 255, 18))
            d.rectangle([gx0, gy, gx0 + (gx1 - gx0) * heat, gy + 4 * SS], fill=color + (255,))

    uah = uah_line(snapshots)
    footer(img, f"PD·02 // {len(tiles)} ASSETS" + (f"  ·  {uah}" if uah else ""))
    return save(img, path)


def uah_line(snapshots: list[dict[str, Any]]) -> str:
    from ...digest import UAH_ASSETS, _fmt_price, _price

    if not snapshots:
        return ""
    parts = []
    for asset, ticker, digits in UAH_ASSETS:
        value = _price(snapshots[0], asset, "uah")
        if value is not None:
            parts.append(f"{ticker} ₴{_fmt_price(value, digits)}")
    return "  ·  ".join(parts)


def build_market_card(snapshots: list[dict[str, Any]], path: Path,
                      period_label: str = "за 24 ч") -> Optional[str]:
    """Экран курсов картинкой. Как и остальные, молчаливо сдаётся в текст."""
    try:
        return _draw_market_card(snapshots, path, period_label)
    except Exception:  # pragma: no cover - рендер best effort
        from ...app_ctx import logger

        logger.exception("Market card rendering failed; falling back to text")
        return None
