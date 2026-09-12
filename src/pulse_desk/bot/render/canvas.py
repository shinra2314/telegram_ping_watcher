"""Рисующий слой Aperture: холст, шапка, плитки, шрифты, сохранение.

Вынесено из ``digest_cards``, где этот словарь сложился первым: дайджест был
единственным, что бот рисовал. Экраны из веба (дашборд, маркет, доски) рисуются
тем же карандашом — палитра, сетка, скобки по углам и приборные цифры должны
совпадать до пикселя, иначе «одно приложение» рассыпается на набор картинок.

Pillow импортируется **лениво, внутри функций**: без него модуль обязан
импортироваться, а вызывающий — откатиться на текстовую карточку.

Геометрия (``squarify``) чистая и покрыта тестами; рисование — нет.
Палитра и геометрия по assets/bot/DESIGN_PHILOSOPHY.md.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Optional, Sequence

Rect = tuple[float, float, float, float]  # x, y, w, h

# ---------------------------------------------------------------- geometry

def squarify(values: Sequence[float], rect: Rect) -> list[Rect]:
    """Squarified treemap: areas proportional to `values`, tiles near-square.

    `values` are taken in the order given (sort them descending for the
    classic look). Non-positive values are kept as zero-area rects so the
    caller can zip the result back onto its own list.
    """
    out: list[Rect] = [(0.0, 0.0, 0.0, 0.0)] * len(values)
    live = [(i, float(v)) for i, v in enumerate(values) if v and v > 0]
    x, y, w, h = rect
    if not live or w <= 0 or h <= 0:
        return out

    total = sum(v for _, v in live)
    scale = (w * h) / total
    queue = [(i, v * scale) for i, v in live]

    def worst(row: list[float], side: float) -> float:
        """Worst aspect ratio in `row` when laid along a strip of `side`."""
        area = sum(row)
        if area <= 0 or side <= 0:
            return math.inf
        side_sq = side * side
        area_sq = area * area
        return max(side_sq * max(row) / area_sq, area_sq / (side_sq * min(row)))

    while queue:
        side = min(w, h)
        row: list[float] = []
        row_idx: list[int] = []
        while queue:
            idx, area = queue[0]
            if row and worst(row + [area], side) > worst(row, side):
                break
            row.append(area)
            row_idx.append(idx)
            queue.pop(0)
        band = sum(row) / side if side else 0.0
        offset = 0.0
        for idx, area in zip(row_idx, row):
            length = area / band if band else 0.0
            if w >= h:
                out[idx] = (x, y + offset, band, length)
            else:
                out[idx] = (x + offset, y, length, band)
            offset += length
        if w >= h:
            x += band
            w -= band
        else:
            y += band
            h -= band
        if w <= 0 or h <= 0:
            break
    return out


# Imported lazily so a missing Pillow degrades to the text digest instead of
# breaking the import of loops.py.

SS = 2                      # supersample, downscaled on save
W, H = 1080, 1350           # final card size

GRAPHITE_TOP = (20, 21, 28)
GRAPHITE_BOT = (9, 10, 14)
GRID = (28, 31, 40)
GRID_MAJOR = (40, 44, 56)
FRAME = (58, 63, 76)
WHITE = (238, 242, 233)
DIM = (150, 158, 150)
MUTED = (104, 112, 122)

CITRON = (205, 255, 74)     # giveaways / accent
AMBER = (255, 178, 62)      # wins
CYAN = (78, 216, 255)       # mentions
UP = (138, 226, 106)        # market: day gain
DOWN = (233, 86, 86)        # market: day loss
FLAT = (128, 136, 148)

GROUP_COLOR = {"wins": AMBER, "giveaways": CITRON, "mentions": CYAN}

# Type is the instrument's own labelling: a DIN-descended condensed grotesque
# (Bahnschrift, variable — weight 300-700, width 75-100) for names and titles,
# and a true monospace for every NUMBER on the card. Digits that share a width
# are what makes a column of readouts line up like a panel instead of a
# paragraph; Segoe's proportional figures never could.
_DISPLAY = ["C:/Windows/Fonts/bahnschrift.ttf", "C:/Windows/Fonts/framd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
            "C:/Windows/Fonts/segoeuib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_MONO = ["C:/Windows/Fonts/consola.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
         "C:/Windows/Fonts/lucon.ttf"]

# kind -> (candidates, variation axes in the file's own axis order)
_FACES: dict[str, tuple[list[str], Optional[tuple[float, ...]]]] = {
    "title": (_DISPLAY, (600.0, 82.0)),    # wordmark: condensed, tracked out
    "name": (_DISPLAY, (600.0, 88.0)),     # tile names and tickers
    "num": (_MONO, None),                  # every figure on the card
    "label": (_MONO, None),                # small caps labels, unit codes
}
_font_cache: dict[tuple[str, int], Any] = {}


def font(kind: str, size: int):
    """A face from the candidate list; Pillow's bundled default as last resort."""
    from PIL import ImageFont

    key = (kind, size)
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    candidates, axes = _FACES[kind]
    font = None
    for path in candidates:
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            continue
        if axes:
            try:  # static fallback faces have no axes — keep them as they are
                font.set_variation_by_axes(list(axes))
            except Exception:
                pass
        break
    if font is None:
        font = ImageFont.load_default(size)
    _font_cache[key] = font
    return font


