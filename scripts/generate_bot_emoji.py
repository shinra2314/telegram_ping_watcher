"""Generate the Pulse Desk custom-emoji set (Aperture philosophy).

Static 100x100 .webp custom-emoji glyphs, drawn from scratch with Pillow
primitives — no OS emoji font is involved, so the set is one coherent icon
system instead of a Windows-flavoured copy of the stock emoji.

Every glyph is the same construction: a graphite tile with a citron keyline and
a flat silhouette inside. The tile is what makes the set readable — a bare
citron glyph washes out on a light chat theme, while the tile carries its own
contrast on light *and* dark. Silhouettes are drawn in a five-token semantic
palette (citron=action, amber=value, red=danger, cyan=info, dim=neutral); the
status dots (🟢🔴🟡🟠🟣🔷⚪️) keep their literal hue because the colour *is*
the meaning.

Each glyph is mapped to the standard emoji the bot already uses in its message
text, so non-Premium users see exactly that emoji as the fallback. The map
covers every emoji the bot prints outside inline keyboards (button labels
cannot carry entities, so they always render stock).

Requires Pillow. Rendering is pure geometry — no font dependency at all.

  python scripts/generate_bot_emoji.py            # write assets/bot/emoji/*.webp
  python scripts/generate_bot_emoji.py --sheet    # + a contact sheet to review

The pack itself is created via @Stickers from a Premium session:
  python scripts/upload_emoji_pack.py --short pulsedesk_aperture_v2 --set-env
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFont

# Windows consoles default to cp1251 here; emoji/arrows in our prints need UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "bot" / "emoji"

S = 100         # Telegram custom-emoji side
SS = 6          # supersample 600 -> 100

# ----------------------------------------------------------------- palette
SLATE = (30, 34, 38)            # tile body
CITRON_DEEP = (168, 216, 31)
KEYLINE = CITRON_DEEP           # tile edge
CITRON = (205, 255, 74)         # action / default
AMBER = (255, 190, 70)          # value, reward, warning
RED = (255, 96, 96)             # danger, negative
CYAN = (110, 205, 255)          # info, time, transport
DIM = (206, 216, 214)           # neutral / structural
GREEN = (74, 222, 128)          # ok / money
ORANGE = (255, 150, 60)
VIOLET = (185, 150, 255)

W_S = 0.105                     # standard stroke weight (unit box)
W_H = 0.135                     # heavy stroke

Painter = Callable[[Image.Image], None]


# ------------------------------------------------------- drawing primitives
# Painters write a silhouette into an L-mask using 0..1 unit coordinates.
# `v=0` erases, which is how interior detail (ribbon gaps, keyholes, dial
# hands) stays legible: negative space survives downscaling better than a
# second colour would.
def _dr(m: Image.Image) -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(m)


def _poly(m, pts, v=255):
    W = m.size[0]
    _dr(m).polygon([(x * W, y * W) for x, y in pts], fill=v)


def _rect(m, x0, y0, x1, y1, v=255, r=0.0):
    W = m.size[0]
    box = [x0 * W, y0 * W, x1 * W, y1 * W]
    if r:
        _dr(m).rounded_rectangle(box, radius=r * W, fill=v)
    else:
        _dr(m).rectangle(box, fill=v)


def _ell(m, cx, cy, rx, ry=None, v=255, w=0.0):
    W = m.size[0]
    ry = rx if ry is None else ry
    box = [(cx - rx) * W, (cy - ry) * W, (cx + rx) * W, (cy + ry) * W]
    if w:
        _dr(m).ellipse(box, outline=v, width=max(1, int(w * W)))
    else:
        _dr(m).ellipse(box, fill=v)


def _line(m, pts, w=W_S, v=255):
    W = m.size[0]
    _dr(m).line([(x * W, y * W) for x, y in pts], fill=v,
                width=max(1, int(w * W)), joint="curve")
    r = w * W / 2
    for x, y in pts:                       # round the joints and the caps
        _dr(m).ellipse([x * W - r, y * W - r, x * W + r, y * W + r], fill=v)


def _arc(m, cx, cy, rx, a0, a1, w=W_S, ry=None, v=255):
    W = m.size[0]
    ry = rx if ry is None else ry
    _dr(m).arc([(cx - rx) * W, (cy - ry) * W, (cx + rx) * W, (cy + ry) * W],
               a0, a1, fill=v, width=max(1, int(w * W)))


def _arrow(m, x0, y0, x1, y1, head=0.20, w=W_H):
    """Bold arrow from (x0,y0) to (x1,y1) — shaft plus a solid triangle head."""
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    bx, by = x1 - head * math.cos(ang), y1 - head * math.sin(ang)
    _line(m, [(x0, y0), (bx, by)], w)
    p = ang + math.pi / 2
    hw = head * 0.62
    _poly(m, [(x1, y1), (bx + hw * math.cos(p), by + hw * math.sin(p)),
              (bx - hw * math.cos(p), by - hw * math.sin(p))])


def _slash(m, cx=0.5, cy=0.5, r=0.34, w=W_H, gap=True):
    """Cancellation stroke. Cuts a wider gap first so it reads over a fill."""
    d = r * 0.72
    pts = [(cx - d, cy + d), (cx + d, cy - d)]
    if gap:
        _line(m, pts, w * 1.9, v=0)
    _line(m, pts, w)


def _rotated(m, paint, angle: float):
    """Composite `paint` rotated by `angle` degrees about the centre.

    Rotation happens on a temp mask so radial glyphs (♻️, chain links) are one
    definition instead of three hand-placed copies.
    """
    tmp = Image.new("L", m.size, 0)
    paint(tmp)
    m.paste(ImageChops.lighter(m, tmp.rotate(angle, resample=Image.BICUBIC)), (0, 0))


def _clock(m, hour: int):
    """Dial with hands at `hour` o'clock — one painter for 🕐 🕓 🕘."""
    import math
    _ell(m, .5, .5, .40, w=W_S)
    _ell(m, .5, .5, .05)
    ang = math.radians(hour * 30 - 90)
    _line(m, [(.5, .5), (.5 + .26 * math.cos(ang), .5 + .26 * math.sin(ang))], .085)
    _line(m, [(.5, .5), (.5, .19)], .075)


