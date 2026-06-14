"""Generate the Pulse Desk Telegram bot branding set (Spectral Pulse philosophy).

One-off tool: writes 6 PNGs into assets/bot/. Requires Pillow.
Fonts are taken from the canvas-design skill font library.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot"
FONTS = Path(r"C:\Windows\Fonts")

# Thin geometric letterforms with full Cyrillic coverage.
JURA_LIGHT = str(FONTS / "segoeuil.ttf")    # Segoe UI Light
JURA_MEDIUM = str(FONTS / "seguisb.ttf")    # Segoe UI Semibold

SS = 2  # supersampling factor for crisp downscaled output


# ---------------------------------------------------------------- gradients

def diagonal_gradient(size: tuple[int, int], corners: list[tuple[int, int, int]]) -> Image.Image:
    """Smooth 4-corner gradient: tiny mesh upscaled with bicubic interpolation."""
    mesh = Image.new("RGB", (2, 2))
    mesh.putpixel((0, 0), corners[0])  # top-left
    mesh.putpixel((1, 0), corners[1])  # top-right
    mesh.putpixel((0, 1), corners[2])  # bottom-left
    mesh.putpixel((1, 1), corners[3])  # bottom-right
    return mesh.resize(size, Image.BICUBIC)


def add_glow(img: Image.Image, center: tuple[int, int], radius: int,
             color: tuple[int, int, int], alpha: int, blur: int) -> None:
    """Soft radial light source blended over the gradient sky."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = center
    d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(layer)


def add_grain(img: Image.Image, alpha: int = 10) -> None:
    noise = Image.effect_noise(img.size, 28).convert("L")
    grain = Image.merge("RGBA", (noise, noise, noise, noise.point(lambda _: alpha)))
    img.alpha_composite(grain)


# ------------------------------------------------------------------ motifs

