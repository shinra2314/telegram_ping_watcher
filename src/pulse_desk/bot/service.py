"""Telegram bot service: inline menus, slash commands, multi-user access keys."""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from telegram_ping_watcher import normalize_usernames

from .. import APP_VERSION
from .. import watch_settings as ws
from ..access_control import find_undoable, parse_repeat_rule, plan_undo, resolve_access, window_from_row
from ..analytics import build_analytics
from ..app_ctx import ADMIN_ID, API_HASH, API_ID, BOT_TOKEN, CHECK_FRESH_MINUTES, LOG_FILE, logger, settings, state
from ..bot_notify import BOT_ASSETS_DIR
from ..bot_prefs import (
    KEYWORD_SCOPES,
    next_hhmm_datetime,
    parse_duration_to_seconds,
    parse_hhmm,
    parse_member_prefs,
    parse_quiet_hours_input,
    parse_weekday_spec,
    render_keyword_list_text,
    render_member_prefs_text,
    render_notification_settings_text,
    render_tracking_text,
    toggle_member_pref,
)
from ..common import record_app_event
from ..giveaway_actions import confirm_safe_giveaway_join
from ..live import publish_live_event
from ..scan_engine import full_history_scan
from ..security import generate_access_key
from ..telegram_accounts import restart_monitoring, telegram_client_for_session
from .views import DIV, fmt_dt, help_text, main_menu_buttons
from .keyboards import back_home, section_nav
from .cards import home_card, summary_card


_ACCESS_DAY_NAMES = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}


