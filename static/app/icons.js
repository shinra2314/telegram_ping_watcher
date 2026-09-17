// Line-art icons for the panel, drawn on a 24x24 grid.
//
// Stroke-only and `currentColor`, so one icon serves every state — the .webp
// tiles in assets/bot/emoji cannot: they are raster, fixed-colour and sized for
// Telegram's custom-emoji slot. Silhouettes follow scripts/generate_bot_emoji.py
// so the panel and the bot's card emoji read as one set.
const ICONS = {
  gift: 'M3 7h18v4H3zM5 11v9h14v-9M12 7v13'
      + 'M12 7c0 0-2-4-4-4a2 2 0 0 0 0 4h4M12 7c0 0 2-4 4-4a2 2 0 0 1 0 4h-4',
  // Handles are cubics rather than arcs: an arc whose endpoints sit further
  // apart than its diameter gets silently rescaled into a wine glass.
  trophy: 'M7 3.5h10v5.5a5 5 0 0 1-10 0z'
        + 'M7 5.5C4.7 5.5 3.5 6.6 3.5 8c0 1.5 1.4 2.8 3.5 2.9'
        + 'M17 5.5c2.3 0 3.5 1.1 3.5 2.5 0 1.5-1.4 2.8-3.5 2.9'
        + 'M12 14.5v3.5M8.5 20.5h7',
  coins: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z'
       + 'M4 7v4c0 1.7 3.6 3 8 3s8-1.3 8-3V7'
       + 'M4 11v4c0 1.7 3.6 3 8 3s8-1.3 8-3v-4',
  swap: 'M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4',
  exchange: 'M4 9h13M14 6l3 3-3 3M20 15H7M10 12l-3 3 3 3',
  home: 'M4 10.5L12 4l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z',
  grid: 'M4 4h6.5v6.5H4zM13.5 4H20v6.5h-6.5zM4 13.5h6.5V20H4zM13.5 13.5H20V20h-6.5z',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5'
       + 'M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.8c1.9.7 3.2 2.5 3.5 5.2',
  phone: 'M7 2.5h10a1.5 1.5 0 0 1 1.5 1.5v16a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 20V4A1.5 1.5 0 0 1 7 2.5zM10.5 18h3',
  wallet: 'M4 7.5A2.5 2.5 0 0 1 6.5 5H18v3M4 7.5V18a2 2 0 0 0 2 2h14V8H6.5A2.5 2.5 0 0 1 4 5.5M16 14h.01',
  cash: 'M3 6.5h18v11H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6.5 9.5v5M17.5 9.5v5',
  chart: 'M3 21h18M7 21V10M12 21V4M17 21v-7',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7.5V12l3 2',
  refresh: 'M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4',
  plus: 'M12 5v14M5 12h14',
  check: 'M4.5 12.5l5 5L19.5 7',
  x: 'M6 6l12 12M18 6L6 18',
  chevron: 'M9 5l7 7-7 7',
  back: 'M15 5l-7 7 7 7',
  left: 'M15 5l-7 7 7 7',
  right: 'M9 5l7 7-7 7',
  down: 'M6 9l6 6 6-6',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  power: 'M12 3v8M6.4 6.6a8 8 0 1 0 11.2 0',
  plug: 'M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
  star: 'M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z',
  copy: 'M9 9h10v11H9zM5 15V4h10',
  lock: 'M6 10.5h12V20H6zM8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3',
  shield: 'M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z',
  alert: 'M12 4L2.8 19.5h18.4zM12 10v4.5M12 17.3h.01',
  pulse: 'M3 12h4l2.5-6 5 12 2.5-6h4',
  scan: 'M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M4 12h16',
  spark: 'M12 3l2.1 5.9L20 11l-5.9 2.1L12 19l-2.1-5.9L4 11l5.9-2.1z',
  send: 'M21 3L10 14M21 3l-7 18-4-7-7-4z',
  message: 'M4 5h16v11H9l-5 4z',
  flame: 'M12 3c1 3.5 5.5 5.5 5.5 10.5a5.5 5.5 0 0 1-11 0c0-2.5 1.3-4 2.5-5 .3 1.8 1 2.8 2 3.2C11 9.5 11 6 12 3z',
  sms: 'M4 5h16v11H9l-5 4zM8 10.5h.01M12 10.5h.01M16 10.5h.01',
};

function icon(name, size) {
  const path = ICONS[name] || ICONS.spark;
  const px = size || 22;
  return '<svg class="ic" viewBox="0 0 24 24" width="' + px + '" height="' + px + '"'
    + ' fill="none" stroke="currentColor" stroke-width="1.7"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="' + path + '"/></svg>';
}
