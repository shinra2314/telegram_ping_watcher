"""Create the Pulse Desk custom-emoji pack automatically via @Stickers.

Drives the @Stickers conversation from a Premium user session (Telethon
Conversation API): /newemojipack -> name -> upload each 100px webp as a
document + assign its emoji -> /publish -> /skip icon -> short name.

A custom-emoji pack can only be created by a Telegram **Premium** account, so
this runs on one of the user sessions in ./sessions (not the bot). The chosen
session file is COPIED to a temp path before connecting, so this never fights
the running app for the same .session file.

Usage:
  python scripts/upload_emoji_pack.py --short pulsedesk_aperture
  python scripts/upload_emoji_pack.py --short <name> --title "Pulse Desk" \
         --session MuverGT --set-env

After it prints the short name, set BOT_CUSTOM_EMOJI_SET=<short> and restart
the bot (or pass --set-env to write it into .env automatically).
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import SQLiteSession  # noqa: E402

from generate_bot_emoji import EMOJI_MAP, OUT  # noqa: E402
from pulse_desk.config import Settings  # noqa: E402

STICKERS_BOT = "Stickers"
SESSIONS_DIR = BASE / "sessions"


async def _drain(conv, *, first: float = 12.0, idle: float = 3.0) -> str:
    """Read @Stickers' reply(ies) after an action; returns the joined text."""
    msgs = []
    try:
        msgs.append(await conv.get_response(timeout=first))
    except asyncio.TimeoutError:
        return ""
    while True:
        try:
            msgs.append(await conv.get_response(timeout=idle))
        except asyncio.TimeoutError:
            break
    return "\n".join((m.message or "").strip() for m in msgs)


def _looks_like_error(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in ("sorry", "invalid", "too many", "already taken",
                                  "isn't valid", "is not valid", "wrong"))


async def _build_with_session(session_path: Path, settings: Settings, *,
                              title: str, short: str) -> bool:
    client = TelegramClient(SQLiteSession(str(session_path)),
                            settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print(f"  session {session_path.stem}: not authorized, skipping")
            return False
        me = await client.get_me()
        if not getattr(me, "premium", False):
            print(f"  session @{me.username or me.id}: no Premium, skipping")
            return False
        print(f"  using Premium session @{me.username or me.id}")

        async with client.conversation(STICKERS_BOT, timeout=90) as conv:
            await conv.send_message("/cancel")     # reset any pending flow
            await _drain(conv, first=6, idle=2)
            await conv.send_message("/newemojipack")
            print("  > /newemojipack:", (await _drain(conv))[:80])
            await conv.send_message(title)
            print("  > title:", (await _drain(conv))[:80])

            for i, (name, (emoji, _glyph)) in enumerate(EMOJI_MAP.items(), 1):
                f = OUT / f"{name}.webp"
                await conv.send_file(str(f), force_document=True)
                r = await _drain(conv)
                if _looks_like_error(r):
                    print(f"  ! upload {name} rejected: {r[:120]}")
                    return False
                await conv.send_message(emoji)
                r = await _drain(conv)
                print(f"  [{i:>2}/{len(EMOJI_MAP)}] {name} {emoji}: {r[:50]}")
                if _looks_like_error(r):
                    print(f"  ! emoji assign for {name} failed: {r[:120]}")
                    return False

            await conv.send_message("/publish")
            print("  > /publish:", (await _drain(conv))[:80])
            await conv.send_message("/skip")        # skip the pack icon
            print("  > /skip icon:", (await _drain(conv))[:80])
            await conv.send_message(short)
            final = await _drain(conv)
            print("  > short name:", final[:160])
            if _looks_like_error(final):
                print("  ! short name rejected (taken/invalid). Pick another --short.")
                return False
        print(f"\nDONE. Pack: https://t.me/addemoji/{short}")
        return True
    finally:
        await client.disconnect()


def _pick_sessions(explicit: str | None) -> list[Path]:
    if explicit:
        p = SESSIONS_DIR / f"{explicit}.session"
        return [p] if p.exists() else []
    return sorted(SESSIONS_DIR.glob("*.session"))


async def main_async(args) -> int:
    settings = Settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env")
        return 2
    missing = [n for n in EMOJI_MAP if not (OUT / f"{n}.webp").exists()]
    if missing:
        print(f"Missing emoji files: {missing}. Run generate_bot_emoji.py first.")
        return 2

    candidates = _pick_sessions(args.session)
    if not candidates:
        print(f"No session files found in {SESSIONS_DIR} (or --session not found).")
        return 2

    for src in candidates:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / src.name
            shutil.copy2(src, tmp)
            ok = await _build_with_session(tmp, settings, title=args.title, short=args.short)
        if ok:
            if args.set_env:
                _write_env(args.short)
            return 0
    print("\nNo Premium session succeeded. Pass --session <name> of your Premium account.")
    return 1


def _write_env(short: str) -> None:
    env = BASE / ".env"
    line = f"BOT_CUSTOM_EMOJI_SET={short}"
    if not env.exists():
        env.write_text(line + "\n", encoding="utf-8")
        print(f"  wrote {line} to new .env")
        return
    text = env.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("BOT_CUSTOM_EMOJI_SET="):
            lines[i] = line
            break
    else:
        lines.append(line)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  set {line} in .env — restart the bot to load it")


def main() -> None:
    ap = argparse.ArgumentParser(description="Create the Pulse Desk custom-emoji pack via @Stickers.")
    ap.add_argument("--short", required=True, help="global short name, e.g. pulsedesk_aperture")
    ap.add_argument("--title", default="Pulse Desk Aperture", help="pack display name")
    ap.add_argument("--session", default=None, help="session stem in ./sessions (default: auto-pick Premium)")
    ap.add_argument("--set-env", action="store_true", help="write BOT_CUSTOM_EMOJI_SET into .env on success")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
