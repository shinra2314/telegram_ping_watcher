"""Generate the Pulse Desk Telegram bot branding set (Aperture philosophy).

One-off tool: writes 6 PNGs into assets/bot/. Requires Pillow.
Fonts are taken from C:\\Windows\\Fonts (full Cyrillic coverage).

Aperture is the optics of the machine that catches the signal: a graphite
instrument body, an oscilloscope grid, a single electric-citron focus reticle
locking onto one point, and one quiet scan line. Flat and exact — no skies,
no glow except the lock. See assets/bot/DESIGN_PHILOSOPHY.md.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot"
FONTS = Path(r"C:\Windows\Fonts")

LIGHT = str(FONTS / "segoeuil.ttf")   # Segoe UI Light  — thin geometric display
SEMI = str(FONTS / "seguisb.ttf")     # Segoe UI Semibold — alert headlines
MONO = str(FONTS / "consola.ttf")     # Consolas — instrument readouts

SS = 2  # supersampling factor for crisp downscaled output

# ------------------------------------------------------------------- palette
GRAPHITE_TOP = (20, 21, 28)
GRAPHITE_BOT = (9, 10, 14)
GRID = (30, 33, 42)        # oscilloscope minor lines
GRID_MAJOR = (42, 46, 58)  # oscilloscope major lines
FRAME = (58, 63, 76)       # structural viewfinder brackets (dim, never citron)
WHITE = (238, 242, 233)    # sparse instrument type
DIM = (150, 158, 150)

CITRON = (205, 255, 74)    # the one signal colour — lock, scan node, accent
CITRON_DEEP = (168, 216, 31)
CYAN = (78, 216, 255)      # status: mention
AMBER = (255, 178, 62)     # status: win


# ----------------------------------------------------------------- canvas ops

def graphite_bg(size: tuple[int, int]) -> Image.Image:
    """Near-flat graphite body with a faint top-to-bottom fall-off."""
    base = Image.new("RGB", (1, 2))
    base.putpixel((0, 0), GRAPHITE_TOP)
    base.putpixel((0, 1), GRAPHITE_BOT)
    return base.resize(size, Image.BICUBIC).convert("RGBA")


def add_vignette(img: Image.Image, strength: int = 130) -> None:
    """Darken the corners so the centre reads as the focal plane."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse([w * 0.04, h * 0.04, w * 0.96, h * 0.96], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(int(w * 0.13)))
    inv = mask.point(lambda v: int((255 - v) * strength / 255))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    black.putalpha(inv)
    img.alpha_composite(black)