def px(value: float) -> int:
    return int(round(value * SS))


def blend(color: tuple[int, int, int], base: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(b + (c - b) * t)) for c, b in zip(color, base))  # type: ignore[return-value]


def canvas():
    from PIL import Image, ImageDraw, ImageFilter

    size = (W * SS, H * SS)
    base = Image.new("RGB", (1, 2))
    base.putpixel((0, 0), GRAPHITE_TOP)
    base.putpixel((0, 1), GRAPHITE_BOT)
    img = base.resize(size, Image.BICUBIC).convert("RGBA")

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    step = px(30)
    cx, cy = size[0] // 2, size[1] // 2
    for axis in (0, 1):
        limit = size[axis]
        centre = cx if axis == 0 else cy
        i = 0
        while centre + i * step <= limit + step:
            for pos in {centre + i * step, centre - i * step}:
                color = (GRID_MAJOR if i % 5 == 0 else GRID) + (255,)
                if axis == 0:
                    d.line([(pos, 0), (pos, size[1])], fill=color, width=SS)
                else:
                    d.line([(0, pos), (size[0], pos)], fill=color, width=SS)
            i += 1
    img.alpha_composite(layer)

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse([size[0] * 0.04, size[1] * 0.04,
                                  size[0] * 0.96, size[1] * 0.96], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(int(size[0] * 0.13)))
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    shade.putalpha(mask.point(lambda v: int((255 - v) * 120 / 255)))
    img.alpha_composite(shade)
    return img


def brackets(img, inset: int, arm: int) -> None:
    from PIL import ImageDraw

    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    for (x, y), (sx, sy) in (((inset, inset), (1, 1)), ((w - inset, inset), (-1, 1)),
                             ((inset, h - inset), (1, -1)), ((w - inset, h - inset), (-1, -1))):
        d.line([(x, y), (x + sx * arm, y)], fill=FRAME + (255,), width=2 * SS)
        d.line([(x, y), (x, y + sy * arm)], fill=FRAME + (255,), width=2 * SS)


def tracked(d, pos: tuple[float, float], text: str, font, tracking: float, fill) -> float:
    """Letter-by-letter with wide tracking (instrument labelling). Returns width."""
    x, y = pos
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + tracking
    return x - pos[0] - tracking


def fit(d, text: str, kind: str, max_w: float, start: int, floor: int):
    """Largest font of `kind` at which `text` fits `max_w`; None if even `floor` won't."""
    size = start
    while size >= floor:
        face = font(kind, size)
        if d.textlength(text, font=face) <= max_w:
            return face
        size = int(size * 0.88) if size > floor else floor - 1
    return None


def ellipsize(d, text: str, font, max_w: float) -> str:
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return (text + "…") if text else ""


def header(img, title: str, subtitle: str, unit: str, accent: tuple[int, int, int]) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    brackets(img, px(30), px(44))
    # A short accent rail anchors the wordmark to the left edge of the plate.
    d.rectangle([px(40), px(58), px(43), px(150)], fill=accent + (255,))
    tracked(d, (px(64), px(56)), title.upper(), font("title", px(50)), px(6.0), WHITE + (255,))
    d.text((px(64), px(128)), subtitle, font=font("label", px(19)), fill=(123, 132, 148) + (255,))
    code = font("label", px(17))
    d.text((W * SS - px(64) - d.textlength(unit, font=code), px(64)),
           unit, font=code, fill=MUTED + (255,))


def stat_row(img, cells: list[tuple[str, str, tuple[int, int, int]]], top: int) -> None:
    """Three readouts under the wordmark: mono label, mono figure, fading rule."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    cell_w = (W * SS - px(128)) / 3
    for i, (label, value, color) in enumerate(cells):
        x = px(64) + i * cell_w
        tracked(d, (x, top), label.upper(), font("label", px(15)), px(2.2), MUTED + (255,))
        face = fit(d, value, "num", cell_w - px(24), px(58), px(22)) or font("num", px(22))
        d.text((x, top + px(26)), value, font=face, fill=color + (255,))
        fade_rule(img, x, top + px(100), cell_w - px(24), color)


def fade_rule(img, x: float, y: float, width: float, color: tuple[int, int, int]) -> None:
    """A 2 px rule that fades out to the right — a level, not a border."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    steps = 48
    for i in range(steps):
        alpha = int(215 * (1 - i / steps) ** 1.6)
        x0 = x + width * i / steps
        d.rectangle([x0, y, x0 + width / steps + 1, y + 2 * SS], fill=color + (alpha,))


