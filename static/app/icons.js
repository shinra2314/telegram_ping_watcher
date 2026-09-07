// Line-art icons for the Mini App, drawn on a 24x24 grid.
//
// Stroke-only and `currentColor`, so one icon serves every state and theme —
// the .webp tiles in assets/bot/emoji cannot: they are raster, fixed-colour and
// sized for Telegram's custom-emoji slot. The silhouettes follow the same
// shapes as scripts/generate_bot_emoji.py so the panel and the bot's card
// emoji read as one set.
const ICONS = {
  gift: 'M3 7h18v4H3zM5 11v9h14v-9M12 7v13'
      + 'M12 7c0 0-2-4-4-4a2 2 0 0 0 0 4h4M12 7c0 0 2-4 4-4a2 2 0 0 1 0 4h-4',
  // Handles are cubics rather than arcs: an arc whose endpoints sit further
  // apart than its diameter gets silently rescaled, which turned the cup into
  // a wine glass.
  trophy: 'M7 3.5h10v5.5a5 5 0 0 1-10 0z'
        + 'M7 5.5C4.7 5.5 3.5 6.6 3.5 8c0 1.5 1.4 2.8 3.5 2.9'
        + 'M17 5.5c2.3 0 3.5 1.1 3.5 2.5 0 1.5-1.4 2.8-3.5 2.9'
        + 'M12 14.5v3.5M8.5 20.5h7',
  // A stack of tiers rather than two overlapping discs: overlapping ellipses
  // put a stray diagonal through the lower coin at any size.
  coins: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z'
       + 'M4 7v4c0 1.7 3.6 3 8 3s8-1.3 8-3V7'
       + 'M4 11v4c0 1.7 3.6 3 8 3s8-1.3 8-3v-4',
  swap: 'M4 9h13M14 6l3 3-3 3M20 15H7M10 12l-3 3 3 3',
  chart: 'M3 21h18M7 21V10M12 21V4M17 21v-7',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7.5V12l3 2',
  bell: 'M6 9a6 6 0 1 1 12 0c0 3.5 1.5 5 2 5.5H4c.5-.5 2-2 2-5.5zM10 19a2 2 0 0 0 4 0',
  menu: 'M4 7h16M4 12h16M4 17h11',
  back: 'M14 5l-7 7 7 7',
  spark: 'M12 3l2.1 5.9L20 11l-5.9 2.1L12 19l-2.1-5.9L4 11l5.9-2.1z',
};

function icon(name, size) {
  const path = ICONS[name] || ICONS.spark;
  const px = size || 22;
  return '<svg class="ic" viewBox="0 0 24 24" width="' + px + '" height="' + px + '"'
    + ' fill="none" stroke="currentColor" stroke-width="1.6"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="' + path + '"/></svg>';
}