async def init_bot() -> None:
    import database
    from database import (
        create_access_window,
        create_bot_key,
        create_disable_until_window,
        deactivate_access_window,
        delete_broadcast_messages,
        get_bot_key_by_secret,
        get_bot_member,
        get_broadcast_messages,
        get_access_audit,
        list_all_access_windows,
        get_giveaway_board,
        get_market_history,
        get_pings,
        get_recent_giveaway_actions,
        list_access_windows,
        list_bot_keys,
        list_bot_members,
        mark_ping_read as mark_ping_read_db,
        record_access_audit,
        record_giveaway_action,
        revoke_bot_key,
        set_access_window_active,
        set_bot_member_blocked,
        set_bot_member_prefs,
        set_member_default_policy,
        set_setting,
        toggle_favorite,
        touch_bot_member,
        update_giveaway_candidate_status,
        update_ping_meta,
        upsert_bot_member,
    )

    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.info("Telegram bot is not configured.")
        return
    try:
        bot_client = telegram_client_for_session("pulse_bot")
        state.bot_client = bot_client
        await bot_client.start(bot_token=BOT_TOKEN)
        bot_me = await bot_client.get_me()
        state.bot_id = bot_me.id
        state.bot_username = bot_me.username
        bot_username = bot_me.username
        logger.info("Bot started: @%s", bot_me.username)

        bot_pending_inputs = state.bot_pending_inputs

        # ---- access control -------------------------------------------------
        def _bot_admin_chat_ids() -> set[int]:
            ids: set[int] = set()
            if ADMIN_ID:
                ids.add(int(ADMIN_ID))
            raw = (settings.bot_admin_chats or "").strip()
            if raw:
                for chunk in raw.split(","):
                    chunk = chunk.strip()
                    if chunk:
                        try:
                            ids.add(int(chunk))
                        except ValueError:
                            pass
            return ids

        async def _access_decision(sender_id: int, member: dict):
            """Cached schedule decision for a member: (allowed, reason, until_utc).

            Source of truth is computed here, not a stored flag — so access stays
            correct even if the scheduler loop is down. The cache only skips repeat
            SQL until the next window boundary."""
            now = datetime.now(timezone.utc)
            cached = state.access_cache.get(sender_id)
            if cached and now < cached[1]:
                return cached[0], cached[2], cached[1]
            rows = await list_access_windows(sender_id)
            decision = resolve_access(member, [window_from_row(r) for r in rows], now)
            until = decision.until or (now + timedelta(minutes=1))
            state.access_cache[sender_id] = (decision.allowed, until, decision.reason)
            return decision.allowed, decision.reason, until

        def _until_phrase(until: Optional[datetime]) -> str:
            if not until:
                return ""
            return f" до {until.astimezone().strftime('%d.%m %H:%M')}"

        async def bot_role(sender_id: int) -> Optional[str]:
            """Resolve a Telegram user to 'admin', 'viewer', or None (no access).

            Admins bypass the schedule; members are additionally gated by their
            access windows (see access_control.resolve_access)."""
            if sender_id in _bot_admin_chat_ids():
                return "admin"
            member = await get_bot_member(sender_id)
            if not member or member.get("blocked"):
                return None
            await touch_bot_member(sender_id)
            allowed, _reason, _until = await _access_decision(sender_id, member)
            if not allowed:
                return None
            return member.get("role") or "viewer"

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

        async def safe_edit(event, *args, **kwargs) -> None:
            """Edit the callback message, ignoring 'not modified' errors."""
            try:
                await event.edit(*args, **kwargs)
            except MessageNotModifiedError:
                await event.answer()

        def _is_callback(event) -> bool:
            return isinstance(event, events.CallbackQuery.Event)

        def safe(handler):
            """Wrap a handler so any failure surfaces feedback instead of a silent log."""
            async def inner(event):
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Bot handler failed: %s", getattr(handler, "__name__", "handler"))
                    with suppress(Exception):
                        if _is_callback(event):
                            await event.answer("⚠️ Ошибка. Попробуйте позже.", alert=True)
                        else:
                            await event.respond("⚠️ Что-то пошло не так. Попробуйте ещё раз.")
            inner.__name__ = getattr(handler, "__name__", "inner")
            return inner

        def viewer_only(handler):
            """Gate a handler to any authenticated role and pass the resolved role in."""
            async def inner(event):
                role = await bot_role(event.sender_id)
                if role is None:
                    notice = await access_block_notice(event.sender_id)
                    if notice:
                        await event.respond(notice)
                    return
                await handler(event, role)
            inner.__name__ = getattr(handler, "__name__", "inner")
            return safe(inner)

        def _cb_id(data: str) -> Optional[int]:
            """Parse the trailing int from a callback like `fav_42`; None if malformed."""
            try:
                return int(data.split("_", 1)[1])
            except (ValueError, IndexError):
                return None

        async def finalize_buttons(event, done_label: str) -> None:
            """Replace the message keyboard with kept URL buttons + a done marker."""
            try:
                msg = await event.get_message()
                kept = [Button.url(b.text, b.url) for row in (msg.buttons or []) for b in row if b.url]
                rows: list[list[Button]] = []
                if kept:
                    rows.append(kept)
                rows.append([Button.inline(done_label, b"noop")])
                await safe_edit(event, buttons=rows)
            except Exception:
                logger.debug("Could not finalize buttons", exc_info=True)

        # ---- shared renderers (reused by slash commands and menu callbacks) -
        async def render_stats() -> str:
            analytics = await build_analytics()
            return (
                "📊 **Статистика**\n"
                f"{DIV}\n"
                f"📨 Всего записей: `{analytics['total_pings']}`\n"
                f"🆕 Новых: `{analytics['new_pings']}`\n"
                f"⭐ Избранных: `{analytics['favorites']}`\n"
                f"🛰 Аккаунтов онлайн: `{analytics['accounts_online']}`"
            )

        async def render_status() -> str:
            accounts_online = sum(1 for acc in list(state.accounts_state.values()) if acc.get("status") == "online")
            account_lines = [
                f"  {'🟢' if a.get('status') == 'online' else '🔴'} `{a.get('session_name', '?')}` — {a.get('status', 'unknown')}"
                for a in list(state.accounts_state.values())
            ]
            try:
                db_size_mb = database.DB_PATH.stat().st_size / 1024 / 1024 if database.DB_PATH.exists() else 0
            except Exception:
                db_size_mb = 0
            uptime_sec = int((datetime.now() - state.started_at).total_seconds())
            uptime_str = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
            running_jobs = sorted(name for name, task in state.background_tasks.items() if not task.done())
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            return (
                f"🛰 **Статус** · `v{APP_VERSION}`\n"
                f"{DIV}\n"
                f"⏱ Uptime: `{uptime_str}`\n"
                f"💾 База: `{db_size_mb:.1f} MB`\n"
                f"🛰 Аккаунты: `{accounts_online}/{len(state.accounts_state)}` online\n"
                f"🔄 Скан: `{last_scan}` · {state.last_scan_status or '—'}\n"
                f"⚙️ Jobs ({len(running_jobs)}): `{', '.join(running_jobs) or '—'}`\n\n"
                "👤 **Аккаунты**\n" + ("\n".join(account_lines) if account_lines else "  __нет аккаунтов__")
            )

        async def render_giveaways() -> str:
            board = await get_giveaway_board(limit=10)
            buckets = board.get("buckets") or {}
            stats = board.get("stats") or {}
            lines = [
                "🎁 **Розыгрыши**",
                DIV,
                f"⏳ Ожидание: `{stats.get('waiting', 0)}`  ·  🎁 Призы: `{stats.get('to_claim', 0)}`  ·  ❗ Срочные: `{stats.get('urgent', 0)}`",
            ]
            urgent = buckets.get("urgent") or []
            if urgent:
                lines.append("\n⚠️ **Срочное**")
                for row in urgent[:5]:
                    lines.append(f"  • `{fmt_dt(row.get('deadline_at'))}` · {row.get('chat') or '?'}\n    {(row.get('text') or '')[:110]}")
            else:
                lines.append("\n✅ __Срочных розыгрышей нет.__")
            return "\n".join(lines)

        async def render_recent(n: int = 5) -> str:
            rows = await get_pings(limit=n)
            if not rows:
                return "🕐 **Последние**\n" + DIV + "\n📭 __Упоминаний пока нет.__"
            result = [f"🕐 **Последние {len(rows)}**", DIV]
            for row in rows:
                priority = row.get("priority_label") or ""
                badge = "🔥" if priority == "critical" else "⚡" if priority == "high" else "•"
                link = row.get("link") or ""
                result.append(
                    f"{badge} `{fmt_dt(row.get('detected_at'))}` · {row['chat']}\n"
                    f"{(row.get('text') or '')[:160]}"
                    + (f"\n🔗 {link}" if link else "")
                )
            return "\n\n".join(result)

        async def render_checks(n: int = 10) -> str:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=CHECK_FRESH_MINUTES)
            ).replace(microsecond=0).isoformat()
            rows = await get_pings(limit=n, chat_type="check", message_date_from=cutoff)
            if not rows:
                return "💸 **Чеки**\n" + DIV + "\n📭 __Чеков пока нет.__"
            result = [f"💸 **Чеки** ({len(rows)})", DIV]
            for row in rows:
                link = row.get("link") or ""
                result.append(
                    f"• `{fmt_dt(row.get('detected_at'))}` · {row['chat']}\n"
                    f"{(row.get('text') or '')[:160]}"
                    + (f"\n🔗 {link}" if link else "")
                )
            return "\n\n".join(result)

        async def render_market() -> str:
            market = await get_market_history(limit=1)
            if not market:
                return "💹 **Курсы**\n" + DIV + "\n📉 __Данные пока недоступны.__"
            m = market[0]
            return (
                "💹 **Курсы**\n"
                f"{DIV}\n"
                f"🟠 BTC: `${m.get('bitcoin', {}).get('usd', 0):,}`\n"
                f"🔷 ETH: `${m.get('ethereum', {}).get('usd', 0):,}`\n"
                f"💎 TON: `${m.get('the-open-network', {}).get('usd', 0):.3f}`\n"
                f"🟣 SOL: `${m.get('solana', {}).get('usd', 0):.2f}`"
            )

        async def render_home(role: str) -> str:
            analytics = await build_analytics()
            board = await get_giveaway_board(limit=10)
            urgent = (board.get("stats") or {}).get("urgent", 0)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(minutes=CHECK_FRESH_MINUTES)
            ).replace(microsecond=0).isoformat()
            fresh = await get_pings(limit=50, chat_type="check", message_date_from=cutoff)
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            if state.last_scan_status:
                last_scan = f"{last_scan} · {state.last_scan_status}"
            return home_card(
                role=role,
                new_pings=analytics["new_pings"],
                urgent=urgent,
                accounts_online=analytics["accounts_online"],
                accounts_total=len(state.accounts_state),
                fresh_checks=len(fresh),
                last_scan=last_scan,
            )

        async def render_summary() -> str:
            analytics = await build_analytics()
            market_rows = await get_market_history(limit=1)
            market = None
            if market_rows:
                m = market_rows[0]
                market = {
                    "btc": m.get("bitcoin", {}).get("usd", 0),
                    "eth": m.get("ethereum", {}).get("usd", 0),
                    "ton": m.get("the-open-network", {}).get("usd", 0),
                    "sol": m.get("solana", {}).get("usd", 0),
                }
            accounts_online = sum(1 for a in list(state.accounts_state.values()) if a.get("status") == "online")
            try:
                db_mb = database.DB_PATH.stat().st_size / 1024 / 1024 if database.DB_PATH.exists() else 0
            except Exception:
                db_mb = 0
            uptime_sec = int((datetime.now() - state.started_at).total_seconds())
            uptime = f"{uptime_sec // 3600}ч {(uptime_sec % 3600) // 60}м"
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            system = {
                "version": APP_VERSION,
                "uptime": uptime,
                "db_mb": db_mb,
                "accounts_online": accounts_online,
                "accounts_total": len(state.accounts_state),
                "last_scan": f"{last_scan} · {state.last_scan_status or '—'}",
            }
            return summary_card(analytics=analytics, market=market, system=system)

        async def render_keys_text() -> str:
            keys = await list_bot_keys()
            if not keys:
                return "🔑 **Ключи доступа**\n" + DIV + "\n📭 __Ключей нет.__\nСоздайте: `/newkey метка`"
            lines = ["🔑 **Ключи доступа**", DIV]
            for k in keys:
                exp = fmt_dt(k.get("expires_at")) if k.get("expires_at") else "бессрочно"
                lines.append(f"`#{k['id']}` · {k.get('label') or '—'}\n   👥 {k.get('member_count', 0)} · ⏳ {exp}")
            return "\n".join(lines)

        # ---- settings menus (admin) + personal prefs (members) -------------
        PENDING_TTL_SECONDS = 300
        SETTINGS_BACK = [Button.inline("⬅️ Назад", b"st")]
        PF_TOGGLES = {
            "pf_mu": "muted",
            "pf_me": "mentions",
            "pf_gw": "giveaways",
            "pf_wn": "wins",
            "pf_ch": "checks",
            "pf_dl": "deadlines",
            "pf_dg": "digest",
        }
        INPUT_PROMPTS = {
            "track_add": "Пришлите юзернейм(ы) для отслеживания — через запятую или пробел.",
            "track_rm": "Пришлите номер из списка или сам юзернейм для удаления.",
            "kw_add": "Пришлите ключевое слово (или несколько через запятую).",
            "kw_rm": "Пришлите номер из списка или точный текст слова для удаления.",
            "quiet": "Пришлите интервал тихих часов в формате `23:00-08:00`.",
            "cooldown": "Пришлите кулдаун в секундах (0–3600).",
            "digest_time": "Пришлите время дайджеста в формате `09:00`.",
        }

        def _pending_expired(pending: dict) -> bool:
            armed = pending.get("armed_at")
            return not isinstance(armed, datetime) or (datetime.now() - armed).total_seconds() > PENDING_TTL_SECONDS

        async def prompt_pending(event, kind: str, scope: Optional[str] = None) -> None:
            bot_pending_inputs[event.sender_id] = {"kind": kind, "scope": scope, "armed_at": datetime.now()}
            await event.respond(f"✍️ {INPUT_PROMPTS[kind]}", buttons=[[Button.inline("✖️ Отмена", b"st_x")]])
            await event.answer()

        def _resolve_removal(items: list[str], text: str) -> Optional[int]:
            """Match a removal request against a numbered list: by index or exact text."""
            cleaned = text.strip().lstrip("@")
            if cleaned.isdigit():
                idx = int(cleaned) - 1
                return idx if 0 <= idx < len(items) else None
            lowered = cleaned.lower()
            for i, item in enumerate(items):
                if item.lstrip("@").lower() == lowered:
                    return i
            return None

        async def save_tracking_from_bot(usernames: list[str]) -> None:
            ws.apply_tracking_settings({"usernames": usernames})
            await set_setting("tracking", {"usernames": state.ping_usernames})
            await record_app_event("INFO", "settings", "Tracked usernames updated", {"count": len(state.ping_usernames), "via": "bot"})
            await publish_live_event("settings-updated", {"scope": "tracking", "usernames": len(state.ping_usernames)})

        async def save_keywords_from_bot(values: dict[str, list[str]]) -> None:
            await set_setting("keywords", values)
            ws.apply_keyword_settings(values)
            await record_app_event("INFO", "settings", "Keyword rules updated", {"via": "bot"})
            await publish_live_event("settings-updated", {"scope": "keywords"})

        async def save_notifications_from_bot(settings_dict: dict) -> None:
            await set_setting("notifications", settings_dict)
            await record_app_event("INFO", "settings", "Notification rules updated", {"via": "bot"})
            await publish_live_event("settings-updated", {"scope": "notifications"})

        async def save_digest_from_bot(cfg: dict) -> None:
            await set_setting("digest", cfg)
            await record_app_event("INFO", "settings", "Digest settings updated", {"via": "bot", **cfg})
            await publish_live_event("settings-updated", {"scope": "digest"})

        def settings_root_menu() -> tuple[str, list[list[Button]]]:
            text = "⚙️ **Настройки**\n" + DIV + "\nУправление мониторингом прямо из бота."
            buttons = [
                [Button.inline("👁 Юзернеймы", b"st_u"), Button.inline("🔑 Ключевые слова", b"st_k")],
                [Button.inline("🔔 Уведомления", b"st_n")],
                [Button.inline("⬅️ Меню", b"menu_main")],
            ]
            return text, buttons

        def usernames_menu() -> tuple[str, list[list[Button]]]:
            buttons = [
                [Button.inline("➕ Добавить", b"st_u_add"), Button.inline("➖ Удалить", b"st_u_rm")],
                SETTINGS_BACK,
            ]
            return render_tracking_text(state.ping_usernames), buttons

        def keywords_root_menu() -> tuple[str, list[list[Button]]]:
            text = "🔑 **Ключевые слова**\n" + DIV + "\nВыберите список."
            scope_buttons = [Button.inline(label, f"st_k_{code}".encode()) for code, (_, label) in KEYWORD_SCOPES.items()]
            buttons = [scope_buttons[i:i + 2] for i in range(0, len(scope_buttons), 2)]
            buttons.append(SETTINGS_BACK)
            return text, buttons

        async def keyword_list_menu(code: str) -> tuple[str, list[list[Button]]]:
            key, label = KEYWORD_SCOPES[code]
            values = await ws.load_keyword_settings()
            buttons = [
                [Button.inline("➕ Добавить", f"st_k_{code}_add".encode()), Button.inline("➖ Удалить", f"st_k_{code}_rm".encode())],
                [Button.inline("⬅️ Назад", b"st_k")],
            ]
            return render_keyword_list_text(label, values.get(key) or []), buttons

        async def notifications_menu() -> tuple[str, list[list[Button]]]:
            notif = await ws.load_notification_settings()
            digest_cfg = await ws.load_digest_settings()
            quiet = notif.get("quiet_hours") or {}
            mark = lambda value: "✅" if value else "❌"
            buttons = [
                [Button.inline(f"{mark(notif.get('enabled', True))} Уведомления", b"st_n_en"),
                 Button.inline(f"{mark(quiet.get('enabled'))} Тихие часы", b"st_n_q")],
                [Button.inline(f"{mark(notif.get('include_giveaways', True))} Розыгрыши", b"st_n_gw"),
                 Button.inline(f"{mark(notif.get('include_wins', True))} Победы", b"st_n_wn")],
                [Button.inline("🕘 Часы тишины…", b"st_n_qt"), Button.inline("⏱ Кулдаун…", b"st_n_cd")],
                [Button.inline(f"{mark(digest_cfg.get('enabled'))} Дайджест", b"st_d_en"),
                 Button.inline("🕘 Время дайджеста…", b"st_d_tm")],
                SETTINGS_BACK,
            ]
            return render_notification_settings_text(notif, digest_cfg), buttons

        def member_prefs_menu(prefs: dict) -> tuple[str, list[list[Button]]]:
            def toggle_btn(label: str, key: str, cb: bytes) -> Button:
                return Button.inline(f"{'✅' if prefs.get(key) else '🔕'} {label}", cb)

            buttons = [
                [Button.inline("🔔 Включить всё" if prefs.get("muted") else "🔕 Отключить всё", b"pf_mu")],
                [toggle_btn("Упоминания", "mentions", b"pf_me"), toggle_btn("Розыгрыши", "giveaways", b"pf_gw")],
                [toggle_btn("Победы", "wins", b"pf_wn"), toggle_btn("Чеки", "checks", b"pf_ch")],
                [toggle_btn("Дедлайны", "deadlines", b"pf_dl"), toggle_btn("Дайджест", "digest", b"pf_dg")],
                [Button.inline("⬅️ Меню", b"menu_main")],
            ]
            return render_member_prefs_text(prefs), buttons

        async def handle_settings_callback(event, data: str) -> None:
            if data in ("st", "st_x"):
                bot_pending_inputs.pop(event.sender_id, None)
                if data == "st_x":
                    await event.answer("Отменено")
                text, buttons = settings_root_menu()
                await safe_edit(event, text, buttons=buttons)
                return
            if data == "st_u":
                text, buttons = usernames_menu()
                await safe_edit(event, text, buttons=buttons)
                return
            if data == "st_u_add":
                await prompt_pending(event, "track_add")
                return
            if data == "st_u_rm":
                await prompt_pending(event, "track_rm")
                return
            if data == "st_k":
                text, buttons = keywords_root_menu()
                await safe_edit(event, text, buttons=buttons)
                return
            if data.startswith("st_k_"):
                rest = data[len("st_k_"):]
                code = rest[:1]
                if code not in KEYWORD_SCOPES:
                    await event.answer()
                    return
                if rest.endswith("_add"):
                    await prompt_pending(event, "kw_add", code)
                    return
                if rest.endswith("_rm"):
                    await prompt_pending(event, "kw_rm", code)
                    return
                text, buttons = await keyword_list_menu(code)
                await safe_edit(event, text, buttons=buttons)
                return
            if data in ("st_n_en", "st_n_gw", "st_n_wn", "st_n_q"):
                notif = await ws.load_notification_settings()
                if data == "st_n_en":
                    notif["enabled"] = not notif.get("enabled", True)
                elif data == "st_n_gw":
                    notif["include_giveaways"] = not notif.get("include_giveaways", True)
                elif data == "st_n_wn":
                    notif["include_wins"] = not notif.get("include_wins", True)
                else:
                    quiet = dict(notif.get("quiet_hours") or {})
                    quiet.setdefault("from", "23:00")
                    quiet.setdefault("to", "08:00")
                    quiet["enabled"] = not quiet.get("enabled")
                    notif["quiet_hours"] = quiet
                await save_notifications_from_bot(notif)
                text, buttons = await notifications_menu()
                await safe_edit(event, text, buttons=buttons)
                await event.answer("Сохранено")
                return
            if data == "st_n_qt":
                await prompt_pending(event, "quiet")
                return
            if data == "st_n_cd":
                await prompt_pending(event, "cooldown")
                return
            if data == "st_d_en":
                cfg = await ws.load_digest_settings()
                cfg["enabled"] = not cfg.get("enabled", True)
                await save_digest_from_bot(cfg)
                text, buttons = await notifications_menu()
                await safe_edit(event, text, buttons=buttons)
                await event.answer("Сохранено")
                return
            if data == "st_d_tm":
                await prompt_pending(event, "digest_time")
                return
            if data == "st_n":
                text, buttons = await notifications_menu()
                await safe_edit(event, text, buttons=buttons)
                return
            await event.answer()

        async def handle_pending_input(event, role: str, pending: dict) -> None:
            if role != "admin":
                return
            kind = pending.get("kind")
            raw = (event.message.text or "").strip()
            if kind == "track_add":
                additions = normalize_usernames(re.split(r"[\s,;]+", raw))
                if not additions:
                    await event.respond("❌ Не распознал юзернеймы. Откройте меню и попробуйте ещё раз.")
                    return
                merged = list(state.ping_usernames)
                added = [u for u in additions if u not in merged]
                merged.extend(added)
                if added:
                    await save_tracking_from_bot(merged)
                note = "✅ Добавлено: " + ", ".join(added) if added else "ℹ️ Уже в списке."
                text, buttons = usernames_menu()
                await event.respond(f"{note}\n\n{text}", buttons=buttons)
                return
            if kind == "track_rm":
                items = list(state.ping_usernames)
                idx = _resolve_removal(items, raw)
                if idx is None:
                    await event.respond("❌ Не нашёл такой юзернейм. Пришлите номер из списка.")
                    return
                removed = items.pop(idx)
                await save_tracking_from_bot(items)
                note = f"🗑 Удалён: {removed}"
                if not items:
                    note += "\n⚠️ Список пуст — восстановлены юзернеймы из .env."
                text, buttons = usernames_menu()
                await event.respond(f"{note}\n\n{text}", buttons=buttons)
                return
            if kind in ("kw_add", "kw_rm"):
                code = pending.get("scope") or "w"
                key, label = KEYWORD_SCOPES[code]
                values = await ws.load_keyword_settings()
                items = list(values.get(key) or [])
                if kind == "kw_add":
                    parts = [p.strip() for p in raw.split(",") if p.strip()]
                    if code == "g":
                        parts = [p.lower() for p in parts]
                    added = [p for p in parts if p not in items]
                    if not added:
                        await event.respond("ℹ️ Нечего добавлять — пусто или уже в списке.")
                        return
                    items.extend(added)
                    note = "✅ Добавлено: " + ", ".join(added)
                else:
                    idx = _resolve_removal(items, raw)
                    if idx is None:
                        await event.respond("❌ Не нашёл такое слово. Пришлите номер из списка.")
                        return
                    if code in ("w", "g") and len(items) == 1:
                        await event.respond("⚠️ Этот список не может быть пустым — удаление отменено.")
                        return
                    note = f"🗑 Удалено: {items.pop(idx)}"
                values[key] = items
                await save_keywords_from_bot(values)
                text, buttons = await keyword_list_menu(code)
                await event.respond(f"{note}\n\n{text}", buttons=buttons)
                return
            if kind == "quiet":
                parsed = parse_quiet_hours_input(raw)
                if not parsed:
                    await event.respond("❌ Формат: `23:00-08:00`. Попробуйте ещё раз через меню.")
                    return
                notif = await ws.load_notification_settings()
                notif["quiet_hours"] = {"enabled": True, "from": parsed[0], "to": parsed[1]}
                await save_notifications_from_bot(notif)
                text, buttons = await notifications_menu()
                await event.respond(f"✅ Тихие часы: {parsed[0]}–{parsed[1]}\n\n{text}", buttons=buttons)
                return
            if kind == "cooldown":
                try:
                    value = max(0, min(3600, int(raw)))
                except ValueError:
                    await event.respond("❌ Нужно число секунд (0–3600).")
                    return
                notif = await ws.load_notification_settings()
                notif["cooldown_seconds"] = value
                await save_notifications_from_bot(notif)
                text, buttons = await notifications_menu()
                await event.respond(f"✅ Кулдаун: {value} сек\n\n{text}", buttons=buttons)
                return
            if kind == "digest_time":
                parsed_time = parse_hhmm(raw)
                if not parsed_time:
                    await event.respond("❌ Формат: `09:00`. Попробуйте ещё раз через меню.")
                    return
                cfg = await ws.load_digest_settings()
                cfg["time"] = parsed_time
                await save_digest_from_bot(cfg)
                text, buttons = await notifications_menu()
                await event.respond(f"✅ Дайджест в {parsed_time}\n\n{text}", buttons=buttons)
                return

        # ---- redeem / onboarding -------------------------------------------
        async def grant_access(event, key: dict) -> None:
            existing = await get_bot_member(event.sender_id)
            if existing and existing.get("blocked"):
                await event.respond("🚫 **Доступ отключён владельцем.**")
                return
            sender = await event.get_sender()
            uname = getattr(sender, "username", "") or ""
            name = " ".join(filter(None, [getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or ""])).strip()
            role = key.get("role") or "viewer"
            await upsert_bot_member(event.sender_id, uname, name, key.get("id"), role)
            greeting = f"Привет, {name}!" if name else "Привет!"
            await event.respond(
                "✅ **Доступ открыт!**\n"
                f"{greeting} Добро пожаловать в **Pulse Desk**.\n"
                + f"{DIV}\n"
                + ("👑 Доступ: __полный__" if role == "admin" else "👁 Доступ: __только просмотр__")
                + "\n\nВыберите раздел 👇",
                buttons=main_menu_buttons(role),
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
                key = await get_bot_key_by_secret(payload)
                if key:
                    await grant_access(event, key)
                    return
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            role = await bot_role(event.sender_id)
            if role is None:
                await event.respond(await access_block_notice(event.sender_id) or locked_text)
                return
            welcome_banner = BOT_ASSETS_DIR / "welcome.png"
            if welcome_banner.exists():
                try:
                    await event.respond(await render_home(role), buttons=main_menu_buttons(role), file=str(welcome_banner))
                    return
                except Exception:
                    logger.warning("Failed to send welcome banner, falling back to text", exc_info=True)
            await event.respond(await render_home(role), buttons=main_menu_buttons(role))

        @bot_client.on(events.NewMessage(pattern=r"/redeem(?:\s+(\S+))?"))
        @safe
        async def redeem_handler(event):
            payload = (event.pattern_match.group(1) or "").strip()
            if not payload:
                await event.respond("Использование: `/redeem <ключ>`")
                return
            key = await get_bot_key_by_secret(payload)
            if not key:
                await event.respond("❌ **Ключ недействителен или отозван.**")
                return
            await grant_access(event, key)

        @bot_client.on(events.NewMessage(pattern="/menu"))
        @safe
        async def menu_handler(event):
            role = await bot_role(event.sender_id)
            if role is None:
                await event.respond(await access_block_notice(event.sender_id) or locked_text)
                return
            await event.respond(await render_home(role), buttons=main_menu_buttons(role))

        @bot_client.on(events.NewMessage(pattern="/settings"))
        @viewer_only
        async def settings_handler(event, role):
            if role == "admin":
                text, buttons = settings_root_menu()
                await event.respond(text, buttons=buttons)
                return
            member = await get_bot_member(event.sender_id)
            if not member:
                await event.respond("Личные настройки недоступны.")
                return
            text, buttons = member_prefs_menu(parse_member_prefs(member.get("notification_prefs")))
            await event.respond(text, buttons=buttons)

        @bot_client.on(events.NewMessage(pattern="/help"))
        @viewer_only
        async def help_handler(event, role):
            await event.respond(help_text(role), buttons=section_nav(b"menu_help"))

        @bot_client.on(events.NewMessage(pattern="/stats"))
        @viewer_only
        async def stats_handler(event, role):
            await event.respond(await render_stats(), buttons=section_nav(b"menu_stats"))

        @bot_client.on(events.NewMessage(pattern="/status"))
        @viewer_only
        async def status_handler(event, role):
            await event.respond(await render_status(), buttons=section_nav(b"menu_status"))

        @bot_client.on(events.NewMessage(pattern="/giveaways"))
        @viewer_only
        async def giveaways_handler(event, role):
            await event.respond(await render_giveaways(), buttons=section_nav(b"menu_giveaways"), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/recent"))
        @viewer_only
        async def recent_handler(event, role):
            parts = (event.message.text or "").split(" ", 1)
            try:
                n = max(1, min(20, int(parts[1]))) if len(parts) > 1 else 5
            except (ValueError, IndexError):
                n = 5
            await event.respond(await render_recent(n), buttons=section_nav(b"menu_recent"), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/latest"))
        @viewer_only
        async def latest_handler(event, role):
            await event.respond(await render_recent(5), buttons=section_nav(b"menu_recent"), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/search"))
        @viewer_only
        async def search_handler(event, role):
            parts = event.message.text.split(" ", 1)
            if len(parts) < 2:
                await event.respond("Укажите текст: `/search TON`")
                return
            rows = await get_pings(limit=5, search=parts[1])
            if not rows:
                await event.respond("🔎 __Ничего не найдено.__", buttons=back_home())
                return
            result = ["🔎 **Результаты поиска**", DIV]
            for row in rows:
                link = row.get("link") or ""
                result.append(
                    f"• `{fmt_dt(row.get('detected_at'))}` · {row['chat']}\n{(row.get('text') or '')[:160]}"
                    + (f"\n🔗 {link}" if link else "")
                )
            await event.respond("\n\n".join(result), buttons=back_home(), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/checks"))
        @viewer_only
        async def checks_handler(event, role):
            await event.respond(await render_checks(10), buttons=section_nav(b"menu_checks"), link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/market"))
        @viewer_only
        async def market_handler(event, role):
            await event.respond(await render_market(), buttons=section_nav(b"menu_market"))

        @bot_client.on(events.NewMessage(pattern="/ping"))
        @viewer_only
        async def ping_handler(event, role):
            await event.respond("🏓 **Понг!** Бот на связи.")

        @bot_client.on(events.NewMessage(pattern="/logs"))
        @safe
        async def logs_handler(event):
            if await deny_non_admin(event):
                return
            if not LOG_FILE.exists():
                await event.respond("Логов пока нет.")
                return
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            await event.respond("📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-3500:] + "\n```")

        @bot_client.on(events.NewMessage(pattern="/export"))
        @safe
        async def export_bot_handler(event):
            if await deny_non_admin(event):
                return
            await event.respond("📤 **Экспорт CSV**\n" + DIV + "\nДоступен в веб-интерфейсе: `/api/export-csv`")

        @bot_client.on(events.NewMessage(pattern="/scan"))
        @safe
        async def scan_handler(event):
            if await deny_non_admin(event):
                return
            if state.scan_lock.locked():
                await event.respond("⏳ Сканирование уже идёт.")
                return
            asyncio.create_task(full_history_scan())
            await event.respond("🔄 **Сканирование истории запущено.**")

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
            await create_bot_key(label, secret, "viewer", None)
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

        @bot_client.on(events.NewMessage(pattern="/keys"))
        @safe
        async def keys_handler(event):
            if await deny_non_admin(event):
                return
            keys = await list_bot_keys()
            if not keys:
                await event.respond("🔑 **Ключи доступа**\n" + DIV + "\n📭 __Ключей нет.__\nСоздайте: `/newkey метка`")
                return
            await event.respond(f"🔑 **Ключи доступа** · `{len(keys)}`")
            for k in keys:
                exp = fmt_dt(k.get("expires_at")) if k.get("expires_at") else "бессрочно"
                await event.respond(
                    f"`#{k['id']}` · **{k.get('label') or '—'}**\n👥 {k.get('member_count', 0)}  ·  ⏳ {exp}",
                    buttons=[[Button.inline("🗑 Отозвать", f"revokekey_{k['id']}".encode())]],
                )

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
            if not members:
                note = f"🔎 По запросу «{query}» никого." if query else "📭 __Пока никого.__"
                await event.respond("👥 **Пользователи**\n" + DIV + "\n" + note, buttons=main_menu_buttons("admin"))
                return
            header = f"👥 **Пользователи** · `{len(members)}`"
            if query:
                header += f"  ·  🔎 «{query}»"
            await event.respond(header + f"\n{DIV}\n_Подсказка: поиск — `/members <имя|@ник|id>`_")
            for m in members:
                uname = f"@{m['tg_username']}" if m.get("tg_username") else "—"
                badge = "🚫 заблокирован" if m.get("blocked") else "🟢 активен"
                seen = fmt_dt(m.get("last_seen_at"))
                if m.get("blocked"):
                    btn = Button.inline("✅ Разблокировать", f"unblockmember_{m['tg_id']}".encode())
                else:
                    btn = Button.inline("🚫 Заблокировать", f"blockmember_{m['tg_id']}".encode())
                await event.respond(
                    f"👤 **{m.get('name') or '—'}** ({uname})\n🔑 {m.get('key_label') or '—'}  ·  {badge}\n🕐 {seen}",
                    buttons=[
                        [btn],
                        [
                            Button.inline("⏰ Доступ", f"accshow_{m['tg_id']}".encode()),
                            Button.inline("🔴 Выкл", f"accoff_{m['tg_id']}".encode()),
                            Button.inline("🟢 Вкл", f"accon_{m['tg_id']}".encode()),
                        ],
                    ],
                )

        # ---- scheduled access management (owner only) ----------------------
        async def _resolve_member_arg(query: str) -> Optional[dict]:
            q = query.strip().lstrip("@").lower()
            members = await list_bot_members()
            for m in members:
                if q == str(m.get("tg_id")) or q == (m.get("tg_username") or "").lower():
                    return m
            for m in members:
                if q and q in (m.get("name") or "").lower():
                    return m
            return None

        def _describe_repeat(rep: dict, row: dict) -> str:
            tz = row.get("timezone") or "UTC"
            rtype = rep.get("type")
            if rtype == "daily":
                return f"ежедневно {rep.get('from', '?')}–{rep.get('to', '?')} ({tz})"
            if rtype == "weekly":
                days = ",".join(_ACCESS_DAY_NAMES.get(d, str(d)) for d in rep.get("days", []))
                return f"{days or '—'} {rep.get('from', '?')}–{rep.get('to', '?')} ({tz})"
            if rtype == "cron":
                return f"cron `{rep.get('expr', '?')}` · {rep.get('dur_min', '?')} мин ({tz})"
            if rtype == "none":
                return f"разово {fmt_dt(row.get('start_at'))} → {fmt_dt(row.get('end_at')) if row.get('end_at') else 'бессрочно'}"
            return str(rep)

        async def render_member_access(member: dict) -> str:
            tg = int(member["tg_id"])
            rows = await list_access_windows(tg)
            decision = resolve_access(member, [window_from_row(r) for r in rows], datetime.now(timezone.utc))
            lines = [
                f"⏰ **Доступ** · {member.get('name') or tg}",
                DIV,
                f"Сейчас: {'🟢 открыт' if decision.allowed else '🔴 закрыт'}  ·  по умолчанию: `{member.get('access_default_policy', 'allow')}`",
            ]
            if not rows:
                lines.append("\n__Правил нет — действует политика по умолчанию.__")
            else:
                lines.append("\n📋 **Окна**")
                for r in rows:
                    kind = "✅ разрешает" if r["enabled"] else "🚫 запрещает"
                    lines.append(f"`#{r['id']}` · {kind} · prio {r['priority']}\n   {_describe_repeat(parse_repeat_rule(r['repeat_rule']), r)}")
            lines.append("\n_off [18:00|2h] · on · work 09:00-18:00 mon-fri [TZ] · mute 23:00-08:00 · cron · del <id> · undo · log_")
            return "\n".join(lines)

        async def render_access_overview() -> str:
            members = await list_bot_members()
            grouped = await list_all_access_windows()
            now = datetime.now(timezone.utc)
            lines = ["⏰ **Ограничения доступа**", DIV]
            restricted = []
            for m in members:
                if m.get("blocked"):
                    continue
                tg = int(m["tg_id"])
                d = resolve_access(m, [window_from_row(r) for r in grouped.get(tg, [])], now)
                if not d.allowed:
                    restricted.append((m, d))
            if not restricted:
                lines.append("✅ Сейчас все участники открыты.")
            else:
                for m, d in restricted:
                    uname = f"@{m['tg_username']}" if m.get("tg_username") else "—"
                    until = f" до {d.until.astimezone().strftime('%d.%m %H:%M')}" if d.until else ""
                    lines.append(f"🔴 {m.get('name') or '—'} ({uname}){until}")
            lines.append("\n_Подробно: /access <user>_")
            return "\n".join(lines)

        async def _open_member_access(tg: int, actor_id: int) -> int:
            """Cancel manual blackouts (the high-priority one-shots created by 'off')."""
            cancelled_ids: list[int] = []
            for w in await list_access_windows(tg):
                if not w["enabled"] and w["priority"] >= 1000:
                    await deactivate_access_window(int(w["id"]))
                    cancelled_ids.append(int(w["id"]))
            await record_access_audit(tg, None, "manual_on", f"admin:{actor_id}", None, {"cancelled_ids": cancelled_ids})
            state.access_cache.pop(tg, None)
            return len(cancelled_ids)

        async def _apply_undo(tg: int, plan: dict) -> None:
            op = plan.get("op")
            if op == "deactivate" and plan.get("schedule_id"):
                await deactivate_access_window(int(plan["schedule_id"]))
            elif op == "reactivate" and plan.get("schedule_id"):
                await set_access_window_active(int(plan["schedule_id"]), True)
            elif op == "reactivate_many":
                for sid in plan.get("schedule_ids", []):
                    await set_access_window_active(int(sid), True)
            elif op == "set_policy" and plan.get("policy"):
                await set_member_default_policy(tg, plan["policy"])

        @bot_client.on(events.NewMessage(pattern=r"/access(?:\s+(.+))?"))
        @safe
        async def access_handler(event):
            if await deny_non_admin(event):
                return
            raw = (event.pattern_match.group(1) or "").strip()
            if not raw:
                await event.respond(await render_access_overview(), buttons=main_menu_buttons("admin"))
                return
            parts = raw.split()
            member = await _resolve_member_arg(parts[0])
            if not member:
                await event.respond(f"❌ Не нашёл участника «{parts[0]}». Список: /members")
                return
            tg = int(member["tg_id"])
            state.access_cache.pop(tg, None)
            args = parts[1:]
            sub = args[0].lower() if args else "show"

            if sub in ("show", "rules", "status"):
                await event.respond(await render_member_access(member))
                return
            if sub == "on":
                cancelled = await _open_member_access(tg, event.sender_id)
                await event.respond(f"🟢 Доступ открыт. Снято ограничений: {cancelled}\n\n" + await render_member_access(member))
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
                row = await create_disable_until_window(tg, until_iso, created_by=event.sender_id)
                await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}", None, {"until": until_iso})
                state.access_cache.pop(tg, None)
                await event.respond(f"🔴 Доступ закрыт ({human}).\n\n" + await render_member_access(member))
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
                await event.respond(f"✅ Cron-окно #{row['id']} добавлено (tz=`{tz}`).\n\n" + await render_member_access(member))
                return
            if sub in ("work", "mute"):
                if len(args) < 2 or not parse_quiet_hours_input(args[1]):
                    await event.respond("❌ Формат: `/access <user> work 09:00-18:00 mon-fri [TZ]`")
                    return
                frm, to = parse_quiet_hours_input(args[1])
                days = parse_weekday_spec(args[2]) if len(args) >= 3 else []
                tz = args[3] if len(args) >= 4 else (member.get("timezone") or "UTC")
                repeat = {"type": "weekly" if days else "daily", "from": frm, "to": to}
                if days:
                    repeat["days"] = days
                enabled = sub == "work"
                if enabled:
                    await set_member_default_policy(tg, "deny")
                row = await create_access_window(tg, enabled=enabled, repeat_rule=repeat, timezone=tz, priority=200, label=sub, created_by=event.sender_id)
                await record_access_audit(tg, int(row["id"]), "create", f"admin:{event.sender_id}", None, repeat)
                state.access_cache.pop(tg, None)
                await event.respond(f"✅ Окно #{row['id']} добавлено (tz=`{tz}`).\n\n" + await render_member_access(member))
                return
            if sub == "del":
                if len(args) < 2 or not args[1].isdigit():
                    await event.respond("❌ Формат: `/access <user> del <id>`")
                    return
                await deactivate_access_window(int(args[1]))
                await record_access_audit(tg, int(args[1]), "delete", f"admin:{event.sender_id}", None, None)
                state.access_cache.pop(tg, None)
                await event.respond(f"🗑 Окно #{args[1]} удалено.\n\n" + await render_member_access(member))
                return
            if sub == "undo":
                target = find_undoable(await get_access_audit(tg, limit=50))
                if not target:
                    await event.respond("↩️ Нечего отменять.")
                    return
                plan = plan_undo(target)
                await _apply_undo(tg, plan)
                await record_access_audit(
                    tg, target.get("schedule_id"), "undo", f"admin:{event.sender_id}",
                    None, {"undone_audit_id": int(target["id"]), "plan": plan},
                )
                state.access_cache.pop(tg, None)
                await event.respond(f"↩️ Отменено: {target['action']} (запись #{target['id']}).\n\n" + await render_member_access(member))
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
                await event.respond("🧾 **Действия по розыгрышам**\n" + DIV + "\n📭 __Пока ничего.__", buttons=main_menu_buttons("admin"))
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
            await event.respond("\n".join(lines), buttons=main_menu_buttons("admin"))

        @bot_client.on(events.NewMessage(func=lambda e: bool(e.is_private and e.message and e.message.text and not e.message.text.startswith("/"))))
        @safe
        async def freeform_handler(event):
            role = await bot_role(event.sender_id)
            if role is not None:
                # Members' free text feeds pending settings inputs, nothing else.
                pending = bot_pending_inputs.pop(event.sender_id, None)
                if pending:
                    if _pending_expired(pending):
                        await event.respond(
                            "⌛ Время ввода истекло — поле сброшено.\nОткройте меню заново: /menu",
                            buttons=main_menu_buttons(role),
                        )
                    else:
                        await handle_pending_input(event, role, pending)
                return
            # A known member closed by schedule gets the reason, not the locked banner.
            notice = await access_block_notice(event.sender_id)
            if notice:
                await event.respond(notice)
                return
            # Treat a bare message as a possible access key for non-members.
            key = await get_bot_key_by_secret((event.message.text or "").strip())
            if key:
                await grant_access(event, key)
                return
            await event.respond(locked_text)

        @bot_client.on(events.CallbackQuery())
        @safe
        async def callback_handler(event):
            data = event.data.decode("utf-8")
            role = await bot_role(event.sender_id)
            if role is None:
                await event.answer("Доступ запрещён", alert=True)
                return

            # ---- menu navigation (any authenticated role) ----
            if data == "menu_help":
                await safe_edit(event, help_text(role), buttons=section_nav(b"menu_help"))
                return
            if data == "menu_stats":
                await safe_edit(event, await render_stats(), buttons=section_nav(b"menu_stats"))
                return
            if data == "menu_status":
                await safe_edit(event, await render_status(), buttons=section_nav(b"menu_status"))
                return
            if data == "menu_giveaways":
                await safe_edit(event, await render_giveaways(), buttons=section_nav(b"menu_giveaways"), link_preview=False)
                return
            if data == "menu_recent":
                await safe_edit(event, await render_recent(5), buttons=section_nav(b"menu_recent"), link_preview=False)
                return
            if data == "menu_checks":
                await safe_edit(event, await render_checks(10), buttons=section_nav(b"menu_checks"), link_preview=False)
                return
            if data == "menu_market":
                await safe_edit(event, await render_market(), buttons=section_nav(b"menu_market"))
                return
            if data == "menu_summary":
                await safe_edit(event, await render_summary(), buttons=section_nav(b"menu_summary"))
                return

            if data == "menu_main":
                await safe_edit(event, await render_home(role), buttons=main_menu_buttons(role))
                return

            if data == "noop":
                await event.answer()
                return

            # ---- personal notification prefs (any member) ----
            if data == "pf" or data.startswith("pf_"):
                member = await get_bot_member(event.sender_id)
                if not member:
                    await event.answer("Вы получаете уведомления как владелец — личные настройки не нужны", alert=True)
                    return
                prefs = parse_member_prefs(member.get("notification_prefs"))
                toggled = PF_TOGGLES.get(data)
                if toggled:
                    prefs = toggle_member_pref(prefs, toggled)
                    await set_bot_member_prefs(event.sender_id, prefs)
                text, buttons = member_prefs_menu(prefs)
                await safe_edit(event, text, buttons=buttons)
                if toggled:
                    await event.answer("Сохранено")
                return

            # ---- settings menus (owner only) ----
            if data == "st" or data.startswith("st_"):
                if role != "admin":
                    await event.answer("Только владелец", alert=True)
                    return
                await handle_settings_callback(event, data)
                return

            # ---- owner-only menu + actions ----
            admin_prefixes = ("revokekey_", "blockmember_", "unblockmember_", "fav_", "read_", "gconfirm_", "gskip_", "hidebc_", "accshow_", "accoff_", "accon_")
            if data in ("menu_keys", "menu_scan", "menu_logs", "menu_restart") or data.startswith(admin_prefixes):
                if role != "admin":
                    await event.answer("Только владелец", alert=True)
                    return

            if data == "menu_keys":
                await safe_edit(event, await render_keys_text(), buttons=main_menu_buttons(role))
                return
            if data == "menu_scan":
                if state.scan_lock.locked():
                    await event.answer("Скан уже идёт")
                else:
                    asyncio.create_task(full_history_scan())
                    await event.answer("Скан запущен")
                return
            if data == "menu_restart":
                result = await restart_monitoring()
                await event.answer(f"♻️ Перезапуск: {result.get('restarted', 0)} аккаунт(ов)", alert=True)
                return
            if data == "menu_logs":
                if not LOG_FILE.exists():
                    await event.answer("Логов нет")
                    return
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                await safe_edit(event, "**Последние логи:**\n\n`" + "\n".join(lines)[-3500:] + "`", buttons=main_menu_buttons(role))
                return
            if data.startswith("revokekey_"):
                key_id = _cb_id(data)
                if key_id is None:
                    await event.answer("Некорректная команда", alert=True)
                    return
                await revoke_bot_key(key_id)
                await event.answer("Ключ отозван")
                await safe_edit(event, await render_keys_text(), buttons=main_menu_buttons(role))
                return
            if data.startswith("blockmember_"):
                member_id = _cb_id(data)
                if member_id is None:
                    await event.answer("Некорректная команда", alert=True)
                    return
                await set_bot_member_blocked(member_id, True)
                await event.answer("Пользователь заблокирован")
                return
            if data.startswith("unblockmember_"):
                member_id = _cb_id(data)
                if member_id is None:
                    await event.answer("Некорректная команда", alert=True)
                    return
                await set_bot_member_blocked(member_id, False)
                await event.answer("Пользователь разблокирован")
                return
            if data.startswith("hidebc_"):
                token = data.split("_", 1)[1]
                rows = await get_broadcast_messages(token)
                if not rows:
                    await event.answer("Уже скрыто или устарело")
                    return
                hidden = 0
                failed = 0
                for row in rows:
                    try:
                        await bot_client.delete_messages(int(row["tg_id"]), [int(row["message_id"])])
                        hidden += 1
                    except Exception as exc:
                        failed += 1
                        logger.warning("Failed to delete broadcast copy for %s: %s", row.get("tg_id"), exc)
                await delete_broadcast_messages(token)
                # Swap the hide button for a confirmation, keeping the rest of the keyboard.
                try:
                    msg = await event.get_message()
                    new_rows: list[list[Button]] = []
                    for btn_row in (msg.buttons or []):
                        rebuilt = []
                        for btn in btn_row:
                            if btn.data == event.data:
                                rebuilt.append(Button.inline(f"✅ Скрыто у друзей ({hidden})", data=b"noop"))
                            elif btn.url:
                                rebuilt.append(Button.url(btn.text, btn.url))
                            elif btn.data:
                                rebuilt.append(Button.inline(btn.text, btn.data))
                        if rebuilt:
                            new_rows.append(rebuilt)
                    if new_rows:
                        await safe_edit(event, buttons=new_rows)
                except Exception:
                    logger.debug("Could not update hide button after broadcast hide", exc_info=True)
                note = f"Скрыто у {hidden} друзей"
                if failed:
                    note += f", не удалось: {failed}"
                await event.answer(note, alert=bool(failed))
                return

            # ---- scheduled access quick actions (owner only, gated above) ----
            if data.startswith(("accshow_", "accoff_", "accon_")):
                tg = _cb_id(data)
                if tg is None:
                    await event.answer("Некорректная команда", alert=True)
                    return
                member = await get_bot_member(tg)
                if not member:
                    await event.answer("Участник не найден", alert=True)
                    return
                if data.startswith("accoff_"):
                    row = await create_disable_until_window(tg, None, created_by=event.sender_id)
                    await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}", None, {"until": None})
                    state.access_cache.pop(tg, None)
                    await event.answer("🔴 Доступ закрыт")
                elif data.startswith("accon_"):
                    cancelled = await _open_member_access(tg, event.sender_id)
                    await event.answer(f"🟢 Доступ открыт ({cancelled})")
                else:
                    state.access_cache.pop(tg, None)
                await safe_edit(event, await render_member_access(member))
                return

            # ---- legacy data-mutating actions (owner only, gated above) ----
            ping_id = _cb_id(data)
            if data.startswith(("fav_", "read_", "gconfirm_", "gskip_")) and ping_id is None:
                await event.answer("Некорректная команда", alert=True)
                return
            if data.startswith("fav_"):
                await toggle_favorite(ping_id)
                await event.answer("Избранное обновлено")
            elif data.startswith("read_"):
                await mark_ping_read_db(ping_id)
                await event.answer("Отмечено как прочитанное")
                await event.delete()
            elif data.startswith("gconfirm_"):
                try:
                    result = await confirm_safe_giveaway_join(ping_id, actor="telegram_bot")
                    await event.answer(result.get("message") or "Joined")
                    await finalize_buttons(event, "✅ Участвую")
                except HTTPException as exc:
                    await event.answer(str(exc.detail), alert=True)
            elif data.startswith("gskip_"):
                await update_giveaway_candidate_status(ping_id, "skipped")
                await update_ping_meta(ping_id, giveaway_status="missed_unsubscribe", action_status="missed")
                await record_giveaway_action(ping_id, "skip", "skipped", "telegram_bot")
                await event.answer("Skipped")
                await event.delete()

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
                BotCommand("checks", "Найденные чеки"),
                BotCommand("search", "Поиск"),
                BotCommand("market", "Курсы"),
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
                    BotCommand("members", "Пользователи"),
                    BotCommand("access", "Доступ по расписанию"),
                    BotCommand("actions", "История действий по розыгрышам"),
                    BotCommand("restart", "Перезапустить мониторинг"),
                ]
                await bot_client(SetBotCommandsRequest(
                    scope=BotCommandScopePeer(peer=await bot_client.get_input_entity(int(ADMIN_ID))),
                    lang_code="",
                    commands=admin_cmds,
                ))
        except Exception:
            logger.debug("Could not set bot command menu", exc_info=True)
    except Exception:
        logger.exception("Bot startup failed")
