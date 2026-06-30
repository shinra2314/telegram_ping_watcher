"""On-brand Aperture sticker delivery for the Telegram bot.

A registry + a best-effort sender. ``send_sticker`` never raises into a handler:
sticker fail → photo fallback → log + return False. A missing ``.webp`` resolves
to ``sticker_path()==None`` and the send is skipped. All sends are gated by the
global ``BOT_STICKERS_ENABLED`` flag.
"""
from __future__ import annotations

from typing import Optional

from ..app_ctx import BASE_DIR, logger, settings as _settings

STICKERS_DIR = BASE_DIR / "assets" / "bot" / "stickers"

# name -> (filename, alt-emoji shown by clients without sticker support)
_REGISTRY: dict[str, tuple[str, str]] = {
    "win": ("win.webp", "🏆"),
    "giveaway": ("giveaway.webp", "🎁"),
    "check": ("check.webp", "💸"),
    "scan": ("scan.webp", "🛰"),
    "scan_done": ("scan_done.webp", "✅"),
    "access_on": ("access_on.webp", "🟢"),
    "access_off": ("access_off.webp", "🔴"),
    "welcome": ("welcome.webp", "🛰"),
    "empty": ("empty.webp", "📭"),
    "error": ("error.webp", "⚠️"),
    "pong": ("pong.webp", "🏓"),
}


def sticker_alt(name: str) -> str:
    entry = _REGISTRY.get(name)
    return entry[1] if entry else "✨"


def sticker_path(name: str) -> Optional[str]:
    entry = _REGISTRY.get(name)
    if not entry:
        return None
    path = STICKERS_DIR / entry[0]
    return str(path) if path.exists() else None


async def send_sticker(client, peer, name: str) -> bool:
    """Best-effort: send the Aperture ``.webp`` as a sticker. Never raises."""
    if client is None or peer in (None, ""):
        return False
    if not getattr(_settings, "bot_stickers_enabled", True):
        return False
    path = sticker_path(name)
    if not path:
        return False
    try:
        from telethon.tl.types import DocumentAttributeSticker, InputStickerSetEmpty
        await client.send_file(
            peer, path,
            attributes=[DocumentAttributeSticker(alt=sticker_alt(name),
                                                 stickerset=InputStickerSetEmpty())],
            force_document=False,
        )
        return True
    except Exception:
        try:
            await client.send_file(peer, path)  # fall back to a plain image
            return True
        except Exception:
            logger.warning("Sticker send failed: %s", name, exc_info=True)
            return False