def _sheetp(m, x0=.20, y0=.14, x1=.80, y1=.86, fold=.18):
    """Document silhouette with a folded top-right corner."""
    _poly(m, [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)])
    _poly(m, [(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)], v=0)


# --------------------------------------------------------------- glyph set
def g_check(m):
    _line(m, [(.20, .53), (.42, .74), (.80, .27)], W_H)


def g_cross(m):
    _line(m, [(.25, .25), (.75, .75)], W_H)
    _line(m, [(.75, .25), (.25, .75)], W_H)


def g_dot(m):
    _ell(m, .5, .5, .33)


def g_trophy(m):
    _poly(m, [(.28, .16), (.72, .16), (.67, .47), (.60, .56), (.40, .56), (.33, .47)])
    _arc(m, .23, .29, .12, 80, 300, .05, ry=.11)
    _arc(m, .77, .29, .12, 240, 100, .05, ry=.11)
    _rect(m, .45, .54, .55, .70)
    _rect(m, .30, .70, .70, .83, r=.03)


def g_ban(m):
    _ell(m, .5, .5, .38, w=W_H)
    _slash(m)


def g_noentry(m):
    _ell(m, .5, .5, .38)
    _rect(m, .22, .43, .78, .57, v=0)


def g_stop(m):
    import math
    pts = [(.5 + .46 * math.cos(math.radians(a)), .5 + .46 * math.sin(math.radians(a)))
           for a in range(22, 382, 45)]
    _poly(m, pts)


def g_gift(m):
    _rect(m, .18, .40, .82, .85, r=.05)
    _rect(m, .13, .27, .87, .43, r=.04)
    _rect(m, .445, .25, .555, .87, v=0)
    _arc(m, .36, .18, .13, 15, 205, .055, ry=.11)
    _arc(m, .64, .18, .13, 335, 165, .055, ry=.11)


def g_satellite(m):
    _rect(m, .38, .34, .62, .70, r=.05)                    # bus
    _rect(m, .02, .40, .36, .64, r=.02)                    # solar wings
    _rect(m, .64, .40, .98, .64, r=.02)
    for x in (.13, .24, .76, .87):                         # cell seams
        _rect(m, x - .012, .40, x + .012, .64, v=0)
    _line(m, [(.50, .34), (.50, .14)], .06)                # mast + dish
    _arc(m, .50, .12, .17, 180, 360, .075, ry=.13)
    _line(m, [(.50, .70), (.50, .86)], .05)


def g_dish(m):
    """Crescent bowl, not a solid dome — a filled dome reads as a parasol."""
    _ell(m, .50, .40, .44)
    _ell(m, .50, .25, .44, v=0)                            # carve the bowl
    _line(m, [(.50, .62), (.50, .28)], .05)                # feed horn
    _ell(m, .50, .24, .07)
    _line(m, [(.50, .74), (.50, .90)], .085)               # mast
    _line(m, [(.30, .94), (.70, .94)], .085)               # base


def g_bell(m):
    _poly(m, [(.50, .13), (.68, .25), (.72, .58), (.81, .70), (.19, .70), (.28, .58),
              (.32, .25)])
    _rect(m, .16, .67, .84, .76, r=.04)
    _ell(m, .50, .855, .085)


def g_bell_off(m):
    g_bell(m)
    _slash(m, r=.44, w=.10)


def g_mute(m):
    _poly(m, [(.10, .38), (.26, .38), (.44, .20), (.44, .80), (.26, .62), (.10, .62)])
    _line(m, [(.58, .36), (.86, .64)], .09)
    _line(m, [(.86, .36), (.58, .64)], .09)


