from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from telethon import TelegramClient



@dataclass
class AppState:
    started_at: datetime = field(default_factory=datetime.now)
    clients: list[TelegramClient] = field(default_factory=list)
    bot_client: Optional[TelegramClient] = None
    bot_id: Optional[int] = None
    bot_username: Optional[str] = None
    # HTTPS origin of the Mini App, owned by the ``tunnel`` job. None while the
    # tunnel is down — the «🛰 Панель» button is simply not rendered then.
    public_url: Optional[str] = None
    # Bot connection health, maintained by the ``bot-connection`` supervisor
    # (see bot_connection.py). While the client is down it receives no updates,
    # so an outage here means every command/button the owner sends is queued on
    # Telegram's side until we reconnect.
    bot_offline_since: Optional[datetime] = None
    # True when init_bot raised before finishing handler registration. The client
    # may still be connected, so `bot_connected` alone would report a bot that
    # answers nothing as healthy.
    bot_init_failed: bool = False
    # Set by every handler that runs. `bot_connected` only proves the socket is
    # up; a client that is connected but no longer receiving updates ("connected
    # but deaf") is invisible without this.
    bot_last_update_at: Optional[datetime] = None
    # Rolling handler counters for /api/health. Reset only on restart.
    bot_handler_calls: int = 0
    bot_handler_errors: int = 0
    bot_handler_slow: int = 0
    # Pending free-text inputs for the bot settings menus: sender_id -> {kind, scope, armed_at}.
    bot_pending_inputs: dict[int, dict] = field(default_factory=dict)
    # Поисковый запрос ленты: sender_id -> (текст, когда задан). В 64 байта
    # callback-данных строка не влезает, поэтому кнопка несёт только флаг, а
    # текст лежит здесь. Живёт до перезапуска — после него лента честно
    # показывается без поиска, а не с чужим запросом.
    bot_feed_queries: dict[int, tuple[str, datetime]] = field(default_factory=dict)
    # Отмеченные строки на доске долгов: sender_id -> {ping_id}. Массовое
    # «забрал» в вебе было галочками в DOM; в боте отметка живёт здесь, потому
    # что в кнопку список из десятка id не влезает. Теряется при перезапуске —
    # это выбор пользователя на один заход, а не данные.
    bot_debt_marks: dict[int, set[int]] = field(default_factory=dict)
    # When each sender last touched their marks — the janitor forgets a
    # selection nobody has looked at for an hour.
    bot_debt_marks_touched: dict[int, datetime] = field(default_factory=dict)
    # 30-second undo of the last status change per sender (see bot/undo.py).
    bot_undo: dict[int, dict] = field(default_factory=dict)
    # Last dead-channel scan: {"at": datetime, "items": {chat_id: candidate}} —
    # the leave button needs to know which accounts sit in the channel.
    bot_cleanup_cache: dict[str, Any] = field(default_factory=dict)
    # Resolved custom-emoji pack: standard-emoji char -> document_id. Empty when
    # BOT_CUSTOM_EMOJI_SET is unset or the pack can't be resolved (plain fallback).
    custom_emoji_map: dict[str, int] = field(default_factory=dict)
    # Precompiled first-character buckets over custom_emoji_map, built once at
    # startup so the entity scan is not O(text x pack size) per rendered card.
    custom_emoji_index: dict[str, list[str]] = field(default_factory=dict)
    # Scheduled-access cache: tg_id -> (allowed, valid_until_utc, reason). Computed
    # in bot_role, refreshed by access_scheduler_loop, invalidated on rule edits.
    access_cache: dict[int, tuple[bool, datetime, str]] = field(default_factory=dict)
    connected_user_ids: set[int] = field(default_factory=set)
    pending_auths: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed_msg_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    accounts_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    scan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scan_cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    background_task_names: set[str] = field(default_factory=set)
    background_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    job_started_at: dict[str, datetime] = field(default_factory=dict)
    job_last_ok_at: dict[str, datetime] = field(default_factory=dict)
    job_restart_count: dict[str, int] = field(default_factory=dict)
    job_last_error: dict[str, str] = field(default_factory=dict)
    last_scan_finished_at: Optional[datetime] = None
    last_scan_status: Optional[str] = None
    # Result of the last `maintenance` pass (sizes, freelist share, what was
    # removed) — read by /api/health and the bot's diagnostics without a query.
    maintenance_stats: dict[str, Any] = field(default_factory=dict)
    # Downtime before this start (previous process's last heartbeat → start),
    # set only when long enough to report. Cleared once the catch-up card is sent.
    downtime_gap: Optional[tuple[datetime, datetime]] = None
    account_cooldown_until: dict[str, datetime] = field(default_factory=dict)
    shutting_down: bool = False
    last_giveaway_action_at: Optional[datetime] = None
    notification_seen: dict = field(default_factory=dict)
    # Obsidian "Долги" note sync: last parsed snapshot, sync meta, write lock.
    obsidian_debts: Optional[dict] = None
    obsidian_sync_meta: Optional[dict] = None
    obsidian_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Книга зарплат: разобранный снимок (salary.SalaryBook) и метаданные чтения.
    # Снимок держится в памяти, чтобы карточка не разбирала .xlsx на каждый клик.
    salary_book: Any = None
    salary_meta: dict = field(default_factory=dict)
    # Wallet-bot check auto-claim (check_claimer.py). The config mirrors the
    # `check_claim` settings key; everything else is process-local bookkeeping.
    check_claim_cfg: dict = field(default_factory=dict)
    check_bot_ids: dict[int, str] = field(default_factory=dict)  # wallet bot user id -> "xrocket" / "send"
    check_seen: OrderedDict[str, None] = field(default_factory=OrderedDict)  # attempts / announcements made
    check_chat_codes: OrderedDict[str, None] = field(default_factory=OrderedDict)  # codes met in chats
    own_check_codes: OrderedDict[str, None] = field(default_factory=OrderedDict)  # codes our accounts made
    own_admin_chat_ids: set[int] = field(default_factory=set)  # chats any of our accounts administers
    check_relays: dict[str, dict] = field(default_factory=dict)  # token -> captcha relay card
    ping_usernames: list = field(default_factory=list)
    ignored_chat_ids: set = field(default_factory=set)  # ignored_chats.py
    ping_regex: object = None  # compiled regex or None
    ping_user_ids: dict[int, str] = field(default_factory=dict)  # resolved user_id -> tracked username
    ping_user_ids_resolved: set[str] = field(default_factory=set)  # lowercase usernames already attempted
    win_keywords: list = field(default_factory=list)
    giveaway_keywords: list = field(default_factory=list)
    high_priority_keywords: list = field(default_factory=list)
    ignore_keywords: list = field(default_factory=list)
    join_button_keywords: list = field(default_factory=list)
    session_names: list = field(default_factory=list)
    scan_status: dict[str, Any] = field(default_factory=lambda: {
        "running": False,
        "started_at": None,
        "finished_at": None,
        "current_account": None,
        "current_username": None,
        "current_channel": None,
        "total_accounts": 0,
        "processed_accounts": 0,
        "total_channels": 0,
        "total_usernames": 0,
        "processed_usernames": 0,
        "found": 0,
        "fast_channels": 0,
        "idle_channels": 0,
        "targeted_channels": 0,
        "edit_sweep_messages": 0,
        "global_search_cards": 0,
        "global_search_found": 0,
        "scan_strategy": "",
        "history_limit": 0,
        "last_error": None,
        "scan_run_id": None,
        "cancel_requested": False,
    })

    def heartbeat(self, name: str) -> None:
        """Record a successful cycle of a background job, for the watchdog.

        Supervised loops run forever and never return, so the supervisor cannot
        time their success — each loop calls this at the end of a healthy cycle
        so ``watchdog_loop`` can tell a live job from one stuck failing."""
        self.job_last_ok_at[name] = datetime.now()

    def remember_message(self, key: str, limit: int = 5000) -> bool:
        if key in self.processed_msg_ids:
            return False
        self.processed_msg_ids[key] = None
        self.processed_msg_ids.move_to_end(key)
        while len(self.processed_msg_ids) > limit:
            self.processed_msg_ids.popitem(last=False)
        return True
