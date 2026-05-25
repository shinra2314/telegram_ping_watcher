from __future__ import annotations

from typing import Any


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _bucket_count(buckets: dict[str, list[dict[str, Any]]], key: str) -> int:
    value = buckets.get(key) or []
    return len(value) if isinstance(value, list) else 0


def _progress(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(100, round(done / total * 100)))


def _attention_item(kind: str, title: str, text: str, value: int | str, icon: str, tone: str = "info") -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "text": text,
        "value": value,
        "icon": icon,
        "tone": tone,
    }


def build_dashboard_summary(
    *,
    status: dict[str, Any],
    analytics: dict[str, Any],
    tasks: dict[str, list[dict[str, Any]]],
    giveaway_board: dict[str, Any],
    problem_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a compact, UI-ready operations summary for the dashboard."""

    scan = status.get("scan") or {}
    last_scan = status.get("last_scan") or {}
    board_stats = giveaway_board.get("stats") or {}
    board_counts = giveaway_board.get("bucket_counts") or {}
    problem_events = problem_events or []

    accounts_online = _as_int(status.get("accounts_online"))
    accounts_total = max(_as_int(status.get("accounts_total")), accounts_online)
    tracked_usernames = status.get("tracked_usernames") or []
    tracked_count = len(tracked_usernames) if isinstance(tracked_usernames, list) else 0

    new_pings = _as_int(analytics.get("new_pings"))
    important = _as_int(analytics.get("important"))
    total_pings = _as_int(analytics.get("total_pings"))
    resolved = _as_int(analytics.get("resolved"))
    favorites = _as_int(analytics.get("favorites"))
    channel_chats_total = _as_int(analytics.get("channel_chats_total"))
    channel_memberships_total = _as_int(analytics.get("channel_memberships_total"))
    analytics_total_channels = _as_int(analytics.get("total_channels")) or channel_memberships_total or channel_chats_total

    overdue = _bucket_count(tasks, "overdue")
    today = _bucket_count(tasks, "today")
    tomorrow = _bucket_count(tasks, "tomorrow")
    no_deadline_tasks = _bucket_count(tasks, "no_deadline")
    open_tasks = _bucket_count(tasks, "all_open")

    need_action = _as_int(board_counts.get("need_action"), _as_int(board_stats.get("claim_prize")))
    waiting_result = _as_int(board_counts.get("waiting_result"), _as_int(board_stats.get("waiting_result")))
    suspicious = _as_int(board_counts.get("suspicious"))
    no_deadline = _as_int(board_counts.get("no_deadline"), _as_int(board_stats.get("no_deadline")))
    giveaway_overdue = _as_int(board_stats.get("overdue"))

    scan_accounts_done = _as_int(scan.get("processed_accounts"))
    scan_accounts_total = _as_int(scan.get("total_accounts"))
    scan_channels_total = _as_int(scan.get("total_channels")) or analytics_total_channels
    scan_usernames_done = _as_int(scan.get("processed_usernames"))
    scan_usernames_total = _as_int(scan.get("total_usernames"))
    scan_running = bool(scan.get("running"))
    scan_percent = _progress(scan_usernames_done or scan_accounts_done, scan_usernames_total or scan_accounts_total)
    last_scan_error = status.get("last_scan_error") or last_scan.get("last_error")

    attention: list[dict[str, Any]] = []
    if scan_running:
        attention.append(_attention_item("scan", "Идет скан истории", f"{scan_usernames_done}/{scan_usernames_total or 0} usernames обработано", f"{scan_percent}%", "loader-circle", "warn"))
    if new_pings:
        attention.append(_attention_item("new", "Новые упоминания", "Свежие сообщения еще не разобраны.", new_pings, "sparkles", "warn"))
    if important:
        attention.append(_attention_item("important", "Важные сигналы", "Высокий приоритет или ручная отметка important.", important, "flame", "bad"))
    if overdue or giveaway_overdue:
        attention.append(_attention_item("overdue", "Просроченные дедлайны", "Проверьте задачи и розыгрыши с истекшим сроком.", overdue + giveaway_overdue, "triangle-alert", "bad"))
    if today:
        attention.append(_attention_item("today", "Дедлайны сегодня", "Лучше закрыть до следующего фонового скана.", today, "calendar-days", "warn"))
    if need_action:
        attention.append(_attention_item("giveaway-action", "Забрать или проверить", "Есть розыгрыши, где требуется ручное решение.", need_action, "mouse-pointer-click", "warn"))
    if no_deadline:
        attention.append(_attention_item("no-deadline", "Нет дедлайна", "Нужно обновить профиль канала или поставить срок вручную.", no_deadline, "calendar-x", "bad"))
    if suspicious:
        attention.append(_attention_item("manual", "Ручная проверка", "Найдены внешние условия, captcha или подозрительные требования.", suspicious, "shield-alert", "bad"))
    if accounts_total and accounts_online < accounts_total:
        attention.append(_attention_item("accounts", "Аккаунты требуют внимания", f"Онлайн {accounts_online} из {accounts_total}.", f"{accounts_online}/{accounts_total}", "radio", "warn"))
    if last_scan_error:
        attention.append(_attention_item("scan-error", "Ошибка последнего скана", str(last_scan_error)[:160], "!", "circle-alert", "bad"))
    if problem_events:
        latest = problem_events[0]
        attention.append(_attention_item("events", "Есть предупреждения в логах", str(latest.get("message") or "Проверьте журнал событий.")[:160], len(problem_events), "terminal", "warn"))

    if not attention:
        attention.append(_attention_item("calm", "Сейчас спокойно", "Критичных действий нет: можно разбирать ленту или запускать плановый скан.", "ok", "check-circle", "good"))

    readiness = [
        {
            "key": "accounts",
            "label": "Telegram-аккаунты",
            "ok": accounts_online > 0,
            "value": f"{accounts_online}/{accounts_total}" if accounts_total else "0",
            "hint": "есть подключенные сессии" if accounts_online else "подключите хотя бы одну session",
        },
        {
            "key": "tracking",
            "label": "Трекинг usernames",
            "ok": tracked_count > 0,
            "value": tracked_count,
            "hint": "список отслеживания заполнен" if tracked_count else "добавьте usernames в настройках",
        },
        {
            "key": "channels",
            "label": "Каналы",
            "ok": analytics_total_channels > 0 or scan_channels_total > 0,
            "value": analytics_total_channels or scan_channels_total,
            "hint": "учтены по аккаунтам" if channel_memberships_total else "появятся после скана истории",
        },
        {
            "key": "scan",
            "label": "История сканов",
            "ok": not last_scan_error,
            "value": last_scan.get("status") or ("идет" if scan_running else "ожидает"),
            "hint": "последний скан без ошибки" if not last_scan_error else str(last_scan_error)[:120],
        },
        {
            "key": "giveaways",
            "label": "Безопасность розыгрышей",
            "ok": bool(status.get("dry_run_giveaways", True)) and not bool(status.get("auto_join_giveaways")),
            "value": "dry-run" if status.get("dry_run_giveaways", True) else "live",
            "hint": "ручной безопасный режим" if status.get("dry_run_giveaways", True) else "действия могут выполняться в Telegram",
        },
    ]

    if any(item["tone"] == "bad" for item in attention):
        health_level = "bad"
        headline = "Есть срочные места"
    elif any(item["tone"] == "warn" for item in attention):
        health_level = "warn"
        headline = "Есть что разобрать"
    else:
        health_level = "good"
        headline = "Пульт в порядке"

    return {
        "status": "ok",
        "health_level": health_level,
        "headline": headline,
        "attention": attention[:10],
        "readiness": readiness,
        "scan_progress": {
            "running": scan_running,
            "percent": scan_percent,
            "accounts_done": scan_accounts_done,
            "accounts_total": scan_accounts_total,
            "total_channels": scan_channels_total,
            "fast_channels": _as_int(scan.get("fast_channels")),
            "targeted_channels": _as_int(scan.get("targeted_channels")),
            "edit_sweep_messages": _as_int(scan.get("edit_sweep_messages")),
            "scan_strategy": scan.get("scan_strategy") or "",
            "history_limit": _as_int(scan.get("history_limit")),
            "usernames_done": scan_usernames_done,
            "usernames_total": scan_usernames_total,
            "found": _as_int(scan.get("found")),
            "current_account": scan.get("current_account") or "",
            "current_username": scan.get("current_username") or "",
            "last_error": last_scan_error or "",
        },
        "counts": {
            "total_pings": total_pings,
            "new_pings": new_pings,
            "important": important,
            "resolved": resolved,
            "favorites": favorites,
            "total_channels": analytics_total_channels,
            "channel_chats_total": channel_chats_total,
            "channel_memberships_total": channel_memberships_total,
            "open_tasks": open_tasks,
            "overdue_tasks": overdue,
            "today_tasks": today,
            "tomorrow_tasks": tomorrow,
            "no_deadline_tasks": no_deadline_tasks,
            "giveaway_need_action": need_action,
            "giveaway_waiting_result": waiting_result,
            "giveaway_no_deadline": no_deadline,
            "giveaway_suspicious": suspicious,
            "giveaway_overdue": giveaway_overdue,
        },
        "generated_at": giveaway_board.get("generated_at") or status.get("time") or "",
    }
