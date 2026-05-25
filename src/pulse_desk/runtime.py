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
    connected_user_ids: set[int] = field(default_factory=set)
    pending_auths: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed_msg_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    accounts_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    scan_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scan_cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    background_task_names: set[str] = field(default_factory=set)
    background_tasks: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    shutting_down: bool = False
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
        "targeted_channels": 0,
        "edit_sweep_messages": 0,
        "scan_strategy": "",
        "history_limit": 0,
        "last_error": None,
        "scan_run_id": None,
        "cancel_requested": False,
    })

    def remember_message(self, key: str, limit: int = 5000) -> bool:
        if key in self.processed_msg_ids:
            return False
        self.processed_msg_ids[key] = None
        self.processed_msg_ids.move_to_end(key)
        while len(self.processed_msg_ids) > limit:
            self.processed_msg_ids.popitem(last=False)
        return True
