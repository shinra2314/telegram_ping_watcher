"""Telegram bot service: inline menus, slash commands, multi-user access keys."""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from telegram_ping_watcher import normalize_usernames

from .. import APP_VERSION
from .. import watch_settings as ws
from ..access_control import find_undoable, parse_repeat_rule, plan_undo, resolve_access, window_from_row
from ..analytics import build_analytics, build_detailed_analytics
from ..app_ctx import ADMIN_ID, API_HASH, API_ID, BOT_TOKEN, LOG_FILE, logger, settings, state
from ..bot_membership import (
    access_decision as _shared_access_decision,
    admin_chat_ids as _shared_admin_chat_ids,
    resolve_member_access as _shared_resolve_member_access,
)
from ..bot_notify import BOT_ASSETS_DIR, edit_pending_admin_card, execute_pending_broadcast
from ..bot_permissions import (
    ALL_FEATURES,
    ALL_NOTIFY,
    account_mentioned,
    accounts_allowed,
    allowed_pref_keys,
    dump_permissions,
    format_delay,
    full_permissions,
    has_feature,
    parse_delay_input,
    parse_permissions,
    permission_delay_minutes,
    render_permissions_summary,
    set_accounts,
    set_delay,
    set_features,
    set_notify,
    toggle_account,
    toggle_feature,
    toggle_notify,
)
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
from ..bot_connection import watch_bot_connection
from ..common import record_app_event, start_background_task
from ..converter import (
    Query, USAGE_HINT, parse_query, render_conversion, render_converter_home, supported_text,
)
from ..live import publish_live_event
from ..roulette import apply_checkin, checkin_moment, skip_today
from ..scan_engine import full_history_scan
from ..security import generate_access_key
from ..telegram_accounts import restart_monitoring, telegram_client_for_session
from .views import (
    DIV, SECTION_FEATURES, GiveawayFilter, account_label, expiry_from_days,
    fmt_dt, format_expiry, help_text, key_expired, main_menu_buttons, paginate,
    parse_expiry_days, parse_giveaway_filter,
)
from .keyboards import (
    KEY_PANEL_ACCOUNTS_PAGE, MON_FILTERS, analytics_keyboard, back_home, conversion_keyboard,
    converter_keyboard, feed_keyboard, giveaway_accounts_keyboard, giveaway_card_keyboard,
    giveaway_feed_keyboard, key_accounts_keyboard, key_delay_keyboard, key_delete_keyboard,
    key_expiry_keyboard, key_features_keyboard, key_members_keyboard,
    key_notify_keyboard, key_panel_keyboard, keys_keyboard, logs_keyboard, management_grid,
    market_keyboard, member_access_keyboard, member_card_keyboard, members_list_keyboard,
    ping_card_keyboard, restart_confirm_keyboard, roulette_panel_keyboard, scan_panel_keyboard,
    section_nav,
)
from .cards import (
    analytics_card, feed_badge, feed_header, giveaway_accounts_card, giveaway_card,
    giveaways_header, key_accounts_card,
    key_delay_card, key_delete_card, key_expiry_card, key_features_card, key_members_card,
    key_notify_card, key_panel_card, key_state_badge, keys_card,
    management_card, member_card, members_header, home_card, ping_card,
    restart_confirm_card, roulette_card, scan_card, summary_card,
)
from .emoji import enrich, resolve_custom_emoji_map


_ACCESS_DAY_NAMES = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}

# `key:<action>:<id>` callbacks that act on the key itself rather than on its
# grants — they are addressed by name, so they must never be read as a key id.
KEY_LIFECYCLE_ACTIONS = ("rm", "on", "del", "delgo")

