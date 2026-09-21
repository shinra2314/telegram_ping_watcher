"""Telegram bot service: inline menus, slash commands, multi-user access keys."""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon import Button, events
from telethon.errors import FloodWaitError
# Re-exported for tests that build these errors through this module.
from telethon.errors import MessageIdInvalidError, MessageNotModifiedError  # noqa: F401

from .. import watch_settings as ws
from ..access_control import find_undoable, plan_undo
from ..app_ctx import ADMIN_ID, API_HASH, API_ID, BOT_TOKEN, LOG_FILE, logger, settings, state
from ..bot_membership import (
    access_decision as _shared_access_decision,
    admin_chat_ids as _shared_admin_chat_ids,
    resolve_member_access as _shared_resolve_member_access,
)
from ..bot_notify import BOT_ASSETS_DIR
from ..bot_permissions import dump_permissions, full_permissions, has_feature, parse_permissions
from ..bot_prefs import (
    next_hhmm_datetime, parse_duration_to_seconds, parse_member_prefs, parse_quiet_hours_input,
    parse_weekday_spec,
)
from ..bot_connection import watch_bot_connection
from ..common import flood_wait_seconds, record_app_event, start_supervised, tail_lines
from ..converter import USAGE_HINT, parse_query, supported_text
from ..roulette import checkin_moment
from .. import salary as salary_book_api
from ..security import generate_access_key
from ..telegram_accounts import restart_monitoring, telegram_client_for_session
from .views import DIV, fmt_dt, help_text
from .keyboards import (
    converter_keyboard, logs_keyboard, members_list_keyboard,
    section_nav,
)
from .cards import members_header
from .emoji import compile_emoji_map, resolve_custom_emoji_map
from .router import CallbackRouter, Click
from . import sections as bot_sections
from .sections import (
    analytics as analytics_section,
    converter as converter_section,
    feed as feed_section,
    giveaways as giveaways_section,
    home as home_section,
    keys as keys_section,
    market as market_section,
    members as members_section,
    prefs as prefs_section,
    roulette as roulette_section,
    salary as salary_section,
    scan as scan_section,
    settings as settings_section,
)
from .pending import consume as consume_pending, delete_quietly, pending_expired, take_pending
# Re-exported: the message-delivery helpers moved to reply.py so section
# modules can answer without importing this module back.
from .reply import (  # noqa: F401
    EDIT_FATAL_ERRORS, EDIT_GONE_ERRORS, MessageEditTimeExpiredError,
    MessageTooLongError, is_callback as _is_callback, respond_rich,
    safe_edit, tell as _tell,
)

# A handler slower than this is close to Telegram's ~15 s callback-query expiry,
# after which the answer silently fails and the user is left with a spinner.
SLOW_HANDLER_SECONDS = 3.0


# Fire-and-forget event writes from the bot-connection supervisor; kept alive
# here so the loop never drops a task mid-write.
_connection_event_tasks: set[asyncio.Task] = set()


def _event_label(event) -> str:
    """Short identifier of what the user pressed/typed, for logs and events.

    Callback data is the useful half (it names the exact button); a command is
    truncated because message text can be arbitrarily long and may be private.
    """
    data = getattr(event, "data", None)
    if data:
        try:
            return data.decode("utf-8")[:64]
        except Exception:
            return repr(data)[:64]
    text = getattr(getattr(event, "message", None), "text", None) or ""
    return str(text).splitlines()[0][:64] if text else ""


def safe(handler):
    """Wrap a handler so any failure surfaces feedback instead of a silent log."""
    name = getattr(handler, "__name__", "handler")

    async def inner(event):
        started = time.monotonic()
        state.bot_handler_calls += 1
        state.bot_last_update_at = datetime.now()
        try:
            await handler(event)
        except FloodWaitError as exc:
            state.bot_handler_errors += 1
            logger.warning("Bot handler %s hit a flood wait of %ss", name, exc.seconds)
            await _tell(event, f"⏳ Telegram просит подождать {flood_wait_seconds(exc.seconds)} с. Повторите позже.")
        except Exception as exc:
            state.bot_handler_errors += 1
            logger.exception("Bot handler failed: %s", name)
            await record_app_event(
                "ERROR", "bot", "Bot handler failed",
                {"handler": name, "error": str(exc), "data": _event_label(event)},
            )
            await _tell(event, "⚠️ Что-то пошло не так. Попробуйте ещё раз.")
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= SLOW_HANDLER_SECONDS:
                state.bot_handler_slow += 1
                # Past ~15 s the callback query expires and the answer is
                # silently dropped, so this is the early warning for it.
                logger.warning("Slow bot handler %s took %.1fs (%s)", name, elapsed, _event_label(event))
    inner.__name__ = name
    return inner