def g_bolt(m):
    _poly(m, [(.58, .08), (.26, .53), (.46, .53), (.38, .92), (.74, .44), (.52, .44)])


def g_fire(m):
    _poly(m, [(.50, .08), (.74, .34), (.80, .58), (.72, .80), (.50, .92), (.28, .80),
              (.20, .58), (.30, .32), (.40, .46), (.42, .26)])
    _poly(m, [(.50, .48), (.62, .64), (.60, .78), (.50, .84), (.40, .78), (.38, .64)], v=0)


def g_clock1(m):
    _clock(m, 1)


def g_clock4(m):
    _clock(m, 4)


def g_clock9(m):
    _clock(m, 9)


def g_alarm(m):
    _ell(m, .5, .54, .36, w=W_S)
    _ell(m, .5, .54, .05)
    _line(m, [(.5, .54), (.5, .30)], .075)
    _line(m, [(.5, .54), (.68, .62)], .075)
    _arc(m, .21, .18, .13, 130, 300, .075, ry=.12)     # bells
    _arc(m, .79, .18, .13, 240, 50, .075, ry=.12)
    _line(m, [(.26, .84), (.18, .94)], .07)
    _line(m, [(.74, .84), (.82, .94)], .07)


def _hourglass(m, top_sand: bool):
    """Outlined bowtie frame + a solid sand mass, top (running) or bottom (done)."""
    _rect(m, .20, .07, .80, .17, r=.03)
    _rect(m, .20, .83, .80, .93, r=.03)
    _poly(m, [(.26, .17), (.74, .17), (.54, .50), (.74, .83), (.26, .83), (.46, .50)])
    _poly(m, [(.35, .25), (.65, .25), (.515, .50), (.65, .75), (.35, .75), (.485, .50)],
          v=0)
    if top_sand:
        _poly(m, [(.365, .29), (.635, .29), (.512, .51), (.488, .51)])
        _ell(m, .50, .68, .035)
    else:
        _poly(m, [(.355, .74), (.645, .74), (.55, .58), (.45, .58)])


def g_hourglass_flow(m):
    _hourglass(m, top_sand=True)


def g_hourglass_done(m):
    _hourglass(m, top_sand=False)


def g_eye(m):
    _poly(m, [(.04, .50), (.28, .26), (.72, .26), (.96, .50), (.72, .74), (.28, .74)])
    _ell(m, .50, .50, .20, v=0)
    _ell(m, .50, .50, .11)


def g_eye_off(m):
    g_eye(m)
    _slash(m, r=.46, w=.10)


def g_user(m):
    _ell(m, .50, .30, .19)
    _poly(m, [(.14, .90), (.20, .66), (.34, .56), (.66, .56), (.80, .66), (.86, .90)])


def g_users(m):
    _ell(m, .33, .32, .16)
    _poly(m, [(.03, .88), (.09, .66), (.20, .58), (.46, .58), (.57, .66), (.63, .88)])
    _ell(m, .72, .34, .14)
    _poly(m, [(.50, .88), (.55, .70), (.64, .62), (.80, .62), (.89, .70), (.94, .88)])


def g_crown(m):
    _poly(m, [(.08, .30), (.28, .56), (.50, .20), (.72, .56), (.92, .30), (.86, .76),
              (.14, .76)])
    _rect(m, .14, .79, .86, .90, r=.03)


def g_trash(m):
    _rect(m, .12, .22, .88, .33, r=.04)
    _rect(m, .38, .10, .62, .22, r=.03)
    _poly(m, [(.20, .36), (.80, .36), (.73, .92), (.27, .92)])
    _rect(m, .38, .48, .45, .82, v=0)
    _rect(m, .55, .48, .62, .82, v=0)


def g_key(m):
    _ell(m, .31, .50, .22)
    _ell(m, .31, .50, .09, v=0)
    _rect(m, .46, .44, .90, .56, r=.03)
    _rect(m, .66, .56, .74, .74)
    _rect(m, .80, .56, .88, .68)


def g_lock(m, open_shackle=False, keyhole=True):
    _rect(m, .17, .44, .83, .92, r=.08)
    x = .62 if open_shackle else .50
    _arc(m, x, .40, .21, 180, 360, .095, ry=.24)
    _line(m, [(x - .21, .40), (x - .21, .46)], .095)
    _line(m, [(x + .21, .40), (x + .21, .46)], .095)
    if keyhole:
        _ell(m, .50, .62, .075, v=0)
        _poly(m, [(.455, .64), (.545, .64), (.565, .80), (.435, .80)], v=0)


def g_lock_closed(m):
    g_lock(m)