def add_grid(img: Image.Image, step: int, alpha: int = 255,
             major_every: int = 5) -> None:
    """Oscilloscope reference grid: minor lines with brighter majors."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy = w // 2, h // 2
    # vertical lines, indexed from centre so majors stay symmetric
    i = 0
    x = cx
    while x <= w + step:
        for xx in {cx + i * step, cx - i * step}:
            c = GRID_MAJOR if i % major_every == 0 else GRID
            d.line([(xx, 0), (xx, h)], fill=c + (alpha,), width=SS)
        x += step
        i += 1
    i = 0
    y = cy
    while y <= h + step:
        for yy in {cy + i * step, cy - i * step}:
            c = GRID_MAJOR if i % major_every == 0 else GRID
            d.line([(0, yy), (w, yy)], fill=c + (alpha,), width=SS)
        y += step
        i += 1
    img.alpha_composite(layer)


def frame_brackets(img: Image.Image, inset: int, arm: int,
                   color: tuple[int, int, int] = FRAME, width: int | None = None) -> None:
    """Dim viewfinder corner brackets framing the whole canvas."""
    w, h = img.size
    width = width or 2 * SS
    d = ImageDraw.Draw(img, "RGBA")
    pts = [
        ((inset, inset), (1, 1)),
        ((w - inset, inset), (-1, 1)),
        ((inset, h - inset), (1, -1)),
        ((w - inset, h - inset), (-1, -1)),
    ]
    for (x, y), (sx, sy) in pts:
        d.line([(x, y), (x + sx * arm, y)], fill=color + (255,), width=width)
        d.line([(x, y), (x, y + sy * arm)], fill=color + (255,), width=width)


# ----------------------------------------------------------------- the mark

def reticle(img: Image.Image, center: tuple[int, int], r: int,
            color: tuple[int, int, int], ring_w: int, dot_r: int,
            bracket_extent: float = 1.9, bracket_arm: float = 0.55,
            ticks: bool = True) -> None:
    """The focus reticle: corner brackets, ring, crosshair, locked dot.

    A faint inner ring and ring tick-marks add instrument detail. The lock
    dot carries the only bloom in the whole identity — the instant of contact.
    """
    cx, cy = center
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # focus corner brackets around the ring
    be = int(r * bracket_extent)
    bl = int(r * bracket_arm)
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x = cx + sx * be
        y = cy + sy * be
        d.line([(x, y), (x - sx * bl, y)], fill=color + (255,), width=ring_w)
        d.line([(x, y), (x, y - sy * bl)], fill=color + (255,), width=ring_w)

    # faint outer halo ring + main ring
    d.ellipse([cx - int(r * 1.34), cy - int(r * 1.34),
               cx + int(r * 1.34), cy + int(r * 1.34)],
              outline=color + (55,), width=SS)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (255,), width=ring_w)

    # measurement ticks on the ring
    if ticks:
        for i in range(72):
            ang = 2 * math.pi * i / 72
            long_t = (r * 0.14) if i % 6 == 0 else (r * 0.07)
            x0 = cx + (r - long_t) * math.cos(ang)
            y0 = cy + (r - long_t) * math.sin(ang)
            x1 = cx + r * math.cos(ang)
            y1 = cy + r * math.sin(ang)
            a = 150 if i % 6 == 0 else 70
            d.line([(x0, y0), (x1, y1)], fill=color + (a,), width=SS)

    # crosshair ticks just outside the ring on the four axes
    gap = int(r * 0.20)
    ext = int(r * 0.46)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d.line([(cx + dx * (r + gap), cy + dy * (r + gap)),
                (cx + dx * (r + gap + ext), cy + dy * (r + gap + ext))],
               fill=color + (210,), width=ring_w)

    img.alpha_composite(layer)

    # lock dot bloom — the only soft light in the system
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [cx - dot_r * 4, cy - dot_r * 4, cx + dot_r * 4, cy + dot_r * 4],
        fill=color + (120,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(dot_r * 3)))
    sharp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sharp).ellipse(
        [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=(255, 255, 255, 255))
    ImageDraw.Draw(sharp).ellipse(
        [cx - int(dot_r * 1.9), cy - int(dot_r * 1.9),
         cx + int(dot_r * 1.9), cy + int(dot_r * 1.9)],
        outline=color + (255,), width=SS)
    img.alpha_composite(sharp)


def scan_line(img: Image.Image, y: int, x0: int, x1: int, node_x: int,
              color: tuple[int, int, int], width: int = 3) -> None:
    """One horizontal sweep with measurement ticks and a bright lock node."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.line([(x0, y), (x1, y)], fill=color + (120,), width=width)
    step = max(1, (x1 - x0) // 36)
    for i, x in enumerate(range(x0, x1, step)):
        t = 8 * SS if i % 5 == 0 else 4 * SS
        d.line([(x, y - t), (x, y + t)], fill=color + (60,), width=SS)
    img.alpha_composite(layer)
    node = Image.new("RGBA", img.size, (0, 0, 0, 0))
    nr = width * 3
    ImageDraw.Draw(node).ellipse([node_x - nr, y - nr, node_x + nr, y + nr],
                                 fill=color + (255,))
    img.alpha_composite(node.filter(ImageFilter.GaussianBlur(width)))
    ImageDraw.Draw(img, "RGBA").ellipse(
        [node_x - width, y - width, node_x + width, y + width],
        fill=(255, 255, 255, 255))


# -------------------------------------------------------------------- type

def tracked_text(d: ImageDraw.ImageDraw, pos: tuple[int, int], text: str,
                 font: ImageFont.FreeTypeFont, tracking: int,
                 fill=(255, 255, 255, 255), anchor_center_x: int | None = None) -> None:
    """Letter-by-letter rendering with wide tracking (instrument labelling)."""
    widths = [d.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = (anchor_center_x - total / 2) if anchor_center_x is not None else pos[0]
    y = pos[1]
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def add_grain(img: Image.Image, alpha: int = 7) -> None:
    noise = Image.effect_noise(img.size, 26).convert("L")
    grain = Image.merge("RGBA", (noise, noise, noise, noise.point(lambda _: alpha)))
    img.alpha_composite(grain)


def finish(img: Image.Image, path: Path, final_size: tuple[int, int]) -> None:
    add_grain(img)
    out = img.convert("RGB").resize(final_size, Image.LANCZOS)
    out.save(path, "PNG")
    print(f"  {path.name}  {final_size[0]}x{final_size[1]}")


def base_canvas(size: tuple[int, int], grid_step: int) -> Image.Image:
    img = graphite_bg(size)
    add_grid(img, grid_step, alpha=255)
    add_vignette(img)
    return img


# ----------------------------------------------------------------- canvases

def make_avatar() -> None:
    W = H = 512 * SS
    img = base_canvas((W, H), grid_step=W // 16)
    frame_brackets(img, inset=int(W * 0.09), arm=int(W * 0.07))
    reticle(img, (W // 2, H // 2), r=int(W * 0.21), color=CITRON,
            ring_w=4 * SS, dot_r=int(W * 0.018),
            bracket_extent=1.95, bracket_arm=0.5)
    finish(img, OUT / "avatar.png", (512, 512))


def make_description() -> None:
    W, H = 640 * SS, 360 * SS
    img = base_canvas((W, H), grid_step=W // 20)
    frame_brackets(img, inset=int(W * 0.04), arm=int(H * 0.10))
    cx, cy = int(W * 0.77), int(H * 0.5)
    scan_line(img, cy, int(W * 0.06), int(W * 0.94), cx, CITRON, width=3 * SS)
    reticle(img, (cx, cy), r=int(H * 0.24), color=CITRON,
            ring_w=3 * SS, dot_r=int(H * 0.016))
    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(LIGHT, 56 * SS)
    sub = ImageFont.truetype(LIGHT, 17 * SS)
    tiny = ImageFont.truetype(MONO, 13 * SS)
    tracked_text(d, (int(W * 0.07), int(H * 0.30)), "PULSE", title, 16 * SS, fill=WHITE + (255,))
    tracked_text(d, (int(W * 0.07), int(H * 0.46)), "DESK", title, 16 * SS, fill=WHITE + (255,))
    tracked_text(d, (int(W * 0.075), int(H * 0.65)), "СИГНАЛЫ · РОЗЫГРЫШИ · ПОБЕДЫ",
                 sub, 4 * SS, fill=DIM + (255,))
    tracked_text(d, (int(W * 0.075), int(H * 0.13)), "PD·01  OPTICAL SIGNAL DESK",
                 tiny, 4 * SS, fill=CITRON_DEEP + (220,))
    finish(img, OUT / "description.png", (640, 360))


def make_welcome() -> None:
    W, H = 1280 * SS, 640 * SS
    img = base_canvas((W, H), grid_step=W // 32)
    frame_brackets(img, inset=int(W * 0.03), arm=int(H * 0.09))
    cx, cy = int(W * 0.79), int(H * 0.46)
    scan_line(img, cy, int(W * 0.04), int(W * 0.96), cx, CITRON, width=4 * SS)
    reticle(img, (cx, cy), r=int(H * 0.26), color=CITRON,
            ring_w=4 * SS, dot_r=int(H * 0.014))
    d = ImageDraw.Draw(img)
    title = ImageFont.truetype(LIGHT, 116 * SS)
    sub = ImageFont.truetype(LIGHT, 26 * SS)
    tiny = ImageFont.truetype(MONO, 19 * SS)
    tracked_text(d, (int(W * 0.05), int(H * 0.24)), "PULSE DESK", title, 24 * SS, fill=WHITE + (255,))
    tracked_text(d, (int(W * 0.055), int(H * 0.48)), "ДОБРО ПОЖАЛОВАТЬ НА ЧАСТОТУ",
                 sub, 8 * SS, fill=DIM + (255,))
    tracked_text(d, (int(W * 0.055), int(H * 0.12)), "PD·01  OPTICAL SIGNAL MONITOR",
                 tiny, 5 * SS, fill=CITRON_DEEP + (220,))
    tracked_text(d, (int(W * 0.055), int(H * 0.86)), "TRACKING · 24/7",
                 tiny, 6 * SS, fill=DIM + (200,))
    finish(img, OUT / "welcome.png", (1280, 640))


def _notify(filename: str, status: tuple[int, int, int], label: str, sublabel: str) -> None:
    W, H = 1200 * SS, 420 * SS
    img = base_canvas((W, H), grid_step=W // 30)
    frame_brackets(img, inset=int(W * 0.03), arm=int(H * 0.12))
    # status spine on the left edge
    ImageDraw.Draw(img, "RGBA").rectangle(
        [int(W * 0.045), int(H * 0.22), int(W * 0.045) + 5 * SS, int(H * 0.78)],
        fill=status + (255,))
    cx, cy = int(W * 0.83), int(H * 0.5)
    scan_line(img, cy, int(W * 0.05), int(W * 0.95), cx, status, width=3 * SS)
    reticle(img, (cx, cy), r=int(H * 0.26), color=status,
            ring_w=3 * SS, dot_r=int(H * 0.018))
    d = ImageDraw.Draw(img)
    big = ImageFont.truetype(SEMI, 78 * SS)
    tiny = ImageFont.truetype(MONO, 20 * SS)
    tracked_text(d, (int(W * 0.075), int(H * 0.30)), label, big, 12 * SS, fill=WHITE + (255,))
    tracked_text(d, (int(W * 0.077), int(H * 0.58)), sublabel, tiny, 4 * SS, fill=status + (220,))
    finish(img, OUT / filename, (1200, 420))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating Pulse Desk bot assets (Aperture):")
    make_avatar()
    make_description()
    make_welcome()
    _notify("notify_mention.png", CYAN, "УПОМИНАНИЕ", "PD·01 // SIGNAL LOCK · MENTION")
    _notify("notify_giveaway.png", CITRON, "РОЗЫГРЫШ", "PD·01 // SIGNAL LOCK · GIVEAWAY")
    _notify("notify_win.png", AMBER, "ПОБЕДА", "PD·01 // SIGNAL LOCK · WIN")
    _notify("notify_check.png", CITRON, "ЧЕК", "PD·01 // SIGNAL LOCK · CHECK")
    _notify("notify_deadline.png", AMBER, "ДЕДЛАЙН", "PD·01 // SIGNAL LOCK · DEADLINE")
    print("Done.")


if __name__ == "__main__":
    main()
