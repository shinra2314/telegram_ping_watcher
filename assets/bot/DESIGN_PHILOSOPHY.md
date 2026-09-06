# Aperture

**A design philosophy for the Pulse Desk brand set.**

Aperture is the optics of the machine that catches the signal. Where an earlier
identity rendered listening as an emotional sky, Aperture renders it as the cold,
exact instrument that does the work — a viewfinder the instant before it locks on
a target. The brand is not the feeling of an arrival; it is the precision tool
that found it. Every canvas is the focal plane of a device built by someone who
trusts measurement over mood.

The body is graphite. A near-black field, faintly darker at the corners so the
eye falls to the centre, carries a single oscilloscope grid: minor lines and
brighter majors, drawn from the centre outward so the geometry stays symmetric.
The grid is not decoration but the instrument's own reference frame — the quiet
ruled surface against which any deviation becomes visible. Nothing floats; every
element sits on a coordinate.

The core motif is the focus reticle. Four corner brackets close in on a ring;
the ring carries fine measurement ticks; a crosshair extends along the four axes;
and at the exact centre sits one locked dot. The dot is the only soft light in
the entire system — a small, disciplined bloom reserved for the moment of
contact. One reticle, one lock, never a crowd. Restraint is the whole point: the
mark must read at the size of a chat-list avatar and still feel like an
instrument, not an ornament.

Colour is governed to a single rule. Graphite is the body; structural marks — the
viewfinder brackets framing the canvas — stay dim, the grey of machined metal,
and never borrow the accent. Electric citron is the one signal colour, spent only
where meaning is: the reticle, the scan node, the lock. For alerts the signal
colour shifts to carry type — ice cyan for a mention, citron for a giveaway,
warm amber for a win — but the system never lights up everywhere at once. White
appears as sparse instrument type and nothing else.

Form is patient and off-centre. The reticle may sit toward one edge, its outer
brackets and ring exiting the frame, implying the field of view continues beyond
what we are permitted to see. A single horizontal scan line crosses the canvas
with its own measurement ticks and one bright node at the lock point — one sweep,
never a flurry. Negative space is listening room, not emptiness.

Typography is an instrument readout, not a voice. Thin geometric letterforms,
widely tracked, set the name; a monospace caption timestamps the canvas with a
unit code and a discipline — `PD·01 // SIGNAL LOCK`. Words never explain; they
label. The work must read as the panel of a device engineered by someone at the
top of their field — every grid interval measured, every bracket arm the same
length, master-level execution disguised as the plainness of a tool that simply
works.

## The glyph system

The bot speaks in emoji, and stock emoji are somebody else's design language —
three vendors' worth of gradients, bevels and cartoon faces dropped into an
instrument panel. Aperture replaces them with one drawn set: every glyph in
`assets/bot/emoji/` is geometry from `scripts/generate_bot_emoji.py`, not a
system font traced at 100 px.

Each glyph is a graphite tile with a citron keyline and a flat silhouette. The
tile is structural, not decorative: a bare citron mark washes out on a light
chat theme and a graphite one vanishes on a dark theme, so the glyph carries its
own ground and reads identically in both. Silhouettes are cut, never shaded —
interior detail is negative space, because a hole survives the downscale to
18 px that a second colour does not.

Colour is the same governed rule, narrowed to five tokens: citron is action,
amber is value, red is danger, cyan is information, dim white is structure. The
status dots are the one exception — there the colour *is* the meaning, so they
keep their literal hue and nothing else.

Coverage is part of the design. A set that maps half the emoji a surface uses is
worse than no set at all: the eye reads the seam between the two systems before
it reads either. The pack therefore covers every emoji the bot prints in message
text. Inline keyboard labels cannot carry entities and always render stock —
that boundary is a Telegram limit, not a choice, and it is the reason button
labels stay sparse.

The stickers are the same silhouettes at 512 px, inside the reticle, with a unit
code beneath. One drawing, two scales — a sticker and its inline emoji are never
two different pictures of the same idea.
