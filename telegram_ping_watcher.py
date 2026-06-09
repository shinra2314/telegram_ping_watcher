from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

try:
    from telethon import TelegramClient, events, types
    from telethon.errors import FloodWaitError
except ImportError:  # pragma: no cover
    TelegramClient = None
    events = None
    types = None

    class FloodWaitError(Exception):
        seconds = 0


# Text-mention ("name link") entities carry a user_id instead of an @username.
# Telegram uses these when a channel pings a user by their display name.
if types is not None:
    MENTION_NAME_ENTITY_TYPES = (types.MessageEntityMentionName, types.InputMessageEntityMentionName)
else:  # pragma: no cover - telethon not installed
    MENTION_NAME_ENTITY_TYPES = ()


DEFAULT_USERNAMES = (
    "alga_kazakhst2n",
    "w3v8f0rm",
    "Fjfjfjfjds",
    "Timofey02513",
    "MuverGT",
    "xdfusybau",
    "davifd23",
    "fsdfsdfdsg34",
    "sakmangg69",
)
BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from pulse_desk.config import get_settings
except Exception:  # pragma: no cover
    get_settings = None


def load_env_file(path: Path) -> None:
    if load_dotenv:
        load_dotenv(path, encoding="utf-8-sig")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_usernames(raw_usernames: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    usernames: list[str] = []
    for raw in raw_usernames:
        username = str(raw).strip().lstrip("@")
        if not username:
            continue
        lowered = username.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        usernames.append(username)
    if not usernames:
        raise ValueError("Список usernames пуст.")
    return usernames


def build_ping_regex(usernames: Iterable[str]) -> re.Pattern[str]:
    normalized = normalize_usernames(usernames)
    escaped = "|".join(re.escape(username) for username in normalized)
    return re.compile(rf"(?<![A-Za-z0-9_@])@({escaped})(?![A-Za-z0-9_])", re.IGNORECASE)


def display_name(entity) -> str:
    if entity is None:
        return "неизвестно"
    username = getattr(entity, "username", None)
    title = getattr(entity, "title", None)
    first_name = getattr(entity, "first_name", None)
    last_name = getattr(entity, "last_name", None)
    if title:
        return f"{title} (@{username})" if username else title
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if full_name and username:
        return f"{full_name} (@{username})"
    if full_name:
        return full_name
    if username:
        return f"@{username}"
    return str(getattr(entity, "id", "неизвестно"))


def chat_type_from_entity(entity) -> str:
    if entity is None or types is None:
        return "unknown"
    if isinstance(entity, types.User):
        return "private"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    return "unknown"


def message_looks_like_broadcast_channel(message) -> bool:
    if getattr(message, "is_group", False):
        return False
    return bool(getattr(message, "is_channel", False))


def build_message_link(chat, message) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message.id}"
    chat_id = str(getattr(message, "chat_id", "") or "")
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message.id}"
    return "нет публичной ссылки"


def extract_mentions(
    message,
    ping_regex: re.Pattern[str],
    usernames: Iterable[str],
    tracked_ids: dict[int, str] | None = None,
) -> list[str]:
    found: set[str] = set()
    text = getattr(message, "raw_text", "") or ""
    normalized_lookup = {username.lower(): username for username in normalize_usernames(usernames)}

    for match in ping_regex.finditer(text):
        username = normalized_lookup.get(match.group(1).lower(), match.group(1))
        found.add(f"@{username}")

    if types and getattr(message, "entities", None):
        for ent in message.entities:
            if isinstance(ent, types.MessageEntityMention):
                mention_text = text[ent.offset : ent.offset + ent.length].lstrip("@")
                username = normalized_lookup.get(mention_text.lower())
                if username:
                    found.add(f"@{username}")
            elif tracked_ids and MENTION_NAME_ENTITY_TYPES and isinstance(ent, MENTION_NAME_ENTITY_TYPES):
                username = tracked_ids.get(getattr(ent, "user_id", None))
                if username:
                    found.add(f"@{username}")

    return sorted(found, key=str.lower)


def local_iso_datetime(value) -> str:
    if not value:
        return ""
    try:
        return value.astimezone().isoformat(timespec="seconds")
    except Exception:
        return str(value)


async def message_to_record(client: TelegramClient, message, ping_regex, usernames, *, require_mentions: bool = True, tracked_ids: dict[int, str] | None = None) -> dict | None:
    mentions = extract_mentions(message, ping_regex, usernames, tracked_ids)
    if require_mentions and not mentions:
        return None
    try:
        chat = await message.get_chat()
    except Exception:
        chat = None
    try:
        sender = await message.get_sender()
    except Exception:
        sender = None
    return {
        "date": local_iso_datetime(getattr(message, "date", None)),
        "chat": display_name(chat),
        "chat_id": getattr(message, "chat_id", None),
        "sender": display_name(sender),
        "sender_id": getattr(message, "sender_id", None),
        "message_id": getattr(message, "id", None),
        "mentions": mentions,
        "link": build_message_link(chat, message),
        "text": getattr(message, "raw_text", "") or "",
    }


def record_to_json_line(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


async def run_cli(args) -> None:
    if TelegramClient is None:
        raise RuntimeError("Установите зависимости: pip install -r requirements.txt")
    load_env_file(BASE_DIR / ".env")
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("Укажите TELEGRAM_API_ID и TELEGRAM_API_HASH в .env")

    usernames = normalize_usernames(args.usernames or os.getenv("USERNAMES", ",".join(DEFAULT_USERNAMES)).split(","))
    ping_regex = build_ping_regex(usernames)
    session = os.getenv("TELEGRAM_SESSION", "telegram_ping_watcher")
    session_path = get_settings().session_path(session) if get_settings else BASE_DIR / session
    client = TelegramClient(str(session_path), int(api_id), api_hash)
    out_file = Path(args.out) if args.out else None

    async with client:
        if args.scan_history:
            async for message in client.iter_messages(None, search=" OR ".join(f"@{u}" for u in usernames), limit=args.history_limit):
                record = await message_to_record(client, message, ping_regex, usernames)
                if record:
                    line = record_to_json_line(record)
                    print(line)
                    if out_file:
                        out_file.open("a", encoding="utf-8").write(line + "\n")

        if args.no_live:
            return

        @client.on(events.NewMessage())
        async def handler(event):
            record = await message_to_record(client, event.message, ping_regex, usernames)
            if record:
                line = record_to_json_line(record)
                print(line)
                if out_file:
                    out_file.open("a", encoding="utf-8").write(line + "\n")

        print("Слушаю новые сообщения. Ctrl+C для остановки.")
        await client.run_until_disconnected()


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Telegram Ping Watcher")
    parser.add_argument("--scan-history", action="store_true", help="Просканировать доступную историю")
    parser.add_argument("--no-live", action="store_true", help="Не слушать новые сообщения после скана")
    parser.add_argument("--history-limit", type=int, default=1000, help="Лимит сообщений для исторического поиска")
    parser.add_argument("--out", help="Файл JSONL для сохранения результатов")
    parser.add_argument("--usernames", nargs="*", help="Usernames без @ или с @")
    return parser.parse_args(argv)


if __name__ == "__main__":
    asyncio.run(run_cli(parse_args()))