def g_lock_key(m):
    _rect(m, .17, .44, .83, .92, r=.08)
    _arc(m, .50, .40, .21, 180, 360, .095, ry=.24)
    _line(m, [(.29, .40), (.29, .46)], .095)
    _line(m, [(.71, .40), (.71, .46)], .095)
    _ell(m, .50, .62, .085, v=0)
    _rect(m, .465, .62, .535, .82, v=0)
    _rect(m, .535, .70, .62, .755, v=0)


def g_shield(m):
    _poly(m, [(.50, .07), (.87, .22), (.87, .50), (.50, .93), (.13, .50), (.13, .22)])
    _line(m, [(.32, .48), (.45, .62), (.70, .34)], .10, v=0)


def g_chart_up(m):
    _line(m, [(.10, .88), (.10, .12)], .08)
    _line(m, [(.10, .88), (.92, .88)], .08)
    _line(m, [(.22, .70), (.42, .48), (.58, .58), (.86, .24)], .105)
    _poly(m, [(.86, .24), (.62, .26), (.84, .48)])


def g_chart_down(m):
    _line(m, [(.10, .88), (.10, .12)], .08)
    _line(m, [(.10, .88), (.92, .88)], .08)
    _line(m, [(.22, .28), (.42, .50), (.58, .40), (.86, .74)], .105)
    _poly(m, [(.86, .74), (.62, .72), (.84, .50)])


def g_chart_money(m):
    """Candlesticks — distinct from the plain trend line of 📈 at 20 px."""
    _line(m, [(.08, .90), (.92, .90)], .07)
    for x, y0, y1, w0, w1 in ((.22, .44, .78, .30, .86),
                              (.50, .22, .56, .12, .70),
                              (.78, .38, .66, .24, .80)):
        _line(m, [(x, w0), (x, w1)], .055)
        _rect(m, x - .10, min(y0, y1), x + .10, max(y0, y1), r=.02)


def g_bars(m):
    _rect(m, .13, .52, .32, .90, r=.03)
    _rect(m, .40, .26, .59, .90, r=.03)
    _rect(m, .67, .40, .86, .90, r=.03)


def g_abacus(m):
    _rect(m, .10, .12, .90, .88, r=.06, v=255)
    _rect(m, .20, .22, .80, .78, v=0)
    for y in (.32, .50, .68):
        _rect(m, .20, y - .025, .80, y + .025)
    for x, y in ((.30, .32), (.44, .32), (.62, .50), (.34, .68), (.70, .68)):
        _ell(m, x, y, .075)


def g_slider(m):
    _line(m, [(.08, .30), (.92, .30)], .07)
    _line(m, [(.08, .70), (.92, .70)], .07)
    _rect(m, .28, .16, .42, .44, r=.04)
    _rect(m, .62, .56, .76, .84, r=.04)


def g_exchange(m):
    _arrow(m, .12, .32, .84, .32, head=.20, w=.095)
    _arrow(m, .88, .70, .16, .70, head=.20, w=.095)


def g_shuffle(m):
    _arrow(m, .10, .28, .86, .72, head=.19, w=.09)
    _arrow(m, .10, .72, .86, .28, head=.19, w=.09)


def g_refresh(m):
    _arc(m, .5, .5, .36, 40, 320, .115)
    _poly(m, [(.96, .38), (.61, .34), (.83, .04)])


def g_recycle(m):
    """Three arrows chasing each other round a triangle."""
    import math
    v = [(.5 + .44 * math.cos(math.radians(-90 + k * 120)),
          .5 + .44 * math.sin(math.radians(-90 + k * 120))) for k in range(3)]
    for i in range(3):
        a, b = v[i], v[(i + 1) % 3]

        def at(t):
            return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        (x0, y0), (x1, y1) = at(.14), at(.86)
        _arrow(m, x0, y0, x1, y1, head=.24, w=.085)


def g_arrow_left(m):
    _arrow(m, .88, .50, .14, .50, head=.28, w=.13)


def g_arrow_right(m):
    _arrow(m, .12, .50, .86, .50, head=.28, w=.13)


def g_arrow_back(m):
    """↩️ — leftwards arrow whose tail elbows down on the right."""
    _line(m, [(.84, .84), (.84, .34), (.46, .34)], .115)
    _arrow(m, .60, .34, .12, .34, head=.26, w=.115)


def g_point_down(m):
    _rect(m, .38, .10, .62, .52, r=.10)
    _poly(m, [(.20, .48), (.80, .48), (.50, .92)])


def g_tri_down(m):
    _poly(m, [(.16, .30), (.84, .30), (.50, .82)])


def g_plus(m):
    _rect(m, .42, .14, .58, .86, r=.06)
    _rect(m, .14, .42, .86, .58, r=.06)


def g_minus(m):
    _rect(m, .12, .42, .88, .58, r=.07)


def g_star(m):
    import math
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        r = .44 if i % 2 == 0 else .19
        pts.append((.5 + r * math.cos(a), .5 + r * math.sin(a)))
    _poly(m, pts)