def pulse_rings(img: Image.Image, center: tuple[int, int], radii: list[int],
                base_alpha: int = 150, width: int = 3) -> None:
    """Concentric rings fading outward — the radar afterglow."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = center
    n = len(radii)
    for i, r in enumerate(radii):
        a = int(base_alpha * (1 - i / (n + 0.5)))
        w = max(SS, width - (i * width) // (n + 1))
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, a), width=w)
    img.alpha_composite(layer)


def heartbeat(img: Image.Image, y: int, x0: int, x1: int, spike_x: int,
              amp: int, alpha: int = 230, width: int = 4) -> None:
    """Calm line, one sharp spike, back to rest. Drawn twice: glow + crisp."""
    s = amp // 4
    pts = [
        (x0, y),
        (spike_x - int(2.6 * s), y),
        (spike_x - int(1.8 * s), y - s // 2),
        (spike_x - s, y + s // 2),
        (spike_x, y - amp),
        (spike_x + s, y + int(0.55 * amp)),
        (spike_x + int(1.8 * s), y),
        (x1, y),
    ]
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=(255, 210, 160, 130), width=width * 3, joint="curve")
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6 * SS)))
    sharp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sharp).line(pts, fill=(255, 255, 255, alpha), width=width, joint="curve")
    img.alpha_composite(sharp)


def tracked_text(d: ImageDraw.ImageDraw, pos: tuple[int, int], text: str,
                 font: ImageFont.FreeTypeFont, tracking: int,
                 fill=(255, 255, 255, 255), anchor_center_x: int | None = None) -> None:
    """Letter-by-letter rendering with wide tracking (surveyor's whisper)."""
    widths = [d.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = (anchor_center_x - total / 2) if anchor_center_x is not None else pos[0]
    y = pos[1]
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def tick_marks(img: Image.Image, center: tuple[int, int], radius: int,
               count: int = 60, length: int = 10, alpha: int = 90) -> None:
    """Clinical reference markers around the outer ring."""
    d = ImageDraw.Draw(img)
    x, y = center
    for i in range(count):
        ang = 2 * math.pi * i / count
        long_tick = length * (2 if i % 5 == 0 else 1)
        x0 = x + radius * math.cos(ang)
        y0 = y + radius * math.sin(ang)
        x1 = x + (radius + long_tick) * math.cos(ang)
        y1 = y + (radius + long_tick) * math.sin(ang)
        d.line([(x0, y0), (x1, y1)], fill=(255, 255, 255, alpha), width=SS)


def finish(img: Image.Image, path: Path, final_size: tuple[int, int]) -> None:
    add_grain(img, alpha=9)
    out = img.convert("RGB").resize(final_size, Image.LANCZOS)
    out.save(path, "PNG")
    print(f"  {path.name}  {final_size[0]}x{final_size[1]}")


# ----------------------------------------------------------------- canvases

INDIGO = (24, 16, 62)
VIOLET = (94, 36, 173)
MAGENTA = (227, 49, 138)
ORANGE = (255, 138, 56)
BLUE = (38, 70, 199)
CYAN = (56, 178, 222)
GOLD = (255, 186, 64)
PINK = (255, 92, 168)


def make_avatar() -> None:
    W = H = 512 * SS
    img = diagonal_gradient((W, H), [INDIGO, VIOLET, MAGENTA, ORANGE]).convert("RGBA")
    cx, cy = W // 2, H // 2
    add_glow(img, (cx, cy), W // 5, ORANGE, 120, 60 * SS)
    add_glow(img, (cx, cy), W // 14, (255, 240, 220), 110, 26 * SS)
    pulse_rings(img, (cx, cy), [int(W * f) for f in (0.14, 0.22, 0.30, 0.38, 0.46)],
                base_alpha=185, width=4 * SS)
    tick_marks(img, (cx, cy), int(W * 0.46), count=60, length=7 * SS, alpha=80)
    heartbeat(img, cy, int(W * 0.10), int(W * 0.90), cx, int(H * 0.16), width=5 * SS)
    finish(img, OUT / "avatar.png", (512, 512))


def make_description() -> None:
    W, H = 640 * SS, 360 * SS
    img = diagonal_gradient((W, H), [INDIGO, VIOLET, MAGENTA, ORANGE]).convert("RGBA")
    cx, cy = int(W * 0.74), int(H * 0.5)
    add_glow(img, (cx, cy), H // 4, ORANGE, 140, 50 * SS)
    pulse_rings(img, (cx, cy), [int(H * f) for f in (0.16, 0.28, 0.40, 0.52, 0.64)],
                base_alpha=170, width=3 * SS)
    heartbeat(img, cy, int(W * 0.05), int(W * 0.95), cx, int(H * 0.18), width=4 * SS)
    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(JURA_LIGHT, 54 * SS)
    small = ImageFont.truetype(JURA_LIGHT, 17 * SS)
    tracked_text(d, (int(W * 0.06), int(H * 0.30)), "PULSE", title, 14 * SS)
    tracked_text(d, (int(W * 0.06), int(H * 0.46)), "DESK", title, 14 * SS)
    tracked_text(d, (int(W * 0.06), int(H * 0.66)), "СИГНАЛЫ · РОЗЫГРЫШИ · ПОБЕДЫ",
                 small, 4 * SS, fill=(255, 255, 255, 200))
    finish(img, OUT / "description.png", (640, 360))


def make_welcome() -> None:
    W, H = 1280 * SS, 640 * SS
    img = diagonal_gradient((W, H), [INDIGO, BLUE, VIOLET, MAGENTA]).convert("RGBA")
    cx, cy = int(W * 0.78), int(H * 0.42)
    add_glow(img, (cx, cy), H // 4, ORANGE, 130, 60 * SS)
    pulse_rings(img, (cx, cy), [int(H * f) for f in (0.12, 0.22, 0.32, 0.42, 0.52, 0.62)],
                base_alpha=160, width=3 * SS)
    tick_marks(img, (cx, cy), int(H * 0.62), count=72, length=8 * SS, alpha=70)
    heartbeat(img, int(H * 0.72), int(W * 0.04), int(W * 0.96), cx, int(H * 0.14), width=5 * SS)
    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(JURA_LIGHT, 110 * SS)
    small = ImageFont.truetype(JURA_LIGHT, 26 * SS)
    tiny = ImageFont.truetype(JURA_LIGHT, 19 * SS)
    tracked_text(d, (int(W * 0.05), int(H * 0.22)), "PULSE DESK", title, 22 * SS)
    tracked_text(d, (int(W * 0.05), int(H * 0.46)), "ДОБРО ПОЖАЛОВАТЬ НА ЧАСТОТУ",
                 small, 8 * SS, fill=(255, 255, 255, 215))
    tracked_text(d, (int(W * 0.05), int(H * 0.88)), "SIGNAL MONITOR · 24/7",
                 tiny, 6 * SS, fill=(255, 255, 255, 150))
    finish(img, OUT / "welcome.png", (1280, 640))


def _notify(filename: str, corners: list[tuple[int, int, int]],
            glow_color: tuple[int, int, int], label: str, sublabel: str) -> None:
    W, H = 1200 * SS, 420 * SS
    img = diagonal_gradient((W, H), corners).convert("RGBA")
    cx, cy = int(W * 0.82), int(H * 0.48)
    add_glow(img, (cx, cy), H // 4, glow_color, 150, 45 * SS)
    pulse_rings(img, (cx, cy), [int(H * f) for f in (0.16, 0.30, 0.44, 0.58, 0.72)],
                base_alpha=170, width=3 * SS)
    tick_marks(img, (cx, cy), int(H * 0.72), count=48, length=8 * SS, alpha=70)
    heartbeat(img, int(H * 0.62), int(W * 0.04), int(W * 0.96), cx, int(H * 0.17), width=4 * SS)
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(JURA_MEDIUM, 76 * SS)
    tiny = ImageFont.truetype(JURA_LIGHT, 22 * SS)
    tracked_text(d, (int(W * 0.05), int(H * 0.26)), label, big, 14 * SS)
    tracked_text(d, (int(W * 0.05), int(H * 0.50)), sublabel, tiny, 6 * SS,
                 fill=(255, 255, 255, 175))
    finish(img, OUT / filename, (1200, 420))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating Pulse Desk bot assets:")
    make_avatar()
    make_description()
    make_welcome()
    _notify("notify_mention.png", [INDIGO, BLUE, VIOLET, CYAN], CYAN,
            "УПОМИНАНИЕ", "PULSE DESK · SIGNAL // MENTION")
    _notify("notify_giveaway.png", [INDIGO, VIOLET, MAGENTA, PINK], PINK,
            "РОЗЫГРЫШ", "PULSE DESK · SIGNAL // GIVEAWAY")
    _notify("notify_win.png", [VIOLET, MAGENTA, ORANGE, GOLD], GOLD,
            "ПОБЕДА", "PULSE DESK · SIGNAL // WIN")
    print("Done.")


if __name__ == "__main__":
    main()
