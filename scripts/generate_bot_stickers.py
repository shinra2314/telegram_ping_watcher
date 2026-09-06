"""Generate the Pulse Desk bot sticker set (Aperture philosophy).

Static 512x512 .webp stickers, transparent background: an Aperture reticle
ring in the event's signal colour wrapped around one of the brand glyphs from
``generate_bot_emoji`` — the same silhouettes the custom-emoji pack uses, so a
sticker and its inline emoji are the same drawing at two scales. Plus a
monospace unit code under the reticle.

Only the unit code needs a font (Consolas); the glyphs are pure geometry.

See assets/bot/DESIGN_PHILOSOPHY.md for the brand rationale.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_bot_emoji import (  # noqa: E402
    AMBER, CITRON, CITRON_DEEP, DIM, RED, Painter,
    g_ban, g_bell, g_check, g_cross, g_dish, g_gift, g_key, g_lock_closed,
    g_mailbox_empty, g_pingpong, g_satellite, g_trophy, g_warn,
)

OUT = BASE / "assets" / "bot" / "stickers"
FONTS = Path(r"C:\Windows\Fonts")
MONO = str(FONTS / "consola.ttf")     # Consolas — instrument readout

SS = 2          # supersample then downscale for crisp edges
S = 512         # Telegram static-sticker side


def _ring(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int,
          color: tuple[int, int, int], w: int) -> None:
    """Reticle ring: corner brackets, ring, measurement ticks, crosshair.

    No centre lock-dot — the brand glyph sits at the focal point instead.
    """
    be, bl = int(r * 1.7), int(r * 0.45)
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x, y = cx + sx * be, cy + sy * be
        d.line([(x, y), (x - sx * bl, y)], fill=color + (255,), width=w)
        d.line([(x, y), (x, y - sy * bl)], fill=color + (255,), width=w)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color + (255,), width=w)
    for i in range(72):
        ang = 2 * math.pi * i / 72
        t = r * 0.12 if i % 6 == 0 else r * 0.06
        a = 150 if i % 6 == 0 else 70
        d.line([(cx + (r - t) * math.cos(ang), cy + (r - t) * math.sin(ang)),
                (cx + r * math.cos(ang), cy + r * math.sin(ang))],
               fill=color + (a,), width=SS)
    gap, ext = int(r * 0.18), int(r * 0.4)
    # No downward tick — the unit code sits there.
    for dx, dy in ((1, 0), (-1, 0), (0, -1)):
        d.line([(cx + dx * (r + gap), cy + dy * (r + gap)),
                (cx + dx * (r + gap + ext), cy + dy * (r + gap + ext))],
               fill=color + (210,), width=w)


def make_sticker(name: str, color: tuple[int, int, int], paint: Painter,
                 code: str) -> None:
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    _ring(d, cx, cy, int(W * 0.30), color, 3 * SS)

    inner = int(W * 0.38)
    m = Image.new("L", (inner, inner), 0)
    paint(m)
    mask = Image.new("L", (W, W), 0)
    mask.paste(m, (cx - inner // 2, cy - inner // 2))
    img.paste(color + (255,), (0, 0), mask)

    try:
        mono = ImageFont.truetype(MONO, int(W * 0.045))
    except Exception:
        mono = ImageFont.load_default()
    d.text((cx, int(W * 0.88)), code, font=mono, fill=color + (230,), anchor="mm")

    out = img.resize((S, S), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.webp", "WEBP", lossless=True, quality=100, method=6)
    print(f"  {name}.webp 512x512")


def main() -> None:
    print("Generating Pulse Desk bot stickers (Aperture):")
    make_sticker("win",        AMBER,       g_trophy,        "PD·WIN")
    make_sticker("giveaway",   CITRON,      g_gift,          "PD·GIFT")
    make_sticker("scan",       CITRON,      g_satellite,     "PD·SCAN")
    make_sticker("scan_done",  CITRON_DEEP, g_check,         "PD·DONE")
    make_sticker("access_on",  CITRON,      g_key,           "PD·OPEN")
    make_sticker("access_off", RED,         g_lock_closed,   "PD·LOCK")
    make_sticker("welcome",    CITRON,      g_dish,          "PD·01")
    make_sticker("empty",      DIM,         g_mailbox_empty, "PD·NIL")
    make_sticker("error",      RED,         g_warn,          "PD·ERR")
    make_sticker("pong",       CITRON,      g_pingpong,      "PD·PONG")
    make_sticker("alert",      RED,         g_ban,           "PD·STOP")
    make_sticker("bell",       CITRON,      g_bell,          "PD·PING")
    make_sticker("fail",       RED,         g_cross,         "PD·FAIL")
    print("Done.")


if __name__ == "__main__":
    main()
