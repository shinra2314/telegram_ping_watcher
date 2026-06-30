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
from time import monotonic

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import SQLiteSession  # noqa: E402
from telethon.tl.functions.messages import GetStickerSetRequest  # noqa: E402
from telethon.tl.types import InputStickerSetShortName  # noqa: E402

from generate_bot_emoji import EMOJI_MAP, OUT  # noqa: E402
from pulse_desk.config import Settings  # noqa: E402

STICKERS_BOT = "Stickers"
SESSIONS_DIR = BASE / "sessions"


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

        # Manual send + poll history for replies. Telethon's Conversation API
        # races the update dispatcher here (InvalidStateError); actively fetching
        # messages with get_messages is reliable, and @Stickers uses inline
        # buttons (emoji type picker) that need a real callback click.
        peer = await client.get_input_entity(STICKERS_BOT)
        last = {"id": 0}
        seed = await client.get_messages(peer, limit=1)
        if seed:
            last["id"] = seed[0].id

        def _short(s: str) -> str:
            return (s or "")[:80].replace("\n", " ")

        async def _latest():
            m = await client.get_messages(peer, limit=1)
            return m[0] if m else None

        async def _wait_reply(prev_text=None, timeout: float = 30.0):
            """Return (text, msg) of the next @Stickers message or edit."""
            end = monotonic() + timeout
            while monotonic() < end:
                await asyncio.sleep(1.5)
                recent = await client.get_messages(peer, limit=6)
                incoming = [m for m in recent if not m.out]
                if not incoming:
                    continue
                newest = max(incoming, key=lambda m: m.id)
                txt = newest.message or ""
                if newest.id > last["id"] or (prev_text is not None and txt != prev_text):
                    last["id"] = max(last["id"], newest.id)
                    return txt, newest
            return "", await _latest()

        async def send(*, text=None, file=None, timeout: float = 30.0):
            if file is not None:
                await client.send_file(peer, file, force_document=True)
            elif text is not None:
                await client.send_message(peer, text)
            return await _wait_reply(timeout=timeout)

        async def click(matches, prev_text: str, timeout: float = 30.0):
            msg = await _latest()
            target = None
            for row in (getattr(msg, "buttons", None) or []):
                for btn in row:
                    label = (btn.text or "").lower()
                    if any(m in label for m in matches):
                        target = btn
                        break
                if target:
                    break
            if target is None:
                return "__nobtn__", msg
            await target.click()
            return await _wait_reply(prev_text=prev_text, timeout=timeout)

        await send(text="/cancel", timeout=10)
        txt, _ = await send(text="/newemojipack")
        print("  > /newemojipack:", _short(txt))
        if not txt.strip():
            print("  ! @Stickers gave no reply — cannot proceed.")
            return False
        # Pick the STATIC emoji type via its inline button (EN/RU labels).
        txt, _ = await click(("static", "стат"), prev_text=txt)
        print("  > type=static:", _short(txt))
        if txt == "__nobtn__":
            print("  ! no 'static' button found on the type picker.")
            return False
        txt, _ = await send(text=title)
        print("  > title:", _short(txt))

        for i, (name, (emoji, _glyph)) in enumerate(EMOJI_MAP.items(), 1):
            r, _ = await send(file=str(OUT / f"{name}.webp"))
            if _looks_like_error(r):
                print(f"  ! upload {name} rejected: {r[:120]}")
                return False
            r, _ = await send(text=emoji)
            print(f"  [{i:>2}/{len(EMOJI_MAP)}] {name} {emoji}: {_short(r)[:48]}")
            if _looks_like_error(r):
                print(f"  ! emoji assign for {name} failed: {r[:120]}")
                return False

        txt, _ = await send(text="/publish")
        print("  > /publish:", _short(txt))
        # @Stickers may ask for a pack icon next; skip if offered.
        if "icon" in txt.lower() or "skip" in txt.lower():
            txt, _ = await send(text="/skip")
            print("  > skip icon:", _short(txt))
        final, _ = await send(text=short)
        print("  > short name:", _short(final))
        if _looks_like_error(final):
            print("  ! short name rejected (taken/invalid). Pick another --short.")
            return False

        # Verify: the only trustworthy success signal is that Telegram now
        # resolves the published set (the conversation replies can race).
        try:
            res = await client(GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=short), hash=0))
            n = len(res.documents)
        except Exception as exc:
            print(f"  ! verification failed — pack not resolvable: {type(exc).__name__}")
            return False
        if n < len(EMOJI_MAP):
            print(f"  ! pack resolved but only {n}/{len(EMOJI_MAP)} emoji — incomplete.")
            return False
        print(f"\nDONE. Pack verified ({n} emoji): https://t.me/addemoji/{short}")
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
