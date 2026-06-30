"""Generate the Pulse Desk custom-emoji set (Aperture philosophy).

Static 100x100 .webp custom-emoji glyphs, transparent background: a Segoe
colour-emoji glyph with a small citron "lock dot" — the Aperture brand mark —
in the corner (visible in the 100px pack preview, invisible at inline size, so
the emoji stays clean in chat). Requires Pillow + C:\\Windows\\Fonts.

Each glyph is mapped to the standard emoji the bot already uses in its cards, so
non-Premium users see exactly that emoji as the fallback.

The pack itself is created manually via @Stickers (needs Telegram Premium):
  1. @Stickers → /newemojipack → choose a name
  2. upload every assets/bot/emoji/*.webp
  3. for each, send the matching emoji from EMOJI_MAP below (printed at the end)
  4. /publish → choose a short name → set BOT_CUSTOM_EMOJI_SET=<short_name>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Windows consoles default to cp1251 here; emoji/arrows in our prints need UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot" / "emoji"
FONTS = Path(r"C:\Windows\Fonts")
EMOJI = str(FONTS / "seguiemj.ttf")

SS = 4          # supersample 400 -> 100
S = 100
CITRON = (205, 255, 74)

# name -> (standard emoji, glyph to render). Name == output filename stem.
EMOJI_MAP: dict[str, tuple[str, str]] = {
    "win":       ("🏆", "\U0001F3C6"),
    "giveaway":  ("🎁", "\U0001F381"),
    "check":     ("💸", "\U0001F4B8"),
    "prize":     ("💰", "\U0001F4B0"),
    "signal":    ("📡", "\U0001F4E1"),
    "radar":     ("🛰", "\U0001F6F0"),
    "target":    ("🎯", "\U0001F3AF"),
    "bell":      ("🔔", "\U0001F514"),
    "watch":     ("👁", "\U0001F441"),
    "key":       ("🔑", "\U0001F511"),
    "online":    ("🟢", "\U0001F7E2"),
    "offline":   ("🔴", "\U0001F534"),
    "fire":      ("🔥", "\U0001F525"),
    "deadline":  ("⏰", "\U000023F0"),
    "done":      ("✅", "\U00002705"),
    "fail":      ("❌", "\U0000274C"),
    "star":      ("⭐", "\U00002B50"),
    "stats":     ("📊", "\U0001F4CA"),
    "btc":       ("🟠", "\U0001F7E0"),
    "eth":       ("🔷", "\U0001F537"),
    "ton":       ("💎", "\U0001F48E"),
    "sol":       ("🟣", "\U0001F7E3"),
    "pong":      ("🏓", "\U0001F3D3"),
    "gear":      ("⚙️", "\U00002699"),
}


def make_emoji(name: str, glyph: str) -> None:
    W = S * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(EMOJI, int(W * 0.74))
    d.text((W // 2, W // 2 - int(W * 0.03)), glyph, font=f,
           embedded_color=True, anchor="mm")
    # Aperture lock-dot brand mark, bottom-right (vanishes at inline size).
    r = int(W * 0.06)
    cx, cy = int(W * 0.84), int(W * 0.84)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CITRON + (255,))
    out = img.resize((S, S), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.webp", "WEBP", lossless=True, quality=100, method=6)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating Pulse Desk custom emoji (Aperture):")
    for name, (emoji, glyph) in EMOJI_MAP.items():
        make_emoji(name, glyph)
        print(f"  {name}.webp  →  assign emoji: {emoji}")
    print("Done. Create the pack via @Stickers and assign the emoji shown above.")


if __name__ == "__main__":
    main()