def g_target(m):
    _ell(m, .5, .5, .42, w=.085)
    _ell(m, .5, .5, .25, w=.085)
    _ell(m, .5, .5, .09)


def g_search(m, flip=False):
    cx = .60 if not flip else .40
    hx0, hx1 = (.14, .38) if not flip else (.62, .86)
    _ell(m, cx, .40, .28, w=.095)
    _line(m, [(cx - .20 if not flip else cx + .20, .60),
              (hx0 if not flip else hx1, .86)], .12)
    _ = hx1


def g_search_left(m):
    g_search(m, flip=True)


def _link_pair(m):
    _rect(m, .06, .38, .56, .62, r=.12)
    _rect(m, .14, .455, .48, .545, r=.045, v=0)
    _rect(m, .44, .38, .94, .62, r=.12)
    _rect(m, .52, .455, .86, .545, r=.045, v=0)


def g_link(m):
    _rotated(m, _link_pair, -40)


def g_tag(m):
    _poly(m, [(.08, .46), (.48, .08), (.90, .08), (.90, .50), (.50, .90)])
    _ell(m, .72, .27, .085, v=0)


def g_pin(m):
    _poly(m, [(.34, .10), (.72, .10), (.66, .30), (.80, .48), (.24, .48), (.38, .30)])
    _line(m, [(.50, .48), (.50, .92)], .075)


def g_calendar(m):
    _rect(m, .10, .18, .90, .90, r=.07)
    _rect(m, .18, .40, .82, .82, v=0)
    _rect(m, .28, .07, .38, .28, r=.03)
    _rect(m, .62, .07, .72, .28, r=.03)
    for x in (.28, .47, .66):
        _rect(m, x, .52, x + .12, .64)


def g_folder(m):
    _poly(m, [(.08, .82), (.08, .20), (.40, .20), (.48, .32), (.92, .32), (.92, .82)])
    _rect(m, .16, .44, .84, .56, v=0)


def g_clipboard(m):
    _rect(m, .14, .16, .86, .92, r=.07)
    _rect(m, .24, .30, .76, .82, v=0)
    _rect(m, .34, .06, .66, .24, r=.04)
    for y in (.42, .56, .70):
        _rect(m, .32, y, .68, y + .07)


def g_receipt(m):
    _poly(m, [(.18, .08), (.82, .08), (.82, .92), (.70, .82), (.58, .92), (.46, .82),
              (.34, .92), (.18, .82)])
    for y in (.28, .44, .60):
        _rect(m, .30, y, .70, y + .07, v=0)


def g_scroll(m):
    _rect(m, .14, .18, .86, .82, r=.05)
    _rect(m, .24, .30, .76, .70, v=0)
    for y in (.36, .50, .64):
        _rect(m, .32, y - .03, .68, y + .03)
    _arc(m, .14, .50, .10, 90, 270, .07, ry=.32)
    _arc(m, .86, .50, .10, 270, 90, .07, ry=.32)


def g_news(m):
    _sheetp(m, .08, .16, .92, .88, fold=.0)
    _rect(m, .16, .24, .54, .44, v=0)
    for y in (.54, .64, .74):
        _rect(m, .16, y, .84, y + .055, v=0)
    _rect(m, .62, .24, .84, .44, v=0)


def g_doc_out(m):
    _rect(m, .10, .58, .90, .90, r=.06)
    _rect(m, .20, .68, .80, .80, v=0)
    _arrow(m, .50, .48, .50, .06, head=.20, w=.11)


def g_envelope_in(m):
    _rect(m, .08, .40, .92, .90, r=.05)
    _poly(m, [(.08, .44), (.50, .72), (.92, .44), (.92, .52), (.50, .80), (.08, .52)], v=0)
    _arrow(m, .50, .04, .50, .34, head=.16, w=.09)


def g_mailbox_empty(m):
    """Open, hollow carton — 'нет данных' reads faster than a mailbox flag."""
    _rect(m, .10, .42, .90, .92, r=.04)
    _rect(m, .19, .51, .81, .83, v=0)                      # hollow interior
    _poly(m, [(.10, .44), (.26, .14), (.46, .26), (.30, .50)])   # open flaps
    _poly(m, [(.90, .44), (.74, .14), (.54, .26), (.70, .50)])


def g_megaphone(m):
    _poly(m, [(.86, .12), (.86, .88), (.34, .66), (.34, .34)])
    _rect(m, .12, .36, .36, .64, r=.05)
    _line(m, [(.26, .66), (.34, .92)], .10)


def g_bubble(m):
    _rect(m, .08, .14, .92, .74, r=.14)
    _poly(m, [(.26, .70), (.48, .70), (.28, .94)])
    for x in (.28, .46, .64):
        _ell(m, x, .44, .065, v=0)


