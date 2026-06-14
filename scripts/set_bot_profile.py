"""Upload assets/bot/avatar.png as the bot's profile photo via MTProto.

Uses an in-memory session signed in with the bot token, so it does not
touch the session file of a running app instance. The description picture
cannot be set through the API — only via BotFather.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

from telethon import TelegramClient, functions
from telethon.sessions import StringSession

from pulse_desk.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.bot_token or not settings.api_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_API_ID not configured in .env")
    avatar = BASE / "assets" / "bot" / "avatar.png"
    if not avatar.exists():
        raise SystemExit(f"Avatar not found: {avatar}")

    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
    await client.start(bot_token=settings.bot_token)
    try:
        me = await client.get_me()
        uploaded = await client.upload_file(str(avatar))
        await client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
        print(f"Profile photo updated for @{me.username}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
