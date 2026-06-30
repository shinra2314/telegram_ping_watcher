"""Generate the Pulse Desk bot sticker set (Aperture philosophy).

Static 512x512 .webp stickers, transparent background: an Aperture reticle
ring in the event's signal colour wrapped around a Segoe color-emoji glyph,
plus a monospace unit code. Requires Pillow + C:\\Windows\\Fonts. Manual tool
(needs the Windows colour-emoji + mono fonts); CI only imports it.

See assets/bot/DESIGN_PHILOSOPHY.md for the brand rationale.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot" / "stickers"
FONTS = Path(r"C:\Windows\Fonts")
MONO = str(FONTS / "consola.ttf")     # Consolas — instrument readout
EMOJI = str(FONTS / "seguiemj.ttf")   # Segoe UI Emoji — colour glyph

SS = 2          # supersample then downscale for crisp edges
S = 512         # Telegram static-sticker side

# ----------------------------------------------------------------- palette
CITRON = (205, 255, 74)
CITRON_DEEP = (168, 216, 31)
CYAN = (78, 216, 255)
AMBER = (255, 178, 62)
RED = (255, 92, 92)
DIM = (150, 158, 150)


def _ring(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int,
          color: tuple[int, int, int], w: int) -> None:
    """Reticle ring: corner brackets, ring, measurement ticks, crosshair.

    No centre lock-dot — the emoji glyph sits at the focal point instead.
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
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        d.line([(cx + dx * (r + gap), cy + dy * (r + gap)),
                (cx + dx * (r + gap + ext), cy + dy * (r + gap + ext))],
               fill=color + (210,), width=w)


def make_sticker(name: str, color: tuple[int, int, int], glyph: str, code: str) -> None:
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = W // 2
    _ring(d, cx, cy, int(W * 0.30), color, 3 * SS)
    emoji = ImageFont.truetype(EMOJI, int(W * 0.30))
    d.text((cx, cy - int(W * 0.01)), glyph, font=emoji, embedded_color=True, anchor="mm")
    mono = ImageFont.truetype(MONO, int(W * 0.045))
    d.text((cx, int(W * 0.88)), code, font=mono, fill=color + (230,), anchor="mm")
    out = img.resize((S, S), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.webp", "WEBP", lossless=True, quality=100, method=6)
    print(f"  {name}.webp 512x512")


def main() -> None:
    print("Generating Pulse Desk bot stickers (Aperture):")
    make_sticker("win",        AMBER,       "\U0001F3C6", "PD·WIN")    # 🏆
    make_sticker("giveaway",   CITRON,      "\U0001F381", "PD·GIFT")   # 🎁
    make_sticker("check",      CITRON,      "\U0001F4B8", "PD·CHECK")  # 💸
    make_sticker("scan",       CITRON,      "\U0001F6F0", "PD·SCAN")   # 🛰
    make_sticker("scan_done",  CITRON_DEEP, "✅",     "PD·DONE")   # ✅
    make_sticker("access_on",  CITRON,      "\U0001F513", "PD·OPEN")   # 🔓
    make_sticker("access_off", RED,         "\U0001F512", "PD·LOCK")   # 🔒
    make_sticker("welcome",    CITRON,      "\U0001F4E1", "PD·01")     # 📡
    make_sticker("empty",      DIM,         "\U0001F4ED", "PD·NIL")    # 📭
    make_sticker("error",      RED,         "⚠",     "PD·ERR")    # ⚠
    make_sticker("pong",       CITRON,      "\U0001F3D3", "PD·PONG")   # 🏓
    print("Done.")


if __name__ == "__main__":
    main()