def g_disk(m):
    _rect(m, .10, .10, .90, .90, r=.06)
    _rect(m, .30, .12, .70, .40, v=0)
    _rect(m, .54, .16, .64, .34)
    _rect(m, .24, .56, .76, .88, v=0)


def g_coin(m):
    _ell(m, .5, .5, .40)
    _ell(m, .5, .5, .30, v=0)
    _line(m, [(.50, .24), (.50, .76)], .075)
    _arc(m, .50, .375, .13, 20, 250, .07, ry=.11)
    _arc(m, .50, .625, .13, 200, 70, .07, ry=.11)


def g_banknote(m):
    _rect(m, .06, .26, .94, .74, r=.06)
    _rect(m, .14, .34, .86, .66, v=0)
    _ell(m, .50, .50, .13)
    _ell(m, .50, .50, .06, v=0)
    _rect(m, .18, .38, .26, .46)
    _rect(m, .74, .54, .82, .62)


def g_money_fly(m):
    _rect(m, .34, .28, .98, .78, r=.06)
    _rect(m, .42, .36, .90, .70, v=0)
    _ell(m, .66, .53, .12)
    _ell(m, .66, .53, .05, v=0)
    for y, x1 in ((.34, .24), (.53, .14), (.72, .26)):     # motion trail
        _line(m, [(.02, y), (x1, y)], .075)


def g_gem(m):
    _poly(m, [(.28, .16), (.72, .16), (.94, .40), (.50, .90), (.06, .40)])
    _line(m, [(.28, .16), (.38, .40), (.50, .90)], .05, v=0)
    _line(m, [(.72, .16), (.62, .40), (.50, .90)], .05, v=0)
    _line(m, [(.06, .40), (.94, .40)], .05, v=0)


def g_diamond(m):
    _poly(m, [(.50, .10), (.90, .50), (.50, .90), (.10, .50)])


def g_ticket(m):
    _rect(m, .06, .26, .94, .74, r=.06)
    _ell(m, .06, .50, .09, v=0)
    _ell(m, .94, .50, .09, v=0)
    _rect(m, .46, .30, .54, .70, v=0)
    _rect(m, .485, .38, .515, .46)
    _rect(m, .485, .54, .515, .62)


def g_slots(m):
    _rect(m, .10, .20, .90, .90, r=.07)
    _rect(m, .20, .34, .80, .62, v=0)
    for x in (.26, .45, .64):
        _rect(m, x, .40, x + .10, .56)
    _rect(m, .30, .70, .70, .80, v=0)
    _line(m, [(.90, .34), (.90, .16)], .07)
    _ell(m, .90, .11, .08)


def g_pingpong(m):
    _line(m, [(.36, .56), (.18, .90)], .12)                # handle
    _ell(m, .44, .40, .29, .27)                            # blade (solid)
    _ell(m, .82, .20, .12)                                 # ball


def g_gear(m):
    import math
    _ell(m, .5, .5, .30)
    for i in range(8):
        a = math.radians(i * 45)
        _poly(m, [(.5 + .24 * math.cos(a - .30), .5 + .24 * math.sin(a - .30)),
                  (.5 + .24 * math.cos(a + .30), .5 + .24 * math.sin(a + .30)),
                  (.5 + .45 * math.cos(a + .17), .5 + .45 * math.sin(a + .17)),
                  (.5 + .45 * math.cos(a - .17), .5 + .45 * math.sin(a - .17))])
    _ell(m, .5, .5, .13, v=0)


def g_warn(m):
    _poly(m, [(.50, .06), (.97, .90), (.03, .90)])
    _rect(m, .45, .34, .55, .62, v=0)
    _ell(m, .50, .74, .06, v=0)


def g_write(m):
    _poly(m, [(.72, .06), (.94, .28), (.36, .86), (.10, .92), (.16, .66)])
    _line(m, [(.64, .14), (.86, .36)], .05, v=0)
    _poly(m, [(.10, .92), (.16, .74), (.28, .86)], v=0)


def g_dog(m):
    """Paw print — the watchdog mark. Reads at 20 px; a dog head does not."""
    _ell(m, .50, .68, .27, .22)                            # pad
    _ell(m, .18, .40, .12, .15)                            # toes
    _ell(m, .40, .24, .12, .16)
    _ell(m, .64, .24, .12, .16)
    _ell(m, .86, .42, .12, .15)


def g_dot_ring(m):
    _ell(m, .5, .5, .33, w=.10)