def scan_line(img, y: int, accent: tuple[int, int, int], node_ratio: float = 0.22) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    x0, x1 = px(64), W * SS - px(64)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    # Dashed, not solid: a measured trace across the plate rather than a border.
    dash = px(9)
    for x in range(x0, x1, dash * 2):
        d.line([(x, y), (min(x + dash, x1), y)], fill=accent + (120,), width=2 * SS)
    img.alpha_composite(layer)
    node_x = int(x0 + (x1 - x0) * node_ratio)
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    r = 5 * SS
    ImageDraw.Draw(glow).ellipse([node_x - r * 3, y - r * 3, node_x + r * 3, y + r * 3],
                                 fill=accent + (110,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(r * 2)))
    ImageDraw.Draw(img, "RGBA").ellipse([node_x - r // 2, y - r // 2, node_x + r // 2, y + r // 2],
                                        fill=(255, 255, 255, 255))


def footer(img, text: str) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    tracked(d, (px(64), H * SS - px(70)), text, font("label", px(17)), px(1.6),
             (92, 100, 110) + (255,))


def save(img, path: Path) -> str:
    from PIL import Image

    noise = Image.effect_noise(img.size, 26).convert("L")
    img.alpha_composite(Image.merge("RGBA", (noise, noise, noise, noise.point(lambda _: 7))))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").resize((W, H), Image.LANCZOS).save(path, "PNG")
    return str(path)


def empty_state(img, message: str) -> None:
    """A lone reticle where the treemap would be — nothing to lock onto."""
    from PIL import Image, ImageDraw, ImageFilter

    cx, cy = W * SS // 2, px(760)
    r = px(120)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=FRAME + (255,), width=2 * SS)
    for i in range(48):
        ang = 2 * math.pi * i / 48
        t = r * (0.12 if i % 6 == 0 else 0.06)
        d.line([(cx + (r - t) * math.cos(ang), cy + (r - t) * math.sin(ang)),
                (cx + r * math.cos(ang), cy + r * math.sin(ang))],
               fill=FRAME + (140 if i % 6 == 0 else 70,), width=SS)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(0)))
    d = ImageDraw.Draw(img, "RGBA")
    face = font("name", px(28))
    d.text((cx - d.textlength(message, font=face) / 2, cy + r + px(46)),
           message, font=face, fill=DIM + (255,))


# ------------------------------------------------------------------- cards

def tile(img, rect: Rect, color: tuple[int, int, int], strength: float,
          marker: bool = True) -> None:
    """One treemap cell: flat graphite-mixed fill, hairline keyline, edge marker.

    Flat and squared off on purpose — a rounded, heavily tinted chip reads as a
    button; the plate wants a cell on an instrument. `marker` is the short bar
    on the left edge that gives the row an origin to scan from.
    """
    from PIL import ImageDraw

    x, y, w, h = rect
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([x, y, x + w, y + h],
                fill=blend(color, GRAPHITE_BOT, 0.11 + 0.24 * strength) + (255,),
                outline=color + (int(95 + 130 * strength),), width=SS)
    if marker and h > px(40):
        d.rectangle([x, y, x + 3 * SS, y + h * 0.34], fill=color + (255,))


def sparkline(img, rect: Rect, values: Sequence[float], color: tuple[int, int, int],
              fill: bool = True) -> None:
    """Кривая по значениям внутри прямоугольника — тренд, а не точные цифры.

    Плоский ряд рисуется линией по середине: делить на нулевой размах нельзя, а
    «ничего не менялось» — тоже показание.
    """
    from PIL import Image, ImageDraw

    x, y, w, h = rect
    series = [float(v) for v in values if v is not None]
    if len(series) < 2 or w <= 0 or h <= 0:
        return
    low, high = min(series), max(series)
    span = (high - low) or 1.0
    flat = high == low
    step = w / (len(series) - 1)
    points = [
        (x + i * step, (y + h / 2) if flat else (y + h - (v - low) / span * h))
        for i, v in enumerate(series)
    ]
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if fill:
        d.polygon([(points[0][0], y + h), *points, (points[-1][0], y + h)],
                  fill=color + (38,))
    d.line(points, fill=color + (235,), width=max(SS, int(2 * SS)), joint="curve")
    # Последняя точка — то, где ряд стоит сейчас; её и подсвечиваем.
    r = 3 * SS
    d.ellipse([points[-1][0] - r, points[-1][1] - r, points[-1][0] + r, points[-1][1] + r],
              fill=color + (255,))
    img.alpha_composite(layer)


def progress(img, rect: Rect, ratio: float, color: tuple[int, int, int]) -> None:
    """Уровень: заполненная часть цветом, остаток — тёмный жёлоб."""
    from PIL import ImageDraw

    x, y, w, h = rect
    ratio = max(0.0, min(1.0, float(ratio or 0.0)))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([x, y, x + w, y + h], fill=blend(color, GRAPHITE_BOT, 0.08) + (255,))
    if ratio > 0:
        d.rectangle([x, y, x + w * ratio, y + h], fill=color + (255,))


def prune(directory: Path, pattern: str, keep_days: int) -> None:
    """Drop rendered cards older than `keep_days`.

    A member's delayed copy still reads the file hours later, so pruning is by
    age rather than on send.
    """
    cutoff = time.time() - keep_days * 86400
    for old in Path(directory).glob(pattern):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