def _record_bot_connection(connected: bool, attempt: int) -> None:
    """Bot connection went up or down — remember it for ``/api/health``.

    Called by the ``bot-connection`` supervisor. While ``bot_offline_since`` is
    set the bot receives no updates at all, so the dashboard (and the external
    PulseWatchdog) can tell a wedged bot from a healthy one.
    """
    state.bot_offline_since = None if connected else (state.bot_offline_since or datetime.now())
    try:
        task = asyncio.get_running_loop().create_task(
            record_app_event(
                "INFO" if connected else "WARNING",
                "telegram",
                "Telegram bot connection restored" if connected else "Telegram bot connection lost",
                {"attempts": attempt},
            )
        )
    except RuntimeError:  # no running loop (unit tests)
        return
    _connection_event_tasks.add(task)
    task.add_done_callback(_connection_event_tasks.discard)


async def init_bot() -> None:
    from database import (
        create_access_window, create_bot_key, get_access_audit, get_bot_key_by_secret, get_bot_member,
        get_recent_giveaway_actions, list_bot_members, record_access_audit,
        set_member_default_policy, upsert_bot_member,
    )

    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.info("Telegram bot is not configured.")
        return
    state.bot_init_failed = False
    bot_client = None
    try:
        bot_client = telegram_client_for_session("pulse_bot")
        await bot_client.start(bot_token=BOT_TOKEN)
        bot_me = await bot_client.get_me()
        # Published only once the client is really usable: publishing it before
        # start() made /api/health report a configured bot for a client that had
        # never authenticated and had no handlers registered.
        state.bot_client = bot_client
        state.bot_id = bot_me.id
        state.bot_username = bot_me.username
        bot_username = bot_me.username
        logger.info("Bot started: @%s", bot_me.username)
        state.bot_offline_since = None

        # Telethon stops reconnecting after `connection_retries` failures and
        # leaves the client dead. Outgoing sends recover on their own
        # (ensure_bot_connected), incoming updates do not — so without this
        # supervisor a network flap silently freezes every command and button
        # until the next notification reconnects us. See bot_connection.py.
        # Supervised, not fire-and-forget: this loop is what keeps *incoming*
        # updates flowing, so if it ever dies the bot silently reverts to the
        # exact failure it exists to prevent.
        start_supervised(
            "bot-connection",
            lambda: watch_bot_connection(
                bot_client,
                should_stop=lambda: state.shutting_down,
                on_change=_record_bot_connection,
                on_alive=lambda: state.heartbeat("bot-connection"),
                logger=logger,
            ),
        )

        # Resolve the Aperture custom-emoji pack (best-effort; empty → plain emoji).
        if settings.bot_custom_emoji_set:
            state.custom_emoji_map = await resolve_custom_emoji_map(bot_client, settings.bot_custom_emoji_set)
            state.custom_emoji_index = compile_emoji_map(state.custom_emoji_map)
            # The map carries a VS16 variant per emoticon, so its key count is
            # ~2x the pack size — report distinct document ids as the glyph count.
            glyphs = len(set(state.custom_emoji_map.values()))
            logger.info("Custom emoji pack '%s': %d glyphs resolved (%d match keys)",
                        settings.bot_custom_emoji_set, glyphs, len(state.custom_emoji_map))


        # ---- callback routing ------------------------------------------------
        # Sections register the callbacks they own; `callback_handler` asks the
        # router first and only then falls through to the legacy if-chain.
        callbacks = CallbackRouter()
        bot_sections.register_all(callbacks)

        # ---- access control -------------------------------------------------
        # The rules themselves live in bot_membership so the Mini App resolves
        # access identically; these stay as thin closures because ~40 call sites
        # below reference them by name.
        def _bot_admin_chat_ids() -> set[int]:
            return _shared_admin_chat_ids(str(ADMIN_ID or ""), settings.bot_admin_chats or "")

        async def _access_decision(sender_id: int, member: dict):
            return await _shared_access_decision(sender_id, member)

        def _until_phrase(until: Optional[datetime]) -> str:
            if not until:
                return ""
            return f" до {until.astimezone().strftime('%d.%m %H:%M')}"

        async def resolve_member_access(sender_id: int) -> tuple[Optional[str], dict]:
            """Resolve a Telegram user to (role, grants) — see bot_membership."""
            return await _shared_resolve_member_access(
                sender_id, admin_ids=_bot_admin_chat_ids()
            )

        async def bot_role(sender_id: int) -> Optional[str]:
            role, _perms = await resolve_member_access(sender_id)
            return role

        async def access_block_notice(sender_id: int) -> Optional[str]:
            """User-facing message if a known member is currently closed by schedule."""
            if sender_id in _bot_admin_chat_ids():
                return None
            member = await get_bot_member(sender_id)
            if not member or member.get("blocked"):
                return None
            allowed, _reason, until = await _access_decision(sender_id, member)
            if allowed:
                return None
            return (
                "⏰ **Доступ к боту сейчас закрыт по расписанию.**\n"
                f"Откроется автоматически{_until_phrase(until)}."
            )

        async def deny_non_admin(event) -> bool:
            """True if the sender must be blocked from an owner-only action."""
            role = await bot_role(event.sender_id)
            if role is None:
                return True  # stranger — ignore silently
            if role != "admin":
                await event.respond("⛔ **Только для владельца.**\nВ режиме просмотра это действие недоступно.")
                return True
            return False

        FEATURE_DENIED = "🔒 **Раздел закрыт.**\nВладелец не открыл его для вашего ключа."

        def viewer_only(feature: Optional[str] = None):
            """Gate a handler to any authenticated role, optionally to one feature.

            The wrapped handler is called as `handler(event, role, perms)`.
            """
            def decorator(handler):
                async def inner(event):
                    role, perms = await resolve_member_access(event.sender_id)
                    if role is None:
                        notice = await access_block_notice(event.sender_id)
                        if notice:
                            await event.respond(notice)
                        return
                    if feature and role != "admin" and not has_feature(perms, feature):
                        await event.respond(FEATURE_DENIED, buttons=await home_section.menu_buttons(event.sender_id, role, perms))
                        return
                    await handler(event, role, perms)
                inner.__name__ = getattr(handler, "__name__", "inner")
                return safe(inner)
            return decorator

        def _cb_id(data: str) -> Optional[int]:
            """Parse the trailing int from a callback like `fav_42`; None if malformed."""
            try:
                return int(data.split("_", 1)[1])
            except (ValueError, IndexError):
                return None

        # ---- shared renderers (reused by slash commands and menu callbacks) -
        # ---- redeem / onboarding -------------------------------------------
        async def grant_access(event, key: dict) -> None:
            existing = await get_bot_member(event.sender_id)
            if existing and existing.get("blocked"):
                await event.respond("🚫 **Доступ отключён владельцем.**")
                return
            is_new_member = existing is None
            sender = await event.get_sender()
            uname = getattr(sender, "username", "") or ""
            name = " ".join(filter(None, [getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or ""])).strip()
            role = key.get("role") or "viewer"
            perms = parse_permissions(key.get("permissions"))
            # Grants are snapshotted onto the member so editing or revoking the
            # key later never leaves an onboarded guest with a broken menu.
            await upsert_bot_member(event.sender_id, uname, name, key.get("id"), role, dump_permissions(perms))
            greeting = f"Привет, {name}!" if name else "Привет!"
            if role == "admin":
                access_line = "👑 Доступ: __полный__"
            elif role == "premium":
                access_line = "⚡ Доступ: __премиум — уведомления мгновенно, без задержки модерации__"
            else:
                access_line = "👁 Доступ: __только просмотр__"
            await event.respond(
                "✅ **Доступ открыт!**\n"
                f"{greeting} Добро пожаловать в **Pulse Desk**.\n"
                + f"{DIV}\n"
                + access_line
                + "\n\nВыберите раздел 👇",
                buttons=await home_section.menu_buttons(event.sender_id, role, perms),
            )
            if is_new_member:
                # The owner hears about every new person, and a one-time invite
                # says it has now been used up.
                once = int(key.get("max_uses") or 0) == 1
                who = f"@{uname}" if uname else (name or str(event.sender_id))
                note = "\n🎟 __Одноразовый ключ использован — ссылка больше не откроет доступ.__" if once else ""
                from ..bot_notify import send_admin_bot_message

                await send_admin_bot_message(
                    f"🔑 **Новый участник:** {who}\nКлюч #{key.get('id')} · {key.get('label') or 'без метки'}{note}",
                    kind="system",
                )
            if is_new_member and role != "admin":
                # First-time onboarding: let the member tune notifications right away.
                prefs = parse_member_prefs(None)
                text, buttons = prefs_section.menu(prefs, perms)
                await event.respond(
                    "⚙️ **Настройте уведомления под себя**\n"
                    f"{DIV}\n"
                    "Отметьте, что присылать, — можно поменять в любой момент через /settings.\n\n"
                    + text,
                    buttons=buttons,
                )

        locked_text = (
            "🔒 **Доступ закрыт**\n"
            f"{DIV}\n"
            "Пришлите ключ доступа, выданный владельцем, "
            "или откройте ссылку-приглашение."
        )

        @bot_client.on(events.NewMessage(pattern=r"/start(?:\s+(\S+))?"))
        @safe
        async def start_handler(event):
            payload = (event.pattern_match.group(1) or "").strip()
            if payload:
                key = await get_bot_key_by_secret(payload, event.sender_id)
                if key:
                    await grant_access(event, key)
                    return
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            # Grants too, not just the role: a menu drawn without them shows a
            # guest every section and then refuses the ones the key never opened.
            role, perms = await resolve_member_access(event.sender_id)
            if role is None:
                await event.respond(await access_block_notice(event.sender_id) or locked_text)
                return
            welcome_banner = BOT_ASSETS_DIR / "welcome.png"
            home = await home_section.render_home(role, perms)
            buttons = await home_section.menu_buttons(event.sender_id, role, perms)
            if welcome_banner.exists():
                try:
                    await respond_rich(event, home, buttons=buttons, file=str(welcome_banner))
                    return
                except Exception:
                    logger.warning("Failed to send welcome banner, falling back to text", exc_info=True)
            await respond_rich(event, home, buttons=buttons)

        @bot_client.on(events.NewMessage(pattern=r"/redeem(?:\s+(\S+))?"))
        @safe
        async def redeem_handler(event):
            payload = (event.pattern_match.group(1) or "").strip()
            if not payload:
                await event.respond("Использование: `/redeem <ключ>`")
                return
            key = await get_bot_key_by_secret(payload, event.sender_id)
            if not key:
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            await grant_access(event, key)

        @bot_client.on(events.NewMessage(pattern="/menu"))
        @safe
        async def menu_handler(event):
            role, perms = await resolve_member_access(event.sender_id)
            if role is None:
                await event.respond(await access_block_notice(event.sender_id) or locked_text)
                return
            home = await home_section.render_home(role, perms)
            buttons = await home_section.menu_buttons(event.sender_id, role, perms)
            banner = BOT_ASSETS_DIR / "welcome.png"
            if banner.exists():
                try:
                    await respond_rich(event, home, buttons=buttons, file=str(banner))
                    return
                except Exception:
                    logger.warning("menu banner failed, text fallback", exc_info=True)
            await respond_rich(event, home, buttons=buttons)

        @bot_client.on(events.NewMessage(pattern="/settings"))
        @viewer_only()
        async def settings_handler(event, role, perms):
            if role == "admin":
                text, buttons = settings_section.root_menu()
                await event.respond(text, buttons=buttons)
                return
            member = await get_bot_member(event.sender_id)
            if not member:
                await event.respond("Личные настройки недоступны.")
                return
            text, buttons = prefs_section.menu(parse_member_prefs(member.get("notification_prefs")), perms)
            await event.respond(text, buttons=buttons)

        @bot_client.on(events.NewMessage(pattern="/help"))
        @viewer_only()
        async def help_handler(event, role, perms):
            await event.respond(help_text(role, perms, salary=await salary_section.visible(event.sender_id, role)), buttons=section_nav(b"menu_help"))

        @bot_client.on(events.NewMessage(pattern="/stats"))
        @viewer_only("stats")
        async def stats_handler(event, role, perms):
            text = await analytics_section.render_stats(perms, role == "admin")
            await event.respond(text, buttons=section_nav(b"menu_stats"))

        @bot_client.on(events.NewMessage(pattern="/analytics"))
        @viewer_only("analytics")
        async def analytics_handler(event, role, perms):
            text, kb = await analytics_section.render_report("sum", perms, role == "admin")
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern=r"/salary(?:\s+(\S+))?"))
        @viewer_only()
        async def salary_handler(event, role, perms):
            # No reply at all for a key without a matching label: the command
            # must look like it does not exist, not like a locked door.
            if not await salary_section.visible(event.sender_id, role):
                return
            month = salary_book_api.parse_month_arg(event.pattern_match.group(1) or "")
            text, kb = await salary_section.render(event.sender_id, role, month)
            if text is None:
                return
            await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern="/status"))
        @viewer_only("status")
        async def status_handler(event, role, perms):
            text = await analytics_section.render_status(perms, role == "admin")
            await event.respond(text, buttons=section_nav(b"menu_status"))

        @bot_client.on(events.NewMessage(pattern="/giveaways"))
        @viewer_only("giveaways")
        async def giveaways_handler(event, role, perms):
            text, kb = await giveaways_section.render_feed(
                perms=perms, is_admin=role == "admin")
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/recent"))
        @viewer_only("recent")
        async def recent_handler(event, role, perms):
            text, kb = await feed_section.render(perms=perms, is_admin=role == "admin")
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/latest"))
        @viewer_only("recent")
        async def latest_handler(event, role, perms):
            text, kb = await feed_section.render(perms=perms, is_admin=role == "admin")
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/search"))
        @viewer_only("search")
        async def search_handler(event, role, perms):
            parts = event.message.text.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                await event.respond("Укажите текст: `/search TON`")
                return
            # Тот же путь, что у кнопки 🔎: запрос запоминается, и лента дальше
            # листается, фильтруется и выгружается вместе с ним.
            text, kb = await feed_section.open_search(
                event.sender_id, parts[1], perms, is_admin=role == "admin")
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/market"))
        @viewer_only("market")
        async def market_handler(event, role, perms):
            text, kb, image = await market_section.render()
            await event.respond(text, buttons=kb, file=image)

        @bot_client.on(events.NewMessage(pattern=r"/conv(?:ert)?(?:\s+(.+))?"))
        @viewer_only("market")
        async def convert_handler(event, role, perms):
            raw = (event.pattern_match.group(1) or "").strip()
            if not raw:
                text, kb = await converter_section.render_home()
                await event.respond(text, buttons=kb)
                return
            query = parse_query(raw)
            if query is None:
                await event.respond(
                    f"❌ **Не понял запрос.**\n{USAGE_HINT}\n{DIV}\n{supported_text()}",
                    buttons=converter_keyboard(),
                )
                return
            text, kb = await converter_section.render_result(query)
            await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern="/ping"))
        @viewer_only()
        async def ping_handler(event, role, perms):
            await event.respond("🏓 **Понг!** Бот на связи.")

        @bot_client.on(events.NewMessage(pattern="/logs"))
        @safe
        async def logs_handler(event):
            if await deny_non_admin(event):
                return
            if not LOG_FILE.exists():
                await event.respond("Логов пока нет.")
                return
            lines = await asyncio.to_thread(tail_lines, LOG_FILE, 20)
            await event.respond(
                "📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-3500:] + "\n```",
                buttons=logs_keyboard(),
            )

        @bot_client.on(events.NewMessage(pattern=r"/export(?:\s+(\S+))?"))
        @safe
        async def export_bot_handler(event):
            if await deny_non_admin(event):
                return
            # `/export json` for JSON; anything else is CSV. Filtered exports
            # live on the feed screen's ⬇️ buttons.
            fmt = "j" if (event.pattern_match.group(1) or "").lower().startswith("j") else "c"
            file, count = await feed_section.export_all(fmt)
            if file is None:
                await event.respond("📭 Выгружать пока нечего.")
                return
            await event.respond(
                f"📤 **Экспорт** · {count} записей\n__С фильтрами — кнопкой ⬇️ в ленте.__",
                file=file,
            )

        @bot_client.on(events.NewMessage(pattern=r"/report(?:\s+(\S+))?"))
        @safe
        async def report_handler(event):
            if await deny_non_admin(event):
                return
            from .sections import report as report_section

            arg = (event.pattern_match.group(1) or "").lower()
            code = "m" if arg.startswith(("m", "мес")) else "w"
            caption, card = await report_section.render(*report_section.VIEWS[code])
            await event.respond(caption, file=card, buttons=report_section.keyboard(code))

        @bot_client.on(events.NewMessage(pattern=r"/win(?:\s+(.+))?"))
        @safe
        async def win_handler(event):
            if await deny_non_admin(event):
                return
            parts = (event.pattern_match.group(1) or "").split()
            if not parts:
                await event.respond(
                    "🏆 **Добавить победу вручную**\n" + DIV + "\n"
                    "`/win <ссылка на пост> [@аккаунт]`\n\n"
                    "Для побед, которые бот не поймал: итоги картинкой, ночь с выключенным ПК, "
                    "результат в личке. Аккаунт можно не указывать, если он упомянут в посте.",
                    link_preview=False,
                )
                return
            from ..manual_win import record_manual_win

            result = await record_manual_win(parts[0], parts[1:])
            buttons = [[Button.inline("💰 К долгам", b"menu_debts")]] if result.ok else None
            await event.respond(result.message, buttons=buttons, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/scan"))
        @safe
        async def scan_handler(event):
            if await deny_non_admin(event):
                return
            started = scan_section.start_if_idle()
            note = "🔄 **Сканирование запущено.**" if started else "⏳ Сканирование уже идёт."
            text, kb = scan_section.render_panel()
            await event.respond(f"{note}\n\n{text}", buttons=kb)

        @bot_client.on(events.NewMessage(pattern="/restart"))
        @safe
        async def restart_handler(event):
            if await deny_non_admin(event):
                return
            result = await restart_monitoring()
            await event.respond(f"♻️ **Мониторинг перезапущен.**\nПереподключаю аккаунтов: `{result.get('restarted', 0)}`")

        @bot_client.on(events.NewMessage(pattern=r"/newkey(?:\s+(.+))?"))
        @safe
        async def newkey_handler(event):
            if await deny_non_admin(event):
                return
            label = (event.pattern_match.group(1) or "").strip()
            secret = generate_access_key()
            key = await create_bot_key(label, secret, "viewer", None, dump_permissions(full_permissions()))
            link = f"https://t.me/{bot_username}?start={secret}" if bot_username else ""
            body = (
                "🔑 **Новый ключ создан**\n"
                f"{DIV}\n"
                f"🏷 Метка: `{label or '—'}`\n"
                f"👁 Доступ: __только просмотр__\n\n"
                f"🔐 Ключ:\n`{secret}`\n"
            )
            if link:
                body += f"\n🔗 **Ссылка-приглашение:**\n{link}\n\n_Отправьте её человеку — он откроет бота и получит доступ._"
            else:
                body += "\n_Бот не настроен на ссылки — передайте ключ вручную через_ `/redeem`."
            await event.respond(body, link_preview=False)
            screen = await keys_section.render_panel(int(key["id"]))
            if screen:
                text, kb = screen
                await event.respond("⚙️ **Настройте ключ перед отправкой**\n\n" + text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern=r"/invite(?:\s+(.+))?"))
        @safe
        async def invite_handler(event):
            """One-time invite: a viewer key that closes after its first person."""
            if await deny_non_admin(event):
                return
            from database import set_bot_key_max_uses

            label = (event.pattern_match.group(1) or "").strip() or "приглашение"
            secret = generate_access_key()
            key = await create_bot_key(label[:40], secret, "viewer", None, dump_permissions(full_permissions()))
            await set_bot_key_max_uses(int(key["id"]), 1)
            link = f"https://t.me/{bot_username}?start={secret}" if bot_username else f"`/redeem {secret}`"
            await event.respond(
                "🎟 **Одноразовое приглашение**\n" + DIV + "\n"
                f"🏷 {label}\n🔗 {link}\n\n"
                "__Сработает для одного человека — потом ссылка закроется. Права — в панели ключа.__",
                link_preview=False,
            )
            screen = await keys_section.render_panel(int(key["id"]))
            if screen:
                text, kb = screen
                await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern="/keys"))
        @safe
        async def keys_handler(event):
            if await deny_non_admin(event):
                return
            text, kb = await keys_section.render_list()
            await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern=r"/members(?:\s+(.+))?"))
        @safe
        async def members_handler(event):
            if await deny_non_admin(event):
                return
            query = (event.pattern_match.group(1) or "").strip().lstrip("@").lower()
            members = await list_bot_members()
            if query:
                members = [
                    m for m in members
                    if query in (m.get("tg_username") or "").lower()
                    or query in (m.get("name") or "").lower()
                    or query == str(m.get("tg_id"))
                ]
            items = []
            for m in members:
                tg = int(m["tg_id"])
                uname = f"@{m['tg_username']}" if m.get("tg_username") else ""
                dot_ = "🚫" if m.get("blocked") else "🟢"
                label = f"{dot_} {m.get('name') or tg} {uname}".strip()
                items.append((tg, label[:48]))
            text = members_header(len(members))
            if query:
                text += f"\n🔎 Фильтр: «{query}»"
            await event.respond(text, buttons=members_list_keyboard(items))

        # ---- scheduled access management (owner only) ----------------------
        @bot_client.on(events.NewMessage(pattern=r"/access(?:\s+(.+))?"))
        @safe
        async def access_handler(event):
            if await deny_non_admin(event):
                return
            raw = (event.pattern_match.group(1) or "").strip()
            if not raw:
                await event.respond(await members_section.render_access_overview(), buttons=await home_section.menu_buttons(event.sender_id, "admin"))
                return
            parts = raw.split()
            member = await members_section.resolve_member_arg(parts[0])
            if not member:
                await event.respond(f"❌ Не нашёл участника «{parts[0]}». Список: /members")
                return
            tg = int(member["tg_id"])
            state.access_cache.pop(tg, None)
            args = parts[1:]
            sub = args[0].lower() if args else "show"

            if sub in ("show", "rules", "status"):
                await event.respond(await members_section.render_member_access(member))
                return
            if sub == "on":
                cancelled = await members_section.open_member_access(tg, event.sender_id)
                await event.respond(f"🟢 Доступ открыт. Снято ограничений: {cancelled}\n\n" + await members_section.render_member_access(member))
                return
            if sub == "off":
                until_iso, human = None, "бессрочно"
                if len(args) >= 2:
                    dur = parse_duration_to_seconds(args[1])
                    if dur:
                        until = datetime.now(timezone.utc) + timedelta(seconds=dur)
                        until_iso, human = until.replace(microsecond=0, tzinfo=None).isoformat(), f"на {args[1]}"
                    else:
                        target_local = next_hhmm_datetime(datetime.now().astimezone(), args[1])
                        if not target_local:
                            await event.respond("❌ Формат: `/access <user> off [18:00|2h]`")
                            return
                        until_iso = target_local.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
                        human = f"до {target_local.strftime('%d.%m %H:%M')}"
                await members_section.close_member_access(tg, until_iso, event.sender_id)
                await event.respond(f"🔴 Доступ закрыт ({human}).\n\n" + await members_section.render_member_access(member))
                return
            if sub == "cron":
                # /access user cron "<expr>" <dur_min> [TZ]
                m = re.search(r'"([^"]+)"|\'([^\']+)\'', raw)
                expr = (m.group(1) or m.group(2)) if m else None
                tail = raw[m.end():].split() if m else []
                if not expr or not tail or not tail[0].isdigit():
                    await event.respond('❌ Формат: `/access <user> cron "0 9 * * 1-5" 540 [TZ]`')
                    return
                tz = tail[1] if len(tail) >= 2 else (member.get("timezone") or "UTC")
                repeat = {"type": "cron", "expr": expr, "dur_min": int(tail[0])}
                await set_member_default_policy(tg, "deny")
                row = await create_access_window(tg, enabled=True, repeat_rule=repeat, timezone=tz, priority=200, label="cron", created_by=event.sender_id)
                await record_access_audit(tg, int(row["id"]), "create", f"admin:{event.sender_id}", None, repeat)
                state.access_cache.pop(tg, None)
                await event.respond(f"✅ Cron-окно #{row['id']} добавлено (tz=`{tz}`).\n\n" + await members_section.render_member_access(member))
                return
            if sub in ("work", "mute"):
                if len(args) < 2 or not parse_quiet_hours_input(args[1]):
                    await event.respond("❌ Формат: `/access <user> work 09:00-18:00 mon-fri [TZ]`")
                    return
                frm, to = parse_quiet_hours_input(args[1])
                days = parse_weekday_spec(args[2]) if len(args) >= 3 else []
                tz = args[3] if len(args) >= 4 else (member.get("timezone") or "UTC")
                row = await members_section.add_access_window(tg, sub, frm, to, days, tz, event.sender_id)
                await event.respond(f"✅ Окно #{row['id']} добавлено (tz=`{tz}`).\n\n" + await members_section.render_member_access(member))
                return
            if sub == "del":
                if len(args) < 2 or not args[1].isdigit():
                    await event.respond("❌ Формат: `/access <user> del <id>`")
                    return
                await members_section.remove_access_window(tg, int(args[1]), event.sender_id)
                await event.respond(f"🗑 Окно #{args[1]} удалено.\n\n" + await members_section.render_member_access(member))
                return
            if sub == "undo":
                target = find_undoable(await get_access_audit(tg, limit=50))
                if not target:
                    await event.respond("↩️ Нечего отменять.")
                    return
                plan = plan_undo(target)
                await members_section.apply_undo(tg, plan)
                await record_access_audit(
                    tg, target.get("schedule_id"), "undo", f"admin:{event.sender_id}",
                    None, {"undone_audit_id": int(target["id"]), "plan": plan},
                )
                state.access_cache.pop(tg, None)
                await event.respond(f"↩️ Отменено: {target['action']} (запись #{target['id']}).\n\n" + await members_section.render_member_access(member))
                return
            if sub in ("log", "audit"):
                log = await get_access_audit(tg)
                if not log:
                    await event.respond("📭 История доступа пуста.")
                    return
                lines = [f"🧾 **История доступа** · {member.get('name') or tg}", DIV]
                lines += [f"`{fmt_dt(a['created_at'])}` · {a['action']} · _{a['actor']}_" for a in log]
                await event.respond("\n".join(lines))
                return
            await event.respond("❓ Подкоманды: `show · on · off [18:00|2h] · work <range> <days> [TZ] · mute <range> [TZ] · cron \"<expr>\" <min> · del <id> · undo · log`")

        @bot_client.on(events.NewMessage(pattern="/actions"))
        @safe
        async def actions_handler(event):
            if await deny_non_admin(event):
                return
            actions = await get_recent_giveaway_actions(limit=15)
            if not actions:
                await event.respond("🧾 **Действия по розыгрышам**\n" + DIV + "\n📭 __Пока ничего.__", buttons=await home_section.menu_buttons(event.sender_id, "admin"))
                return
            icon = {"join": "✅", "confirm": "✅", "skip": "⏭", "analyze": "🔍"}
            lines = ["🧾 **Действия по розыгрышам**", DIV]
            for a in actions:
                mark = icon.get(a.get("action"), "•")
                chat = a.get("chat") or f"ping #{a.get('ping_id') or '?'}"
                lines.append(
                    f"{mark} `{fmt_dt(a.get('created_at'))}` · {a.get('action')} → {a.get('status')}\n"
                    f"   {chat} · _{a.get('actor') or '—'}_"
                )
            await event.respond("\n".join(lines), buttons=await home_section.menu_buttons(event.sender_id, "admin"))

        @bot_client.on(events.NewMessage(pattern=r"/roulette(?:\s+(.+))?"))
        @safe
        async def roulette_handler(event):
            if await deny_non_admin(event):
                return
            raw = (event.pattern_match.group(1) or "").strip()
            if raw:
                moment = checkin_moment(datetime.now(), raw)
                if moment is None:
                    await event.respond("❌ Формат: `/roulette 21:47`.")
                    return
                await roulette_section.checkin(event, moment)
                return
            text, kb = await roulette_section.render_panel()
            await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(func=lambda e: bool(e.is_private and e.message and e.message.text and not e.message.text.startswith("/"))))
        @safe
        async def freeform_handler(event):
            role, perms = await resolve_member_access(event.sender_id)
            if role is not None:
                # Members' free text only feeds pending settings inputs; the owner
                # may also answer a roulette nudge with a bare time.
                pending = take_pending(event.sender_id)
                if pending:
                    if pending_expired(pending):
                        await delete_quietly(pending.get("chat_id"), pending.get("cleanup") or [])
                        await event.respond(
                            "⌛ Время ввода истекло — поле сброшено.\nОткройте меню заново: /menu",
                            buttons=await home_section.menu_buttons(event.sender_id, role, perms),
                        )
                    else:
                        await consume_pending(event, role, pending)
                    return
                # A bare `21:47` answers the roulette nudge, but only while one
                # is actually waiting — otherwise a message that merely looks
                # like a time would be swallowed here.
                if role == "admin":
                    cfg = await ws.load_roulette_settings()
                    if cfg.get("awaiting"):
                        moment = checkin_moment(datetime.now(), event.message.text or "")
                        if moment is not None:
                            await roulette_section.checkin(event, moment)
                return
            # A known member closed by schedule gets the reason, not the locked banner.
            notice = await access_block_notice(event.sender_id)
            if notice:
                await event.respond(notice)
                return
            # Treat a bare message as a possible access key for non-members.
            key = await get_bot_key_by_secret((event.message.text or "").strip(), event.sender_id)
            if key:
                await grant_access(event, key)
                return
            await event.respond(locked_text)

        @bot_client.on(events.CallbackQuery())
        @safe
        async def callback_handler(event):
            """Every button in the bot lands here and is routed by `callbacks`."""
            data = event.data.decode("utf-8")
            role, perms = await resolve_member_access(event.sender_id)
            if role is None:
                await event.answer("Доступ запрещён", alert=True)
                return

            if await callbacks.dispatch(Click(event, data, role, perms, has_feature)):
                return
            # Nothing matched. Without this the handler returned having never
            # answered the query, and the button spun until the client gave up —
            # the normal fate of any button from an older deploy.
            logger.info("Unhandled bot callback: %s", data[:64])
            await event.answer("Кнопка устарела — откройте меню заново.", alert=True)

        # ---- register Telegram command menus (best-effort) ------------------
        try:
            from telethon.tl.functions.bots import SetBotCommandsRequest
            from telethon.tl.types import BotCommand, BotCommandScopeDefault, BotCommandScopePeer
            viewer_cmds = [
                BotCommand("menu", "Главное меню"),
                BotCommand("stats", "Статистика"),
                BotCommand("status", "Состояние аккаунтов"),
                BotCommand("giveaways", "Розыгрыши"),
                BotCommand("recent", "Последние упоминания"),
                BotCommand("latest", "Последние 5"),
                BotCommand("search", "Поиск"),
                BotCommand("market", "Курсы"),
                BotCommand("convert", "Конвертер валют и крипты"),
                BotCommand("settings", "Настройки и уведомления"),
                BotCommand("ping", "Проверка связи"),
            ]
            await bot_client(SetBotCommandsRequest(scope=BotCommandScopeDefault(), lang_code="", commands=viewer_cmds))
            if ADMIN_ID:
                admin_cmds = viewer_cmds + [
                    BotCommand("scan", "Скан истории"),
                    BotCommand("logs", "Логи"),
                    BotCommand("export", "CSV выгрузка"),
                    BotCommand("newkey", "Создать ключ"),
                    BotCommand("keys", "Ключи доступа"),
                    BotCommand("invite", "Одноразовое приглашение"),
                    BotCommand("members", "Пользователи"),
                    BotCommand("access", "Доступ по расписанию"),
                    BotCommand("actions", "История действий по розыгрышам"),
                    BotCommand("win", "Добавить победу вручную"),
                    BotCommand("report", "Отчёт за неделю / месяц"),
                    BotCommand("roulette", "Рулетка йобо"),
                    # Только в списке владельца: в общем меню команда выдала бы
                    # существование раздела зарплат ключам без метки.
                    BotCommand("salary", "Зарплаты за месяц"),
                    BotCommand("restart", "Перезапустить мониторинг"),
                ]
                await bot_client(SetBotCommandsRequest(
                    scope=BotCommandScopePeer(peer=await bot_client.get_input_entity(int(ADMIN_ID))),
                    lang_code="",
                    commands=admin_cmds,
                ))
        except Exception:
            logger.debug("Could not set bot command menu", exc_info=True)
    except Exception as exc:
        # The try above spans the whole of handler registration, so a failure
        # anywhere in it leaves a running app with a bot that answers nothing.
        # Say so loudly instead of logging once and carrying on.
        state.bot_init_failed = True
        logger.exception("Bot startup failed")
        await record_app_event("ERROR", "telegram", "Bot startup failed", {"error": str(exc)})
        if bot_client is not None and state.bot_client is not bot_client:
            # Never published: close it, or the retry's client would fight it
            # for the pulse_bot session file.
            with contextlib.suppress(Exception):
                await bot_client.disconnect()
            state.bot_offline_since = state.bot_offline_since or datetime.now()