# ---------------------------------------------------------------- registry
# standard emoji -> (file stem, painter, colour). The emoji key is what the bot
# already prints, so non-Premium users keep seeing exactly that glyph.
GLYPHS: list[tuple[str, str, Painter, tuple[int, int, int]]] = [
    ("✅", "done", g_check, CITRON),
    ("❌", "fail", g_cross, RED),
    ("✖️", "x_mark", g_cross, DIM),
    ("🟢", "dot_green", g_dot, GREEN),
    ("🔴", "dot_red", g_dot, RED),
    ("🟡", "dot_yellow", g_dot, AMBER),
    ("🟠", "dot_orange", g_dot, ORANGE),
    ("🟣", "dot_purple", g_dot, VIOLET),
    ("⚪️", "dot_white", g_dot_ring, DIM),
    ("🔷", "diamond_blue", g_diamond, CYAN),
    ("🏆", "win", g_trophy, AMBER),
    ("👑", "crown", g_crown, AMBER),
    ("⭐", "star", g_star, AMBER),
    ("🎁", "gift", g_gift, CITRON),
    ("🚫", "ban", g_ban, RED),
    ("⛔", "noentry", g_noentry, RED),
    ("🛑", "stop", g_stop, RED),
    ("🛰", "satellite", g_satellite, CITRON),
    ("📡", "dish", g_dish, CITRON),
    ("🎯", "target", g_target, CITRON),
    ("🔎", "search", g_search, CITRON),
    ("🔍", "search_left", g_search_left, CITRON),
    ("👁", "eye", g_eye, CITRON),
    ("🙈", "eye_off", g_eye_off, DIM),
    ("🔔", "bell", g_bell, CITRON),
    ("🔕", "bell_off", g_bell_off, DIM),
    ("🔇", "mute", g_mute, DIM),
    ("⚡", "bolt", g_bolt, AMBER),
    ("🔥", "fire", g_fire, AMBER),
    ("🕐", "clock1", g_clock1, CYAN),
    ("🕓", "clock4", g_clock4, CYAN),
    ("🕘", "clock9", g_clock9, CYAN),
    ("⏰", "alarm", g_alarm, AMBER),
    ("⏳", "hourglass_flow", g_hourglass_flow, AMBER),
    ("⌛", "hourglass_done", g_hourglass_done, AMBER),
    ("👤", "user", g_user, DIM),
    ("👥", "users", g_users, DIM),
    ("🗑", "trash", g_trash, DIM),
    ("🔑", "key", g_key, CITRON),
    ("🔒", "lock_closed", g_lock_closed, RED),
    ("🔐", "lock_key", g_lock_key, CITRON),
    ("🛡", "shield", g_shield, CITRON),
    ("📈", "chart_up", g_chart_up, CITRON),
    ("📉", "chart_down", g_chart_down, RED),
    ("💹", "chart_money", g_chart_money, CITRON),
    ("📊", "bars", g_bars, CITRON),
    ("🧮", "abacus", g_abacus, DIM),
    ("🎚", "slider", g_slider, CITRON),
    ("💱", "exchange", g_exchange, CITRON),
    ("🔀", "shuffle", g_shuffle, CITRON),
    ("🔄", "refresh", g_refresh, CITRON),
    ("♻️", "recycle", g_recycle, CITRON),
    ("⬅️", "arrow_left", g_arrow_left, DIM),
    ("➡️", "arrow_right", g_arrow_right, DIM),
    ("↩️", "arrow_back", g_arrow_back, DIM),
    ("👇", "point_down", g_point_down, CITRON),
    ("🔽", "tri_down", g_tri_down, RED),
    ("➕", "plus", g_plus, CITRON),
    ("➖", "minus", g_minus, DIM),
    ("⚙️", "gear", g_gear, DIM),
    ("⚠️", "warn", g_warn, AMBER),
    ("🔗", "link", g_link, CYAN),
    ("🏷", "tag", g_tag, CITRON),
    ("📌", "pin", g_pin, RED),
    ("📅", "calendar", g_calendar, CYAN),
    ("📂", "folder", g_folder, CITRON),
    ("📋", "clipboard", g_clipboard, DIM),
    ("🧾", "receipt", g_receipt, DIM),
    ("📜", "scroll", g_scroll, DIM),
    ("📰", "news", g_news, DIM),
    ("📤", "doc_out", g_doc_out, CITRON),
    ("📨", "envelope_in", g_envelope_in, CYAN),
    ("📭", "mailbox_empty", g_mailbox_empty, DIM),
    ("📣", "megaphone", g_megaphone, CITRON),
    ("💬", "bubble", g_bubble, CYAN),
    ("💾", "disk", g_disk, CYAN),
    ("🪙", "coin", g_coin, AMBER),
    ("💵", "banknote", g_banknote, GREEN),
    ("💸", "money_fly", g_money_fly, GREEN),
    ("💎", "gem", g_gem, CYAN),
    ("🎫", "ticket", g_ticket, CITRON),
    ("🎰", "slots", g_slots, CITRON),
    ("🏓", "pingpong", g_pingpong, CITRON),
    ("✍️", "write", g_write, CITRON),
    ("🐕", "dog", g_dog, AMBER),
]

# Back-compat shape for upload_emoji_pack.py: stem -> (emoji, painter).
EMOJI_MAP: dict[str, tuple[str, Painter]] = {
    stem: (emoji, paint) for emoji, stem, paint, _ in GLYPHS
}


