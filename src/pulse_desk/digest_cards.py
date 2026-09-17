"""Daily digest as two Aperture treemap cards instead of a wall of text.

Card 1 — the day's pings, grouped into wins / giveaways / mentions, one tile
per chat sized by how many pings it produced. Card 2 — the tracked coins,
sized by market cap and coloured by the day move, the way a market heatmap is
read at a glance.

Everything here is best effort: ``build_digest_cards`` never raises, and the
digest falls back to its text form when Pillow is missing or a render fails.

The layout maths (``squarify``) is pure and unit-tested; the drawing is not.
Palette and geometry follow assets/bot/DESIGN_PHILOSOPHY.md — graphite body,
oscilloscope grid, instrument type. Colour is the one place the crypto card
departs from the citron rule: on a heatmap the colour *is* the value, exactly
like the status dots in the emoji set.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

# The drawing vocabulary now lives in bot/render/canvas.py, shared with every
# other screen the bot renders; the names stay private here so the card code
# below reads exactly as it did.
# Хитмап рынка теперь общий с экраном «Курсы» в боте — одна реализация.
from .bot.render.screens import (  # noqa: F401
    FALLBACK_CAP, damp_weights as _damp_weights, market_tiles as _market_tiles,
    uah_line as _uah_line,
)
from .bot.render.screens import _draw_market_card as _market_card
from .bot.render.canvas import (  # noqa: F401
    AMBER, CITRON, GROUP_COLOR, H, MUTED, SS, W, WHITE, Rect, squarify,
    canvas as _canvas, ellipsize as _ellipsize, empty_state as _empty_state,
    fade_rule as _fade_rule, font as _font, footer as _footer, header as _header,
    px as _px, save as _save, scan_line as _scan_line, stat_row as _stat_row,
    tile as _tile, tracked as _tracked, prune as _prune_dir,
)

# ------------------------------------------------------------ data shaping

GROUPS: list[tuple[str, str]] = [
    ("wins", "ПОБЕДЫ"),
    ("giveaways", "РОЗЫГРЫШИ"),
    ("mentions", "УПОМИНАНИЯ"),
]

# FALLBACK_CAP (market-cap weights for snapshots predating `usd_market_cap`) is
# imported from bot/render/screens.py above — a second copy here shadowed it.


def _bucket(ping: dict[str, Any]) -> str:
    if ping.get("is_win"):
        return "wins"
    if ping.get("is_giveaway"):
        return "giveaways"
    return "mentions"


def group_pings(pings: Sequence[dict[str, Any]]) -> list[tuple[str, str, int, list[tuple[str, int]]]]:
    """(key, label, total, [(chat, count), …] desc) per non-empty group."""
    buckets: dict[str, dict[str, int]] = {key: {} for key, _ in GROUPS}
    for ping in pings or []:
        chat = str(ping.get("chat") or "?").strip() or "?"
        chats = buckets[_bucket(ping)]
        chats[chat] = chats.get(chat, 0) + 1
    result = []
    for key, label in GROUPS:
        chats = sorted(buckets[key].items(), key=lambda kv: (-kv[1], kv[0]))
        if chats:
            result.append((key, label, sum(c for _, c in chats), chats))
    return result


def _giveaway_card(pings: Sequence[dict[str, Any]], stats: dict[str, int],
                   prev: Optional[dict[str, int]], path: Path, period_label: str) -> str:
    from PIL import ImageDraw

    img = _canvas()
    delta = ""
    if prev is not None:
        diff = stats["total"] - prev["total"]
        delta = f"  ·  вчера {prev['total']} ({'+' if diff > 0 else ('−' if diff else '±')}{abs(diff)})"
    _header(img, "PULSE DESK", f"Пинги {period_label}{delta}",
            f"PD·01 // {datetime.now():%d.%m.%Y}", CITRON)

    d = ImageDraw.Draw(img, "RGBA")
    groups = group_pings(pings)
    totals = {key: total for key, _label, total, _chats in groups}
    # Stat row — the same three classes the treemap below breaks down, so the
    # numbers at the top and the tiles under them always add up.
    _stat_row(img, [("Всего", str(stats["total"]), WHITE),
                    ("Побед", str(totals.get("wins", 0)), AMBER),
                    ("Розыгрышей", str(totals.get("giveaways", 0)), CITRON)], _px(190))

    _scan_line(img, _px(330), CITRON)

    area: Rect = (_px(64), _px(360), W * SS - _px(128), H * SS - _px(360) - _px(110))
    if not groups:
        _empty_state(img, f"Нет пингов {period_label}")
        _footer(img, "PD·01 // NO SIGNAL")
        return _save(img, path)

    gap = _px(10)
    for (key, label, total, chats), rect in zip(groups, squarify([g[2] for g in groups], area)):
        gx, gy, gw, gh = rect[0], rect[1], max(0.0, rect[2] - gap), max(0.0, rect[3] - gap)
        if gw < _px(40) or gh < _px(40):
            continue
        color = GROUP_COLOR[key]
        head = _px(40)
        label_font = _font("label", _px(18))
        label_w = _tracked(d, (gx, gy), label, label_font, _px(2.6), color + (230,))
        count_x = gx + label_w + _px(16)
        d.text((count_x, gy), str(total), font=label_font, fill=MUTED + (255,))
        # Hairline from the label out to the group's edge — the class owns the
        # whole band under it, and the eye is told where that band ends.
        rule_x = count_x + d.textlength(str(total), font=label_font) + _px(14)
        _fade_rule(img, rule_x, gy + _px(9), max(0.0, gx + gw - rule_x), color)
        inner: Rect = (gx, gy + head, gw, gh - head)
        counts = [c for _, c in chats]
        for (chat, count), tile in zip(chats, squarify(counts, inner)):
            tw, th = tile[2] - _px(6), tile[3] - _px(6)
            if tw < _px(26) or th < _px(26):
                continue
            strength = min(1.0, count / max(counts)) if counts else 0.0
            _tile(img, (tile[0], tile[1], tw, th), color, strength)
            pad = _px(10)
            # One type scale for the whole card: the name is sized by the tile,
            # then clipped — shrinking it to fit would make every tile a
            # different typeface size and the grid stops reading as a system.
            # Type scales with the tile (both axes) so the biggest chat reads
            # first; the caps only ever bind on a near-empty day's one tile.
            name_font = _font("name", int(max(_px(12), min(_px(64), th * 0.18, tw * 0.16))))
            count_font = _font("num", int(max(_px(19), min(_px(104), th * 0.27, tw * 0.27))))
            line_h = name_font.size * 1.28
            if th - 2 * pad < line_h + count_font.size * 0.9:
                # Too short for both lines — the number is the information.
                d.text((tile[0] + tw / 2 - d.textlength(str(count), font=name_font) / 2,
                        tile[1] + th / 2 - name_font.size * 0.62),
                       str(count), font=name_font, fill=color + (255,))
                continue
            top = tile[1] + (th - line_h - count_font.size * 1.2) / 2
            d.text((tile[0] + pad, top),
                   _ellipsize(d, chat.lstrip("@"), name_font, tw - 2 * pad),
                   font=name_font, fill=WHITE + (255,))
            d.text((tile[0] + pad, top + line_h), str(count),
                   font=count_font, fill=color + (255,))

    _footer(img, f"PD·01 // {stats['total']} SIGNALS  ·  {len(groups)} CLASSES")
    return _save(img, path)


# ------------------------------------------------------------------ public

CARD_DIR_NAME = "digest"
_KEEP_DAYS = 3


def _prune(directory: Path) -> None:
    """Drop cards older than a few days — delayed member copies still read them."""
    _prune_dir(directory, "digest_*.png", _KEEP_DAYS)


def build_digest_cards(
    pings: Sequence[dict[str, Any]],
    out_dir: Path,
    *,
    period_label: str = "за последние 24 ч",
    prev_pings: Optional[Sequence[dict[str, Any]]] = None,
    market: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """Render both cards into `out_dir`. Returns paths, or [] if rendering failed."""
    from .digest import ping_stats

    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        directory = Path(out_dir)
        first = _giveaway_card(
            pings, ping_stats(list(pings)),
            ping_stats(list(prev_pings)) if prev_pings is not None else None,
            directory / f"digest_pings_{stamp}.png", period_label,
        )
        snapshots = [s for s in (market or []) if isinstance(s, dict)]
        second = _market_card(snapshots, directory / f"digest_market_{stamp}.png", period_label)
        _prune(directory)
        return [first, second]
    except Exception:  # pragma: no cover - render is best effort
        from .app_ctx import logger

        logger.exception("Digest card rendering failed; falling back to text")
        return []