def bot_start_needs_retry() -> bool:
    """The first ``init_bot`` failed before the client came up."""
    return bool(BOT_TOKEN) and state.bot_client is None and state.bot_init_failed


async def retry_bot_start() -> None:
    """Keep starting the bot after a failed first try (``bot-start-retry`` task).

    Pings found meanwhile are stored with their card owed, so once the bot is up
    ``notify-retry`` delivers them; the owner is told how long it was down.
    """
    from database import count_owed_ping_notifications

    from ..bot_connection import keep_starting_bot
    from ..bot_notify import send_admin_bot_message

    if not bot_start_needs_retry():
        return
    down_since = state.bot_offline_since or datetime.now()

    async def announce(attempts: int) -> None:
        minutes = max(1, int((datetime.now() - down_since).total_seconds() // 60))
        owed = await count_owed_ping_notifications()
        text = f"🤖 Бот снова на связи: не мог запуститься с {down_since:%H:%M} ({minutes} мин, попыток: {attempts})."
        if owed:
            text += f"\nДосылаю пропущенные уведомления: {owed}."
        await record_app_event("INFO", "telegram", "Bot started after retries",
                               {"attempts": attempts, "down_minutes": minutes, "owed_cards": owed})
        await send_admin_bot_message(text)

    await keep_starting_bot(
        init_bot,
        is_started=lambda: state.bot_client is not None,
        should_stop=lambda: state.shutting_down,
        on_started=announce,
        logger=logger,
    )