# --------------------------------------------------------------- rendering
def render(paint: Painter, color: tuple[int, int, int], size: int = S) -> Image.Image:
    """Draw one tile glyph at `size` px, supersampled then downscaled."""
    W = size * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    box = [W * .03, W * .03, W * .97, W * .97]
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=W * .28, fill=SLATE + (255,))
    d.rounded_rectangle(box, radius=W * .28, outline=KEYLINE + (205,),
                        width=max(1, int(W * .024)))

    inner = int(W * .70)
    m = Image.new("L", (inner, inner), 0)
    paint(m)
    mask = Image.new("L", (W, W), 0)
    mask.paste(m, ((W - inner) // 2, (W - inner) // 2))
    img.paste(color + (255,), (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


def write_all() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Generating {len(GLYPHS)} Pulse Desk custom emoji (Aperture tiles):")
    for emoji, stem, paint, color in GLYPHS:
        render(paint, color).save(OUT / f"{stem}.webp", "WEBP",
                                  lossless=True, quality=100, method=6)
        print(f"  {stem}.webp  →  assign emoji: {emoji}")
    print(f"\nDone — {len(GLYPHS)} glyphs in {OUT}")


# ------------------------------------------------------------ contact sheet
def _font(name: str, px: int):
    try:
        return ImageFont.truetype(rf"C:\Windows\Fonts\{name}", px)
    except Exception:
        return ImageFont.load_default()


def _chat_strip(w: int, bg, fg, label: str) -> Image.Image:
    """A fake chat bubble with glyphs at their real inline size (20 px)."""
    rows = [("Победа", ["win", "gift", "done", "star", "coin"]),
            ("Скан", ["satellite", "dish", "target", "eye", "refresh", "bars"]),
            ("Доступ", ["key", "lock_closed", "user", "users", "ban", "warn"])]
    h = 34 + len(rows) * 34
    img = Image.new("RGBA", (w, h + 16), bg + (255,))
    d = ImageDraw.Draw(img)
    bub = (44, 49, 54) if bg[0] < 100 else (234, 239, 244)
    d.rounded_rectangle([12, 8, w - 60, h + 4], radius=14, fill=bub + (255,))
    f = _font("segoeui.ttf", 18)
    d.text((w - 52, 12), label, font=_font("segoeui.ttf", 12), fill=(128, 138, 134))
    y = 22
    for title, stems in rows:
        x = 28
        d.text((x, y), f"{title}:", font=f, fill=fg + (255,))
        x += int(d.textlength(f"{title}:", font=f)) + 10
        for stem in stems:
            _e, _s, paint, color = next(g for g in GLYPHS if g[1] == stem)
            img.alpha_composite(render(paint, color, 20), (x, y - 1))
            x += 27
        y += 34
    return img


def write_sheet(path: Path) -> None:
    cell, pad, cols = 72, 10, 12
    rows = (len(GLYPHS) + cols - 1) // cols
    grid_h = rows * (cell + 26) + pad
    strip_w = cols * (cell + pad) + pad
    dark = _chat_strip(strip_w, (23, 26, 30), (232, 238, 244), "тёмная тема")
    light = _chat_strip(strip_w, (255, 255, 255), (28, 32, 36), "светлая тема")
    sheet = Image.new("RGBA", (strip_w, 52 + grid_h + dark.height + light.height + 20),
                      (18, 20, 22, 255))
    d = ImageDraw.Draw(sheet)
    d.text((pad + 4, 14), f"Pulse Desk · Aperture · {len(GLYPHS)} глифов",
           font=_font("seguisb.ttf", 21), fill=(232, 240, 232))
    fs = _font("segoeui.ttf", 11)
    y = 52
    for i, (emoji, stem, paint, color) in enumerate(GLYPHS):
        cx = pad + (i % cols) * (cell + pad)
        cy = y + (i // cols) * (cell + 26)
        sheet.alpha_composite(render(paint, color, cell), (cx, cy))
        d.text((cx + cell // 2, cy + cell + 4), stem[:11], font=fs,
               fill=(122, 132, 128), anchor="ma")
    y += grid_h + 8
    sheet.alpha_composite(dark, (0, y))
    y += dark.height
    sheet.alpha_composite(light, (0, y))
    sheet.convert("RGB").save(path)
    print(f"  contact sheet → {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Aperture custom-emoji set.")
    ap.add_argument("--sheet", nargs="?", const=str(BASE / "assets" / "bot" / "emoji-preview.png"),
                    default=None, help="also write a contact sheet for review")
    ap.add_argument("--only-sheet", action="store_true", help="skip the .webp files")
    args = ap.parse_args()
    if not args.only_sheet:
        write_all()
    if args.sheet or args.only_sheet:
        write_sheet(Path(args.sheet or BASE / "assets" / "bot" / "emoji-preview.png"))


if __name__ == "__main__":
    main()