# Fire-and-forget event writes from the bot-connection supervisor; kept alive
# here so the loop never drops a task mid-write.
_connection_event_tasks: set[asyncio.Task] = set()


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
    import database
    from database import (
        cancel_pending_sends,
        claim_pending_broadcast,
        create_access_window,
        create_bot_key,
        get_bot_key,
        set_bot_key_permissions,
        member_engagement_stats,
        set_member_engagement,
        create_disable_until_window,
        deactivate_access_window,
        delete_bot_key,
        delete_broadcast_messages,
        get_bot_key_by_secret,
        get_bot_member,
        get_broadcast_messages,
        get_access_audit,
        list_all_access_windows,
        get_giveaway_board,
        get_market_history,
        get_ping_by_id,
        get_pings,
        get_recent_giveaway_actions,
        giveaway_account_counts,
        list_access_windows,
        list_bot_key_members,
        list_bot_keys,
        list_bot_members,
        mark_ping_read as mark_ping_read_db,
        record_access_audit,
        set_access_window_active,
        set_bot_key_expiry,
        set_bot_key_label,
        set_bot_key_revoked,
        set_bot_key_role,
        set_bot_member_blocked,
        set_bot_member_prefs,
        set_member_default_policy,
        set_setting,
        toggle_favorite,
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
        state.bot_offline_since = None

        # Telethon stops reconnecting after `connection_retries` failures and
        # leaves the client dead. Outgoing sends recover on their own
        # (ensure_bot_connected), incoming updates do not — so without this
        # supervisor a network flap silently freezes every command and button
        # until the next notification reconnects us. See bot_connection.py.
        start_background_task(
            "bot-connection",
            watch_bot_connection(
                bot_client,
                should_stop=lambda: state.shutting_down,
                on_change=_record_bot_connection,
                logger=logger,
            ),
        )

        # Resolve the Aperture custom-emoji pack (best-effort; empty → plain emoji).
        if settings.bot_custom_emoji_set:
            state.custom_emoji_map = await resolve_custom_emoji_map(bot_client, settings.bot_custom_emoji_set)
            # The map carries a VS16 variant per emoticon, so its key count is
            # ~2x the pack size — report distinct document ids as the glyph count.
            glyphs = len(set(state.custom_emoji_map.values()))
            logger.info("Custom emoji pack '%s': %d glyphs resolved (%d match keys)",
                        settings.bot_custom_emoji_set, glyphs, len(state.custom_emoji_map))

        bot_pending_inputs = state.bot_pending_inputs

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

        async def safe_edit(event, *args, **kwargs) -> None:
            """Edit the callback message, ignoring 'not modified' errors.

            Injects Aperture custom emoji into the text when a pack is resolved;
            with no pack the text is sent unchanged (normal Markdown).
            """
            if (args and isinstance(args[0], str) and state.custom_emoji_map
                    and "formatting_entities" not in kwargs):
                clean, ents = enrich(args[0], state.custom_emoji_map)
                if ents is not None:
                    args = (clean,) + tuple(args[1:])
                    kwargs["formatting_entities"] = ents
                    kwargs["parse_mode"] = None
            try:
                await event.edit(*args, **kwargs)
            except MessageNotModifiedError:
                await event.answer()

        async def respond_rich(event, text, **kwargs):
            """``event.respond`` with Aperture custom emoji injected when available."""
            if state.custom_emoji_map and "formatting_entities" not in kwargs:
                clean, ents = enrich(text, state.custom_emoji_map)
                if ents is not None:
                    return await event.respond(clean, formatting_entities=ents,
                                               parse_mode=None, **kwargs)
            return await event.respond(text, **kwargs)

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
                        await event.respond(FEATURE_DENIED, buttons=main_menu_buttons(role, perms))
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

        async def render_analytics(tab: str = "sum"):
            """Analytics report page. Both queries are read-only aggregates."""
            analytics, detailed = await asyncio.gather(build_analytics(), build_detailed_analytics())
            return analytics_card(tab, analytics=analytics, detailed=detailed), analytics_keyboard(tab)

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

        def visible_pings(rows: list[dict], perms: dict, limit: int) -> list[dict]:
            """Drop rows about accounts this key was not granted, then trim."""
            return [r for r in rows if accounts_allowed(perms, r.get("mentions"))][:limit]

        def giveaway_accounts(perms: dict) -> list[str]:
            """Tracked usernames this key may see, '@' stripped.

            The account filter addresses these by index, so render and click must
            rebuild the exact same list — hence one helper, no local sorting.
            """
            names = [name.lstrip("@") for name in (state.ping_usernames or [])]
            whitelist = {n.lower() for n in (perms.get("accounts") or [])}
            return [n for n in names if not whitelist or n.lower() in whitelist]

        async def render_giveaways(
            filt: Optional[GiveawayFilter] = None,
            perms: Optional[dict] = None,
        ):
            perms = perms or full_permissions()
            filt = filt or GiveawayFilter()
            page = max(1, filt.page)
            accounts = giveaway_accounts(perms)
            account = accounts[filt.account] if 0 <= filt.account < len(accounts) else None
            # Fetch the whole prefix up to the requested page (+1 row to detect a
            # next one). Any filter applied below eats rows, so widen the window
            # whenever one is active — an account-scoped key always has one.
            narrowed = bool(perms.get("accounts")) or filt.wins or account is not None
            window = FEED_PAGE_SIZE * page + 1
            board = await get_giveaway_board(limit=window * (8 if narrowed else 1), sort=filt.db_sort)
            stats = board.get("stats") or {}
            need = (board.get("buckets") or {}).get("need_action") or []
            need = [r for r in need if accounts_allowed(perms, r.get("mentions"))]
            if filt.wins:
                need = [r for r in need if r.get("is_win")]
            if account is not None:
                need = [r for r in need if account_mentioned(r.get("mentions"), account)]
            rows, has_more = paginate(need, page, FEED_PAGE_SIZE)
            items = []
            for r in rows:
                when = fmt_dt(r.get("date") if filt.sort == "p" else r.get("detected_at"))
                badge = "🗑" if r.get("deleted_at") else feed_badge(r.get("priority_label"))
                label = f"{badge} {when} {r.get('chat') or '?'}"
                items.append((int(r["id"]), label[:48]))
            # Header shows the real queue depth, not just what fits on this page.
            total = (
                len(need) if narrowed
                else int((board.get("bucket_totals") or {}).get("need_action") or len(need))
            )
            return (
                giveaways_header(stats, total, filt, account_label(accounts, filt.account)),
                giveaway_feed_keyboard(items, page, has_more, state=filt, accounts=accounts),
            )

        async def render_giveaway_accounts(
            filt: Optional[GiveawayFilter] = None,
            page: int = 0,
            perms: Optional[dict] = None,
        ):
            """Account picker: every tracked username with its open wins/giveaways."""
            perms = perms or full_permissions()
            filt = filt or GiveawayFilter()
            accounts = giveaway_accounts(perms)
            counts = await giveaway_account_counts()
            return (
                giveaway_accounts_card(counts, accounts),
                giveaway_accounts_keyboard(accounts, counts, state=filt, page=page),
            )

        async def open_giveaway_view(
            ping_id: int,
            perms: Optional[dict] = None,
            filt: Optional[GiveawayFilter] = None,
        ):
            ping = await get_ping_by_id(ping_id)
            if not ping:
                return None
            if perms is not None and not accounts_allowed(perms, ping.get("mentions")):
                return None
            return giveaway_card(ping), giveaway_card_keyboard(ping_id, filt or GiveawayFilter())

        async def render_management():
            return management_card(), management_grid()

        async def render_roulette_panel():
            cfg = await ws.load_roulette_settings()
            return roulette_card(cfg, datetime.now()), roulette_panel_keyboard(cfg)

        def render_scan_panel():
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            if state.last_scan_status:
                last_scan = f"{last_scan} · {state.last_scan_status}"
            running = state.scan_lock.locked()
            status = dict(state.scan_status)
            status["running"] = running
            return scan_card(status, last_scan), scan_panel_keyboard(running)

        def start_scan_if_idle() -> bool:
            if state.scan_lock.locked():
                return False
            asyncio.create_task(full_history_scan())
            return True

        async def render_members():
            members = await list_bot_members()
            items = []
            for m in members:
                tg = int(m["tg_id"])
                uname = f"@{m['tg_username']}" if m.get("tg_username") else ""
                dot_ = "🚫" if m.get("blocked") else "🟢"
                label = f"{dot_} {m.get('name') or tg} {uname}".strip()
                items.append((tg, label[:48]))
            return members_header(len(members)), members_list_keyboard(items)

        async def open_member_view(tg: int):
            member = await get_bot_member(tg)
            if not member:
                return None
            rows = await list_access_windows(tg)
            decision = resolve_access(member, [window_from_row(r) for r in rows], datetime.now(timezone.utc))
            engagement = await member_engagement_stats(tg)
            return member_card(member, decision.allowed, engagement), member_card_keyboard(tg, bool(member.get("blocked")))

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

        async def latest_snapshot() -> Optional[dict]:
            """Newest market snapshot, or None while the market loop is cold."""
            rows = await get_market_history(limit=1)
            return rows[0] if rows else None

        async def render_converter():
            return render_converter_home(await latest_snapshot()), converter_keyboard()

        async def render_conversion_view(query: Query):
            snapshot = await latest_snapshot()
            return (
                render_conversion(snapshot, query),
                conversion_keyboard(query.amount, query.src, query.dst),
            )

        async def render_home(role: str) -> str:
            analytics = await build_analytics()
            board = await get_giveaway_board(limit=10)
            urgent = int((board.get("bucket_totals") or {}).get("need_action") or 0)
            last_scan = fmt_dt(state.last_scan_finished_at.isoformat() if state.last_scan_finished_at else None)
            if state.last_scan_status:
                last_scan = f"{last_scan} · {state.last_scan_status}"
            return home_card(
                role=role,
                new_pings=analytics["new_pings"],
                urgent=urgent,
                accounts_online=analytics["accounts_online"],
                accounts_total=len(state.accounts_state),
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

        # plain `menu_*` callback -> feature code its key must grant
        MENU_CALLBACK_FEATURES = {
            "menu_stats": "stats",
            "menu_summary": "stats",
            "menu_status": "status",
            "menu_giveaways": "giveaways",
            "menu_recent": "recent",
            "menu_market": "market",
        }

        FEED_LABELS = dict(MON_FILTERS)
        FEED_PAGE_SIZE = 8

        async def render_feed(active: str = "all", page: int = 1, perms: Optional[dict] = None):
            perms = perms or full_permissions()
            if active not in FEED_LABELS:
                active = "all"
            page = max(1, page)
            # Fetch one extra row to know whether an older page exists. An
            # account-scoped key must page over the *filtered* list, so it reads
            # the whole prefix from the top through a wider window — offsetting
            # the raw query instead would skip every row the filter removed.
            scoped = bool(perms.get("accounts"))
            if scoped:
                rows = await get_pings(
                    limit=(FEED_PAGE_SIZE * page + 1) * 8, offset=0, chat_type=active
                )
                rows = [r for r in rows if accounts_allowed(perms, r.get("mentions"))]
                rows, has_more = paginate(rows, page, FEED_PAGE_SIZE)
            else:
                rows = await get_pings(
                    limit=FEED_PAGE_SIZE + 1,
                    offset=(page - 1) * FEED_PAGE_SIZE,
                    chat_type=active,
                )
                has_more = len(rows) > FEED_PAGE_SIZE
                rows = rows[:FEED_PAGE_SIZE]
            items = []
            for r in rows:
                hhmm = fmt_dt(r.get("detected_at"))[-5:]
                badge = "🗑" if r.get("deleted_at") else feed_badge(r.get("priority_label"))
                label = f"{badge} {hhmm} {r.get('chat') or '?'}"
                items.append((int(r["id"]), label[:48]))
            return feed_header(FEED_LABELS[active], len(rows)), feed_keyboard(items, active, page, has_more)

        async def open_ping_view(ping_id: int, is_admin: bool, perms: Optional[dict] = None):
            ping = await get_ping_by_id(ping_id)
            if not ping:
                return None
            if perms is not None and not accounts_allowed(perms, ping.get("mentions")):
                return None
            return ping_card(ping), ping_card_keyboard(ping_id, is_admin)

        async def render_keys():
            # Revoked keys stay listed — the panel is where they get restored
            # or deleted for good.
            keys = await list_bot_keys(include_revoked=True)
            items = [
                (int(k["id"]), f"{key_state_badge(k)} #{k['id']} {k.get('label') or 'без метки'}"[:40])
                for k in keys
            ]
            return keys_card(keys), keys_keyboard(items)

        # ---- per-key control panel (owner only) ----------------------------
        def grantable_accounts() -> list[str]:
            """Tracked usernames, '@' stripped — the whitelist the grant addresses.

            Rebuilt identically on render and on click, because account toggles
            travel as an index into this list (callback data is capped at 64 B).
            """
            return [name.lstrip("@") for name in (state.ping_usernames or [])]

        async def render_key_panel(key_id: int):
            key = await get_bot_key(key_id)
            if not key:
                return None
            perms = parse_permissions(key.get("permissions"))
            accounts = grantable_accounts()
            return key_panel_card(key, perms, accounts), key_panel_keyboard(key, perms, len(accounts))

        async def render_key_panel_into(event, key_id: int) -> None:
            """Re-render the panel in place after an edit; the key may be gone."""
            screen = await render_key_panel(key_id)
            if screen is None:
                text, kb = await render_keys()
                await safe_edit(event, text, buttons=kb)
                return
            text, kb = screen
            await safe_edit(event, text, buttons=kb)

        def key_link_text(key: dict) -> str:
            """Secret + invite link for one key, ready to forward."""
            secret = key.get("secret") or ""
            link = f"https://t.me/{bot_username}?start={secret}" if bot_username else ""
            body = f"🔑 **Ключ #{key.get('id')}** · {key.get('label') or '—'}\n{DIV}\n🔐 `{secret}`"
            body += f"\n🔗 {link}" if link else "\n__Передайте ключ вручную:__ `/redeem <ключ>`"
            if key.get("revoked"):
                body += "\n\n🚫 __Ключ отозван — ссылка не сработает, пока его не вернуть.__"
            elif key_expired(key.get("expires_at")):
                body += "\n\n⌛ __Срок ключа истёк — продлите его в панели.__"
            return body

        async def save_key_permissions(key_id: int, perms: dict) -> None:
            await set_bot_key_permissions(key_id, dump_permissions(perms))
            await record_app_event("INFO", "bot", "Bot key grants updated", {"id": key_id, "via": "bot"})

        # ---- settings menus (admin) + personal prefs (members) -------------
        PENDING_TTL_SECONDS = 300
        SETTINGS_BACK = [Button.inline("⬅️ Назад", b"st")]
        PF_TOGGLES = {
            "pf_mu": "muted",
            "pf_me": "mentions",
            "pf_gw": "giveaways",
            "pf_wn": "wins",
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
            "min_score": "Пришлите минимальный score розыгрышей (0–100, 0 — показывать все).",
            "convert": f"Что пересчитать?\n{USAGE_HINT}",
            "key_delay": "Пришлите задержку в минутах (0–1440). `0` — отправлять сразу.",
            "key_label": "Пришлите метку ключа — как вы будете его узнавать (до 40 символов).",
            "key_expiry": "Пришлите срок в днях (0–365). `0` — бессрочно.",
            "roulette_time": "Пришлите время проклика последнего аккаунта: `21:47`.",
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

        async def save_roulette_from_bot(cfg: dict) -> None:
            await set_setting("roulette", cfg)
            await record_app_event("INFO", "roulette", "Roulette reminder updated", {"via": "bot", **cfg})
            await publish_live_event("settings-updated", {"scope": "roulette"})

        async def roulette_checkin(event, when: datetime) -> None:
            """Record the reported click time and show the refreshed panel."""
            cfg = apply_checkin(await ws.load_roulette_settings(), when)
            await save_roulette_from_bot(cfg)
            text, kb = await render_roulette_panel()
            await event.respond(f"✅ Проклик в {cfg['time']} записан.\n\n{text}", buttons=kb)

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
                [Button.inline(
                    "🛡 Модерация рассылок: вкл" if notif.get("moderation_mode") == "moderated" else "📤 Модерация рассылок: авто",
                    b"st_n_md",
                )],
                [Button.inline("🕘 Часы тишины…", b"st_n_qt"), Button.inline("⏱ Кулдаун…", b"st_n_cd")],
                [Button.inline(f"{mark(digest_cfg.get('enabled'))} Дайджест", b"st_d_en"),
                 Button.inline("🕘 Время дайджеста…", b"st_d_tm")],
                SETTINGS_BACK,
            ]
            return render_notification_settings_text(notif, digest_cfg), buttons

        PF_LABELS = {
            "mentions": ("Упоминания", b"pf_me"),
            "giveaways": ("Розыгрыши", b"pf_gw"),
            "wins": ("Победы", b"pf_wn"),
            "digest": ("Дайджест", b"pf_dg"),
        }

        def member_prefs_menu(prefs: dict, perms: Optional[dict] = None) -> tuple[str, list[list[Button]]]:
            """Personal toggles, limited to the notification types the key granted."""
            perms = perms or full_permissions()
            allowed = allowed_pref_keys(perms)
            toggles = [
                Button.inline(f"{'✅' if prefs.get(code) else '🔕'} {PF_LABELS[code][0]}", PF_LABELS[code][1])
                for code in allowed
            ]
            buttons = [[Button.inline("🔔 Включить всё" if prefs.get("muted") else "🔕 Отключить всё", b"pf_mu")]]
            buttons += [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
            if "giveaways" in allowed:
                buttons.append([Button.inline("🎯 Мин. score розыгрышей…", b"pf_sc")])
            buttons.append([Button.inline("⬅️ Меню", b"menu_main")])
            return render_member_prefs_text(prefs, allowed, perms.get("accounts")), buttons

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
            if data in ("st_n_en", "st_n_gw", "st_n_wn", "st_n_md", "st_n_q"):
                notif = await ws.load_notification_settings()
                if data == "st_n_en":
                    notif["enabled"] = not notif.get("enabled", True)
                elif data == "st_n_gw":
                    notif["include_giveaways"] = not notif.get("include_giveaways", True)
                elif data == "st_n_wn":
                    notif["include_wins"] = not notif.get("include_wins", True)
                elif data == "st_n_md":
                    notif["moderation_mode"] = "moderated" if notif.get("moderation_mode") != "moderated" else "auto"
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
            kind = pending.get("kind")
            raw = (event.message.text or "").strip()
            if kind == "convert":
                # Open to every role — the converter is gated by the `market`
                # feature at the button/command, not by ownership.
                query = parse_query(raw)
                if query is None:
                    await event.respond(
                        f"❌ **Не понял запрос.**\n{USAGE_HINT}\n{DIV}\n{supported_text()}",
                        buttons=converter_keyboard(),
                    )
                    return
                text, buttons = await render_conversion_view(query)
                await event.respond(text, buttons=buttons)
                return
            if kind == "min_score":
                # Personal member setting — the only pending input open to non-admins.
                member = await get_bot_member(event.sender_id)
                if not member:
                    await event.respond("Личные настройки недоступны.")
                    return
                try:
                    value = max(0, min(100, int(raw)))
                except ValueError:
                    await event.respond("❌ Нужно число 0–100 (0 — показывать все розыгрыши).")
                    return
                prefs = parse_member_prefs(member.get("notification_prefs"))
                prefs["min_score"] = value
                await set_bot_member_prefs(event.sender_id, prefs)
                text, buttons = member_prefs_menu(prefs)
                await event.respond(f"✅ Мин. score: {value if value else 'любой'}\n\n{text}", buttons=buttons)
                return
            if role != "admin":
                return
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
            if kind in ("key_delay", "key_label", "key_expiry"):
                try:
                    key_id = int(pending.get("scope") or 0)
                except (TypeError, ValueError):
                    key_id = 0
                key = await get_bot_key(key_id) if key_id else None
                if not key:
                    await event.respond("❌ Ключ не найден — возможно, он был удалён.")
                    return
                if kind == "key_delay":
                    minutes = parse_delay_input(raw)
                    if minutes is None:
                        await event.respond("❌ Нужно целое число минут от 0 до 1440.")
                        return
                    perms = set_delay(parse_permissions(key.get("permissions")), minutes)
                    await save_key_permissions(key_id, perms)
                    await event.respond(
                        f"✅ Задержка: {format_delay(minutes)}\n\n{key_delay_card(perms)}",
                        buttons=key_delay_keyboard(key_id, perms),
                    )
                    return
                if kind == "key_label":
                    label = raw.strip()[:40]
                    if not label:
                        await event.respond("❌ Метка не может быть пустой.")
                        return
                    await set_bot_key_label(key_id, label)
                    await record_app_event(
                        "INFO", "bot", "Bot key label changed",
                        {"id": key_id, "label": label, "via": "bot"},
                    )
                    screen = await render_key_panel(key_id)
                    if screen:
                        text, kb = screen
                        await event.respond(f"✅ Метка: **{label}**\n\n{text}", buttons=kb)
                    return
                days = parse_expiry_days(raw)
                if days is None:
                    await event.respond("❌ Нужно целое число дней от 0 до 365 (`0` — бессрочно).")
                    return
                expires_at = expiry_from_days(days)
                await set_bot_key_expiry(key_id, expires_at)
                await record_app_event(
                    "INFO", "bot", "Bot key expiry changed",
                    {"id": key_id, "expires_at": expires_at, "via": "bot"},
                )
                key = await get_bot_key(key_id) or key
                await event.respond(
                    f"✅ Срок: {format_expiry(expires_at)}\n\n{key_expiry_card(key)}",
                    buttons=key_expiry_keyboard(key_id, key),
                )
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

            if kind == "roulette_time":
                moment = checkin_moment(datetime.now(), raw)
                if moment is None:
                    await event.respond("❌ Формат: `21:47`. Попробуйте ещё раз через меню.")
                    return
                await roulette_checkin(event, moment)
                return

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
                buttons=main_menu_buttons(role),
            )
            if is_new_member and role != "admin":
                # First-time onboarding: let the member tune notifications right away.
                prefs = parse_member_prefs(None)
                text, buttons = member_prefs_menu(prefs)
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
            home = await render_home(role)
            if welcome_banner.exists():
                try:
                    await respond_rich(event, home, buttons=main_menu_buttons(role), file=str(welcome_banner))
                    return
                except Exception:
                    logger.warning("Failed to send welcome banner, falling back to text", exc_info=True)
            await respond_rich(event, home, buttons=main_menu_buttons(role))

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
            home = await render_home(role)
            banner = BOT_ASSETS_DIR / "welcome.png"
            if banner.exists():
                try:
                    await respond_rich(event, home, buttons=main_menu_buttons(role), file=str(banner))
                    return
                except Exception:
                    logger.warning("menu banner failed, text fallback", exc_info=True)
            await respond_rich(event, home, buttons=main_menu_buttons(role))

        @bot_client.on(events.NewMessage(pattern="/settings"))
        @viewer_only()
        async def settings_handler(event, role, perms):
            if role == "admin":
                text, buttons = settings_root_menu()
                await event.respond(text, buttons=buttons)
                return
            member = await get_bot_member(event.sender_id)
            if not member:
                await event.respond("Личные настройки недоступны.")
                return
            text, buttons = member_prefs_menu(parse_member_prefs(member.get("notification_prefs")), perms)
            await event.respond(text, buttons=buttons)

        @bot_client.on(events.NewMessage(pattern="/help"))
        @viewer_only()
        async def help_handler(event, role, perms):
            await event.respond(help_text(role, perms), buttons=section_nav(b"menu_help"))

        @bot_client.on(events.NewMessage(pattern="/stats"))
        @viewer_only("stats")
        async def stats_handler(event, role, perms):
            await event.respond(await render_stats(), buttons=section_nav(b"menu_stats"))

        @bot_client.on(events.NewMessage(pattern="/analytics"))
        @viewer_only("analytics")
        async def analytics_handler(event, role, perms):
            text, kb = await render_analytics()
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/status"))
        @viewer_only("status")
        async def status_handler(event, role, perms):
            await event.respond(await render_status(), buttons=section_nav(b"menu_status"))

        @bot_client.on(events.NewMessage(pattern="/giveaways"))
        @viewer_only("giveaways")
        async def giveaways_handler(event, role, perms):
            text, kb = await render_giveaways(perms=perms)
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/recent"))
        @viewer_only("recent")
        async def recent_handler(event, role, perms):
            text, kb = await render_feed("all", perms=perms)
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/latest"))
        @viewer_only("recent")
        async def latest_handler(event, role, perms):
            text, kb = await render_feed("all", perms=perms)
            await event.respond(text, buttons=kb, link_preview=False)

        @bot_client.on(events.NewMessage(pattern="/search"))
        @viewer_only("search")
        async def search_handler(event, role, perms):
            parts = event.message.text.split(" ", 1)
            if len(parts) < 2:
                await event.respond("Укажите текст: `/search TON`")
                return
            # Over-fetch when the key is account-scoped so the page still fills.
            found = await get_pings(limit=5 if not perms.get("accounts") else 40, search=parts[1])
            rows = visible_pings(found, perms, 5)
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

        @bot_client.on(events.NewMessage(pattern="/market"))
        @viewer_only("market")
        async def market_handler(event, role, perms):
            await event.respond(await render_market(), buttons=market_keyboard())

        @bot_client.on(events.NewMessage(pattern=r"/conv(?:ert)?(?:\s+(.+))?"))
        @viewer_only("market")
        async def convert_handler(event, role, perms):
            raw = (event.pattern_match.group(1) or "").strip()
            if not raw:
                text, kb = await render_converter()
                await event.respond(text, buttons=kb)
                return
            query = parse_query(raw)
            if query is None:
                await event.respond(
                    f"❌ **Не понял запрос.**\n{USAGE_HINT}\n{DIV}\n{supported_text()}",
                    buttons=converter_keyboard(),
                )
                return
            text, kb = await render_conversion_view(query)
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
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
            await event.respond(
                "📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-3500:] + "\n```",
                buttons=logs_keyboard(),
            )

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
            started = start_scan_if_idle()
            note = "🔄 **Сканирование запущено.**" if started else "⏳ Сканирование уже идёт."
            text, kb = render_scan_panel()
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
            screen = await render_key_panel(int(key["id"]))
            if screen:
                text, kb = screen
                await event.respond("⚙️ **Настройте ключ перед отправкой**\n\n" + text, buttons=kb)

        @bot_client.on(events.NewMessage(pattern="/keys"))
        @safe
        async def keys_handler(event):
            if await deny_non_admin(event):
                return
            text, kb = await render_keys()
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
                await roulette_checkin(event, moment)
                return
            text, kb = await render_roulette_panel()
            await event.respond(text, buttons=kb)

        @bot_client.on(events.NewMessage(func=lambda e: bool(e.is_private and e.message and e.message.text and not e.message.text.startswith("/"))))
        @safe
        async def freeform_handler(event):
            role = await bot_role(event.sender_id)
            if role is not None:
                # Members' free text only feeds pending settings inputs; the owner
                # may also answer a roulette nudge with a bare time.
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
                # A bare `21:47` answers the roulette nudge, but only while one
                # is actually waiting — otherwise a message that merely looks
                # like a time would be swallowed here.
                if role == "admin":
                    cfg = await ws.load_roulette_settings()
                    if cfg.get("awaiting"):
                        moment = checkin_moment(datetime.now(), event.message.text or "")
                        if moment is not None:
                            await roulette_checkin(event, moment)
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
            role, perms = await resolve_member_access(event.sender_id)
            if role is None:
                await event.answer("Доступ запрещён", alert=True)
                return

            def feature_ok(code: str) -> bool:
                return role == "admin" or has_feature(perms, code)

            # ---- converter: landing, preset pairs, free-text input ----
            if data == "cv" or data.startswith("cv:"):
                if not feature_ok("market"):
                    await event.answer("Раздел закрыт владельцем", alert=True)
                    return
                if data == "cv:in":
                    await prompt_pending(event, "convert")
                    return
                seg = data.split(":")
                if len(seg) == 5 and seg[1] == "p":
                    try:
                        amount = float(seg[2])
                    except ValueError:
                        amount = 1.0
                    text, kb = await render_conversion_view(Query(amount, seg[3], seg[4]))
                else:
                    text, kb = await render_converter()
                await safe_edit(event, text, buttons=kb)
                return

            # ---- roulette reminder: panel, check-in, skip, toggle ----
            if data == "rl" or data.startswith("rl:"):
                if role != "admin":
                    await event.answer("Только владелец", alert=True)
                    return
                action = data.partition(":")[2]
                if action == "in":
                    await prompt_pending(event, "roulette_time")
                    return
                now = datetime.now()
                cfg = await ws.load_roulette_settings()
                if action == "now":
                    cfg = apply_checkin(cfg, now)
                    await save_roulette_from_bot(cfg)
                    await event.answer(f"Записал: {cfg['time']}")
                elif action == "skip":
                    cfg = skip_today(cfg, now)
                    await save_roulette_from_bot(cfg)
                    await event.answer("Сегодня больше не напомню")
                elif action == "tog":
                    cfg = dict(cfg)
                    cfg["enabled"] = not cfg.get("enabled")
                    await save_roulette_from_bot(cfg)
                    await event.answer("Напоминание включено" if cfg["enabled"] else "Напоминание выключено")
                await safe_edit(event, roulette_card(cfg, now), buttons=roulette_panel_keyboard(cfg))
                return

            # ---- structured `domain:action:arg` callbacks ----
            if ":" in data:
                seg = data.split(":")
                if seg[0] == "an":
                    if not feature_ok("analytics"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    text, kb = await render_analytics(seg[1] if len(seg) > 1 else "sum")
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "mon" and len(seg) >= 2 and seg[1] == "feed":
                    active = seg[2] if len(seg) > 2 else "all"
                    if not feature_ok("recent"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    try:
                        page = int(seg[3]) if len(seg) > 3 else 1
                    except ValueError:
                        page = 1
                    text, kb = await render_feed(active, page, perms=perms)
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "mon" and len(seg) >= 3 and seg[1] == "open":
                    if not feature_ok("recent"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    res = await open_ping_view(pid, role == "admin", perms)
                    if res is None:
                        await event.answer("Запись не найдена", alert=True)
                        return
                    text, kb = res
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "ping" and len(seg) >= 3 and seg[1] in ("fav", "read"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    if seg[1] == "fav":
                        await toggle_favorite(pid)
                        await event.answer("Избранное обновлено")
                    else:
                        await mark_ping_read_db(pid)
                        await event.answer("Отмечено как прочитанное")
                    res = await open_ping_view(pid, True)
                    if res is not None:
                        text, kb = res
                        await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "bc" and len(seg) >= 3 and seg[1] in ("ok", "no"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    try:
                        pb_id = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    status = "approved" if seg[1] == "ok" else "rejected"
                    row = await claim_pending_broadcast(pb_id, status, event.sender_id)
                    if row is None:
                        await event.answer("Уже обработано")
                        return
                    if seg[1] == "ok":
                        count, token = await execute_pending_broadcast(row)
                        await edit_pending_admin_card(row, f"✅ Разослано друзьям ({count})", token=token, delivered_count=count)
                        await event.answer(f"📣 Разослано: {count}")
                        await record_app_event("INFO", "broadcast", "Pending broadcast approved", {"id": pb_id, "delivered": count})
                    else:
                        footer = "🚫 Рассылка отклонена"
                        bc_token = row.get("bc_token") or None
                        premium_count = 0
                        if bc_token:
                            premium_count = len(await get_broadcast_messages(bc_token))
                            if premium_count:
                                footer += f" · ⚡ премиум уже получили: {premium_count}"
                        await edit_pending_admin_card(row, footer, token=bc_token if premium_count else None, delivered_count=premium_count)
                        await event.answer("Отклонено")
                        await record_app_event("INFO", "broadcast", "Pending broadcast rejected", {"id": pb_id})
                    return
                if seg[0] == "bcm" and len(seg) >= 3 and seg[1] in ("in", "skip"):
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    action = "joined" if seg[1] == "in" else "skipped"
                    await set_member_engagement(event.sender_id, pid, action)
                    await record_app_event("INFO", "engagement", "Member engagement recorded", {"tg_id": event.sender_id, "ping_id": pid, "action": action})
                    chosen = "✅ Участвую" if action == "joined" else "⏭ Пропустил"
                    with suppress(Exception):
                        # Keep the link button, freeze the choice row on the member's copy.
                        ping = await get_ping_by_id(pid)
                        new_buttons: list[list[Button]] = []
                        link = (ping or {}).get("link")
                        if link and not str(link).startswith("нет "):
                            new_buttons.append([Button.url("Открыть в Telegram", link)])
                        other = "bcm:skip" if action == "joined" else "bcm:in"
                        new_buttons.append([
                            Button.inline(f"● {chosen}", data=b"noop"),
                            Button.inline("изменить", data=f"{other}:{pid}"),
                        ])
                        await event.edit(buttons=new_buttons)
                    await event.answer("Записал: участвуете 🎯" if action == "joined" else "Ок, пропускаем")
                    return
                if seg[0] == "gw" and len(seg) >= 2 and seg[1] == "feed":
                    # Legacy page-only callback, still live on older messages.
                    if not feature_ok("giveaways"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    try:
                        page = int(seg[2]) if len(seg) > 2 else 1
                    except ValueError:
                        page = 1
                    text, kb = await render_giveaways(GiveawayFilter(page=max(1, page)), perms=perms)
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "gw" and len(seg) >= 2 and seg[1] == "f":
                    if not feature_ok("giveaways"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    text, kb = await render_giveaways(parse_giveaway_filter(seg[2:]), perms=perms)
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "gw" and len(seg) >= 2 and seg[1] == "a":
                    # `gw:a:<sort>:<wins>:<account>:<picker page>` — pick an account.
                    if not feature_ok("giveaways"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    filt = parse_giveaway_filter(seg[2:5])
                    try:
                        picker_page = int(seg[5]) if len(seg) > 5 else 0
                    except ValueError:
                        picker_page = 0
                    text, kb = await render_giveaway_accounts(filt, picker_page, perms=perms)
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] == "gw" and len(seg) >= 3 and seg[1] == "open":
                    if not feature_ok("giveaways"):
                        await event.answer("Раздел закрыт владельцем", alert=True)
                        return
                    try:
                        pid = int(seg[2])
                    except ValueError:
                        await event.answer("Некорректная команда", alert=True)
                        return
                    # The list state rides along so ⬅️ returns to the same filter.
                    res = await open_giveaway_view(pid, perms, parse_giveaway_filter(seg[3:]))
                    if res is None:
                        await event.answer("Розыгрыш не найден", alert=True)
                        return
                    text, kb = res
                    await safe_edit(event, text, buttons=kb, link_preview=False)
                    return
                if seg[0] in ("scan", "key"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    if seg[0] == "scan" and seg[1] == "start":
                        started = start_scan_if_idle()
                        await event.answer("🔄 Скан запущен" if started else "Скан уже идёт")
                        text, kb = render_scan_panel()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "key" and seg[1] not in KEY_LIFECYCLE_ACTIONS:
                        # `key:<id>` root, `key:<section>:<id>[:<arg>]` for the rest.
                        try:
                            key_id = int(seg[1]) if len(seg) == 2 else int(seg[2])
                        except (ValueError, IndexError):
                            await event.answer("Некорректная команда", alert=True)
                            return
                        key = await get_bot_key(key_id)
                        if not key:
                            await event.answer("Ключ не найден", alert=True)
                            return
                        perms = parse_permissions(key.get("permissions"))
                        section = seg[1] if len(seg) > 2 else ""
                        arg = seg[3] if len(seg) > 3 else ""
                        accounts = grantable_accounts()

                        if section == "link":
                            await event.respond(key_link_text(key), link_preview=False)
                            await event.answer()
                            return

                        if section == "name":
                            await prompt_pending(event, "key_label", scope=str(key_id))
                            return

                        if section == "role":
                            new_role = "viewer" if (key.get("role") or "viewer") == "premium" else "premium"
                            await set_bot_key_role(key_id, new_role)
                            await record_app_event(
                                "INFO", "bot", "Bot key role changed",
                                {"id": key_id, "role": new_role, "via": "bot"},
                            )
                            await event.answer("⚡ Премиум" if new_role == "premium" else "👁 Просмотр")
                            await render_key_panel_into(event, key_id)
                            return

                        if section == "m":
                            members = await list_bot_key_members(key_id)
                            items = [
                                (
                                    int(m["tg_id"]),
                                    f"{'🚫' if m.get('blocked') else '🟢'} "
                                    f"{m.get('name') or m.get('tg_username') or m['tg_id']}",
                                )
                                for m in members
                            ]
                            await safe_edit(
                                event, key_members_card(key, members),
                                buttons=key_members_keyboard(key_id, items),
                            )
                            return

                        if section == "e":
                            if arg == "_x":
                                await prompt_pending(event, "key_expiry", scope=str(key_id))
                                return
                            if arg:
                                days = 0 if arg == "_off" else (int(arg) if arg.isdigit() else 0)
                                expires_at = expiry_from_days(days)
                                await set_bot_key_expiry(key_id, expires_at)
                                await record_app_event(
                                    "INFO", "bot", "Bot key expiry changed",
                                    {"id": key_id, "expires_at": expires_at, "via": "bot"},
                                )
                                key = await get_bot_key(key_id) or key
                                await event.answer(f"Срок: {format_expiry(expires_at)}")
                            await safe_edit(event, key_expiry_card(key), buttons=key_expiry_keyboard(key_id, key))
                            return

                        if section == "f" and arg:
                            if arg == "_all":
                                perms = set_features(perms, ALL_FEATURES)
                            elif arg == "_none":
                                perms = set_features(perms, [])
                            else:
                                perms = toggle_feature(perms, arg)
                            await save_key_permissions(key_id, perms)
                            await event.answer("Сохранено")
                        elif section == "n" and arg:
                            if arg == "_all":
                                perms = set_notify(perms, ALL_NOTIFY)
                            elif arg == "_none":
                                perms = set_notify(perms, [])
                            else:
                                perms = toggle_notify(perms, arg)
                            await save_key_permissions(key_id, perms)
                            await event.answer("Сохранено")
                        elif section == "a" and arg:
                            if arg == "_all":
                                perms = set_accounts(perms, [])
                                await save_key_permissions(key_id, perms)
                                await event.answer("Все аккаунты")
                            elif arg.startswith("_p"):
                                page = int(arg[2:]) if arg[2:].isdigit() else 0
                                await safe_edit(
                                    event, key_accounts_card(perms, accounts),
                                    buttons=key_accounts_keyboard(key_id, perms, accounts, page),
                                )
                                return
                            else:
                                index = int(arg) if arg.isdigit() else -1
                                if not 0 <= index < len(accounts):
                                    await event.answer("Список аккаунтов изменился — откройте заново", alert=True)
                                    return
                                name = accounts[index]
                                # An empty whitelist means "all", so materialise it
                                # before removing the first entry.
                                base = perms.get("accounts") or list(accounts)
                                updated = toggle_account(set_accounts(perms, base), name)
                                if not updated.get("accounts"):
                                    await event.answer("Нельзя снять последний аккаунт", alert=True)
                                    return
                                if len(updated["accounts"]) == len(accounts):
                                    updated = set_accounts(updated, [])
                                perms = updated
                                await save_key_permissions(key_id, perms)
                                await event.answer(f"@{name}")
                            page = (int(arg) // KEY_PANEL_ACCOUNTS_PAGE) if arg.isdigit() else 0
                            await safe_edit(
                                event, key_accounts_card(perms, accounts),
                                buttons=key_accounts_keyboard(key_id, perms, accounts, page),
                            )
                            return
                        elif section == "d" and arg:
                            if arg == "_x":
                                await prompt_pending(event, "key_delay", scope=str(key_id))
                                return
                            perms = set_delay(perms, int(arg) if arg.isdigit() else 0)
                            await save_key_permissions(key_id, perms)
                            await event.answer(f"Задержка: {format_delay(permission_delay_minutes(perms))}")

                        if section == "f":
                            await safe_edit(event, key_features_card(perms), buttons=key_features_keyboard(key_id, perms))
                        elif section == "n":
                            await safe_edit(event, key_notify_card(perms), buttons=key_notify_keyboard(key_id, perms))
                        elif section == "a":
                            await safe_edit(event, key_accounts_card(perms, accounts), buttons=key_accounts_keyboard(key_id, perms, accounts))
                        elif section == "d":
                            await safe_edit(event, key_delay_card(perms), buttons=key_delay_keyboard(key_id, perms))
                        else:
                            await render_key_panel_into(event, key_id)
                        return
                    if seg[0] == "key" and seg[1] in KEY_LIFECYCLE_ACTIONS and len(seg) >= 3:
                        try:
                            key_id = int(seg[2])
                        except ValueError:
                            await event.answer("Некорректная команда", alert=True)
                            return
                        action = seg[1]
                        if action in ("rm", "on"):
                            revoked = action == "rm"
                            await set_bot_key_revoked(key_id, revoked)
                            await record_app_event(
                                "INFO", "bot", "Bot key revoked" if revoked else "Bot key restored",
                                {"id": key_id, "via": "bot"},
                            )
                            await event.answer(
                                "🚫 Ключ отозван — ссылка больше не откроет доступ"
                                if revoked else
                                "♻️ Ключ снова работает"
                            )
                            await render_key_panel_into(event, key_id)
                            return
                        if action == "del":
                            # Deleting is the one key action with no undo, so confirm.
                            key = await get_bot_key(key_id)
                            if not key:
                                await event.answer("Ключ уже удалён", alert=True)
                                text, kb = await render_keys()
                                await safe_edit(event, text, buttons=kb)
                                return
                            await safe_edit(event, key_delete_card(key), buttons=key_delete_keyboard(key_id))
                            return
                        deleted = await delete_bot_key(key_id)
                        if deleted is None:
                            await event.answer("Ключ уже удалён", alert=True)
                        else:
                            # Deleting drops the invite only; people who already
                            # joined keep their access and their grants.
                            joined = int(deleted.get("member_count") or 0)
                            note = "🗑 Ключ удалён"
                            if joined:
                                note += f"\nВошедшие ({joined}) сохраняют доступ — отключить можно в «Люди»."
                            await event.answer(note, alert=bool(joined))
                            await record_app_event(
                                "INFO", "bot", "Bot access key deleted",
                                {"id": key_id, "label": deleted.get("label"), "via": "bot"},
                            )
                        text, kb = await render_keys()
                        await safe_edit(event, text, buttons=kb)
                        return
                    await event.answer("Неизвестная команда", alert=True)
                    return
                if seg[0] in ("adm", "mem", "acc"):
                    if role != "admin":
                        await event.answer("Только владелец", alert=True)
                        return
                    if seg[0] == "adm" and seg[1] == "restart":
                        result = await restart_monitoring()
                        await event.answer(f"♻️ Перезапущено аккаунтов: {result.get('restarted', 0)}", alert=True)
                        text, kb = await render_management()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "adm" and seg[1] == "home":
                        text, kb = await render_management()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "adm" and seg[1] == "members":
                        text, kb = await render_members()
                        await safe_edit(event, text, buttons=kb)
                        return
                    if seg[0] == "adm" and seg[1] == "access":
                        await safe_edit(
                            event, await render_access_overview(),
                            buttons=[[Button.inline("⬅️ Управление", b"adm:home")]],
                        )
                        return
                    if seg[0] == "adm" and seg[1] in ("newkey", "newkeyp"):
                        premium = seg[1] == "newkeyp"
                        secret = generate_access_key()
                        key = await create_bot_key(
                            "премиум" if premium else "", secret,
                            "premium" if premium else "viewer", None,
                            dump_permissions(full_permissions()),
                        )
                        link = f"https://t.me/{bot_username}?start={secret}" if bot_username else ""
                        title = "⚡ **Новый премиум-ключ**" if premium else "🔑 **Новый ключ**"
                        body = title + "\n" + DIV + f"\n🔐 `{secret}`"
                        if premium:
                            body += "\n⚡ Держатель получает рассылки мгновенно, без модерации."
                        if link:
                            body += f"\n🔗 {link}"
                        await event.respond(body, link_preview=False)
                        await event.answer("Ключ создан")
                        screen = await render_key_panel(int(key["id"]))
                        if screen:
                            text, kb = screen
                            await event.respond("⚙️ **Настройте ключ перед отправкой**\n\n" + text, buttons=kb)
                        return
                    if seg[0] in ("mem", "acc") and len(seg) >= 3:
                        try:
                            tg = int(seg[2])
                        except ValueError:
                            await event.answer("Некорректная команда", alert=True)
                            return
                        if seg[0] == "mem" and seg[1] == "open":
                            res = await open_member_view(tg)
                            if res is None:
                                await event.answer("Участник не найден", alert=True)
                                return
                            text, kb = res
                            await safe_edit(event, text, buttons=kb)
                            return
                        if seg[0] == "mem" and seg[1] in ("block", "unblock"):
                            await set_bot_member_blocked(tg, seg[1] == "block")
                            state.access_cache.pop(tg, None)
                            await event.answer("Заблокирован" if seg[1] == "block" else "Разблокирован")
                            res = await open_member_view(tg)
                            if res is not None:
                                text, kb = res
                                await safe_edit(event, text, buttons=kb)
                            return
                        if seg[0] == "mem" and seg[1] == "access":
                            member = await get_bot_member(tg)
                            if not member:
                                await event.answer("Участник не найден", alert=True)
                                return
                            await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
                            return
                        if seg[0] == "acc" and seg[1] in ("close", "open", "close2h", "morning", "undo", "log"):
                            member = await get_bot_member(tg)
                            if not member:
                                await event.answer("Участник не найден", alert=True)
                                return
                            if seg[1] == "log":
                                log = await get_access_audit(tg)
                                lines = ["🧾 **История доступа**", DIV]
                                if not log:
                                    lines.append("📭 __Пусто.__")
                                else:
                                    lines += [f"`{fmt_dt(a['created_at'])}` · {a['action']} · _{a['actor']}_" for a in log]
                                await safe_edit(
                                    event, "\n".join(lines),
                                    buttons=[[Button.inline("⬅️ Назад", f"mem:access:{tg}".encode())]],
                                )
                                return
                            if seg[1] == "open":
                                cancelled = await _open_member_access(tg, event.sender_id)
                                await event.answer(f"🟢 Доступ открыт ({cancelled})")
                            elif seg[1] == "undo":
                                target = find_undoable(await get_access_audit(tg, limit=50))
                                if not target:
                                    await event.answer("Нечего отменять", alert=True)
                                else:
                                    plan = plan_undo(target)
                                    await _apply_undo(tg, plan)
                                    await record_access_audit(
                                        tg, target.get("schedule_id"), "undo", f"admin:{event.sender_id}",
                                        None, {"undone_audit_id": int(target["id"]), "plan": plan},
                                    )
                                    state.access_cache.pop(tg, None)
                                    await event.answer("↩️ Отменено")
                            else:
                                until_iso = None
                                note = "🔴 Доступ закрыт"
                                if seg[1] == "close2h":
                                    until = datetime.now(timezone.utc) + timedelta(hours=2)
                                    until_iso = until.replace(microsecond=0, tzinfo=None).isoformat()
                                    note = "🔴 Закрыт на 2ч"
                                elif seg[1] == "morning":
                                    target_local = next_hhmm_datetime(datetime.now().astimezone(), "08:00")
                                    if target_local:
                                        until_iso = target_local.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
                                    note = "🔴 Закрыт до 08:00"
                                row = await create_disable_until_window(tg, until_iso, created_by=event.sender_id)
                                await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}", None, {"until": until_iso})
                                state.access_cache.pop(tg, None)
                                await event.answer(note)
                            member = await get_bot_member(tg)
                            await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
                            return
                    await event.answer("Неизвестная команда", alert=True)
                    return

            # ---- menu navigation (any authenticated role, within granted sections) ----
            needed = MENU_CALLBACK_FEATURES.get(data)
            if needed and not feature_ok(needed):
                await event.answer("Раздел закрыт владельцем", alert=True)
                return

            if data == "menu_help":
                await safe_edit(event, help_text(role, perms), buttons=section_nav(b"menu_help"))
                return
            if data == "menu_stats":
                await safe_edit(event, await render_stats(), buttons=section_nav(b"menu_stats"))
                return
            if data == "menu_status":
                await safe_edit(event, await render_status(), buttons=section_nav(b"menu_status"))
                return
            if data == "menu_giveaways":
                text, kb = await render_giveaways(perms=perms)
                await safe_edit(event, text, buttons=kb, link_preview=False)
                return
            if data == "menu_recent":
                text, kb = await render_feed("all", perms=perms)
                await safe_edit(event, text, buttons=kb, link_preview=False)
                return
            if data == "menu_market":
                await safe_edit(event, await render_market(), buttons=market_keyboard())
                return
            if data == "menu_summary":
                await safe_edit(event, await render_summary(), buttons=section_nav(b"menu_summary"))
                return

            if data == "menu_main":
                await safe_edit(event, await render_home(role), buttons=main_menu_buttons(role, perms))
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
                if data == "pf_sc":
                    await prompt_pending(event, "min_score")
                    return
                toggled = PF_TOGGLES.get(data)
                if toggled:
                    if toggled != "muted" and toggled not in allowed_pref_keys(perms):
                        await event.answer("Этот тип уведомлений закрыт владельцем", alert=True)
                        return
                    prefs = toggle_member_pref(prefs, toggled)
                    await set_bot_member_prefs(event.sender_id, prefs)
                text, buttons = member_prefs_menu(prefs, perms)
                await safe_edit(event, text, buttons=buttons)
                if toggled:
                    await event.answer("Сохранено")
                return

            # ---- settings menus (owner only; st_x cancel works for members too) ----
            if data == "st" or data.startswith("st_"):
                if role != "admin":
                    if data == "st_x":
                        bot_pending_inputs.pop(event.sender_id, None)
                        await event.answer("Отменено")
                        return
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
                text, kb = await render_keys()
                await safe_edit(event, text, buttons=kb)
                return
            if data == "menu_scan":
                text, kb = render_scan_panel()
                await safe_edit(event, text, buttons=kb)
                return
            if data == "menu_restart":
                await safe_edit(event, restart_confirm_card(), buttons=restart_confirm_keyboard())
                return
            if data == "menu_logs":
                if not LOG_FILE.exists():
                    await event.answer("Логов нет")
                    return
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
                await safe_edit(
                    event,
                    "📜 **Последние логи**\n" + DIV + "\n```\n" + "\n".join(lines)[-3500:] + "\n```",
                    buttons=logs_keyboard(),
                )
                return
            if data.startswith("revokekey_"):
                key_id = _cb_id(data)
                if key_id is None:
                    await event.answer("Некорректная команда", alert=True)
                    return
                await set_bot_key_revoked(key_id, True)
                await event.answer("Ключ отозван")
                text, kb = await render_keys()
                await safe_edit(event, text, buttons=kb)
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
                # Copies still waiting on a per-key delay are dropped before they
                # ever reach the member.
                cancelled = await cancel_pending_sends(token)
                if not rows and not cancelled:
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
                                rebuilt.append(Button.inline(f"✅ Скрыто у друзей ({hidden + cancelled})", data=b"noop"))
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
                if cancelled:
                    note += f", отменено до отправки: {cancelled}"
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
            if data.startswith(("fav_", "read_")) and ping_id is None:
                await event.answer("Некорректная команда", alert=True)
                return
            if data.startswith("fav_"):
                await toggle_favorite(ping_id)
                await event.answer("Избранное обновлено")
            elif data.startswith("read_"):
                await mark_ping_read_db(ping_id)
                await event.answer("Отмечено как прочитанное")
                await event.delete()
            elif data.startswith(("gconfirm_", "gskip_")):
                await event.answer("Действие розыгрышей больше недоступно.", alert=True)

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
                    BotCommand("members", "Пользователи"),
                    BotCommand("access", "Доступ по расписанию"),
                    BotCommand("actions", "История действий по розыгрышам"),
                    BotCommand("roulette", "Рулетка йобо"),
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
