"""Состояние приложения одним набором фактов — для бота и для HTTP.

Эти сводки жили внутри ``routers/system.py``, то есть существовали только пока
существует веб. Бот показывал похожие цифры, считая их по-своему, и они
расходились — «база 143 МБ» в одном месте и 142 в другом, разный список джобов.
Теперь факт считается один раз здесь, а поверхности только рисуют.

Модуль ничего не решает про доступ: он собирает данные, гейты — на вызывающем.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import database
from database import (
    get_db_stats, get_latest_scan_run, get_outbox_stats, get_recent_problem_events,
    get_scan_run_health, get_schema_version, list_db_backups,
)

from . import APP_VERSION
from . import watch_settings as ws
from .app_ctx import API_HASH, API_ID, LOG_FILE, state
from .jobs import runtime_health


def db_size_bytes() -> int:
    try:
        return database.DB_PATH.stat().st_size if database.DB_PATH.exists() else 0
    except OSError:
        return 0


def uptime_seconds() -> int:
    return int((datetime.now() - state.started_at).total_seconds())


def uptime_text() -> str:
    total = uptime_seconds()
    return f"{total // 3600}ч {(total % 3600) // 60}м"


def accounts_online() -> int:
    return sum(1 for acc in list(state.accounts_state.values()) if acc.get("status") == "online")


async def system_status() -> dict[str, Any]:
    """Версия, аптайм, аккаунты, скан, база, джобы — то, что печатает «Статус»."""
    latest_scan = await get_latest_scan_run()
    size = db_size_bytes()
    return {
        "version": APP_VERSION,
        "uptime_seconds": uptime_seconds(),
        "uptime": uptime_text(),
        "accounts_online": len(state.clients),
        "accounts_total": len(state.accounts_state) or len(state.session_names),
        "tracked_usernames": state.ping_usernames,
        "scan": state.scan_status,
        "last_scan": latest_scan,
        "last_scan_error": (state.scan_status.get("last_error")
                            or (latest_scan or {}).get("last_error")),
        "background_tasks": sorted(state.background_task_names),
        "schema_version": await get_schema_version(),
        "db_size_bytes": size,
        "db_size_mb": round(size / 1024 / 1024, 2),
        "giveaway_action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}",
        "giveaway_review_mode": ws.GIVEAWAY_REVIEW_MODE,
        "giveaway_min_action_delay_seconds": ws.GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
        "runtime_settings": ws.runtime_settings_payload(),
    }


def recommendations(scan_health: dict, outbox: dict, runtime: dict, owed_ping_cards: int = 0) -> list[str]:
    """Что делать с тем, что нашли. Пусто не бывает — молчание читается как сбой."""
    out: list[str] = []
    if owed_ping_cards:
        out.append(f"Не доставлено карточек об упоминаниях: {owed_ping_cards}. "
                   "notify-retry досылает их, пока бот на связи — если число не падает, бот офлайн.")
    if scan_health.get("running"):
        out.append("Есть активный scan-run. Если скан давно не движется, перезапустите приложение.")
    if scan_health.get("recent_interrupted"):
        out.append("Последний запуск нашёл прерванные scan-runs и пометил их interrupted.")
    if outbox.get("pressure") == "high":
        out.append("Live outbox большой: фоновая чистка должна удерживать последние события.")
    if runtime.get("missing_background_tasks"):
        out.append("Некоторые фоновые задачи не активны: проверьте логи и перезапустите приложение.")
    if not out:
        out.append("Критичных проблем диагностика не видит.")
    return out


async def diagnostics() -> dict[str, Any]:
    """Глубокий отчёт: база, очередь событий, джобы, скан, последние проблемы."""
    latest_scan = await get_latest_scan_run()
    outbox = await get_outbox_stats()
    scan_health = await get_scan_run_health()
    runtime = runtime_health(state, accounts_online=len(state.clients),
                             accounts_configured=len(state.session_names))
    size = db_size_bytes()
    try:
        owed_ping_cards = await database.count_owed_ping_notifications()
    except Exception:
        owed_ping_cards = 0
    return {
        "version": APP_VERSION,
        "owed_ping_cards": owed_ping_cards,
        "schema_version": await get_schema_version(),
        "db": {
            "path": str(database.DB_PATH),
            "size_bytes": size,
            "size_mb": round(size / 1024 / 1024, 2),
            "stats": await get_db_stats(),
            "backup_count": len(list_db_backups(limit=500)),
        },
        # Last pass of the hourly `maintenance` job (empty until it has run).
        "maintenance": dict(state.maintenance_stats),
        "live": {"outbox": outbox},
        "runtime": runtime,
        "scan": {
            "current": state.scan_status,
            "latest": latest_scan,
            "health": scan_health,
            "background_tasks": sorted(state.background_task_names),
        },
        "recent_problem_events": await get_recent_problem_events(limit=8),
        "recommendations": recommendations(scan_health, outbox, runtime, owed_ping_cards),
        "accounts": {
            "online": len(state.clients),
            "configured": len(state.session_names),
            "known_state": state.accounts_state,
        },
        "giveaways": {
            "review_mode": ws.GIVEAWAY_REVIEW_MODE,
            "action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}",
            "min_action_delay_seconds": ws.GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
            "strict_rule": "chat_type == channel and text contains one configured giveaway keyword",
        },
        "runtime_settings": ws.runtime_settings_payload(),
    }


def setup_checks() -> list[dict[str, Any]]:
    """Готовность установки. Про веб-токены здесь ничего нет намеренно —
    они принадлежат HTTP-слою и уходят вместе с ним."""
    return [
        {"key": "api_credentials", "label": "TELEGRAM_API_ID/API_HASH", "ok": bool(API_ID and API_HASH)},
        {"key": "sessions", "label": "Найдены Telegram-сессии",
         "ok": bool(state.session_names or state.accounts_state)},
        {"key": "database", "label": "SQLite база доступна", "ok": database.DB_PATH.exists()},
        {"key": "logs", "label": "Папка логов доступна", "ok": LOG_FILE.parent.exists()},
        {"key": "giveaway_rule", "label": "Розыгрыш = канал + ключевое слово",
         "ok": True, "details": state.giveaway_keywords},
    ]


def setup_ready(checks: list[dict[str, Any]]) -> bool:
    return all(item.get("ok") for item in checks)
