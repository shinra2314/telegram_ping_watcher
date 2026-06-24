"""System endpoints: index, health, status, diagnostics, events, logs, report."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, HTMLResponse, Response

import database
from database import (
    get_db_stats,
    get_events,
    get_latest_scan_run,
    get_outbox_stats,
    get_recent_problem_events,
    get_report_data,
    get_scan_run_health,
    get_schema_version,
    list_db_backups,
)
from pulse_desk import APP_VERSION
from pulse_desk import watch_settings as ws
from pulse_desk.app_ctx import (
    ADMIN_TOKEN,
    ALLOW_QUERY_TOKEN,
    API_HASH,
    API_ID,
    AUTO_JOIN_GIVEAWAYS,
    LOG_FILE,
    PUBLIC_SHARE_MODE,
    STATIC_DIR,
    VIEWER_TOKEN,
    app_base_url,
    get_current_role,
    require_admin,
    state,
)
from pulse_desk.common import now_iso
from pulse_desk.jobs import runtime_health
from pulse_desk.security import is_weak_token
from pulse_desk.watchdog import classify_job, default_thresholds

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg")


@router.get("/", response_class=HTMLResponse)
async def read_index():
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/health")
async def health():
    accounts_configured = len(state.session_names) or len(state.accounts_state)
    accounts_online = sum(
        1 for acc in list(state.accounts_state.values()) if acc.get("status") == "online"
    )
    info = runtime_health(
        state,
        accounts_online=accounts_online,
        accounts_configured=accounts_configured,
    )
    try:
        db_size_bytes = database.DB_PATH.stat().st_size if database.DB_PATH.exists() else 0
    except Exception:
        db_size_bytes = 0
    # Engine health: which critical jobs have gone silent or died (same logic the
    # watchdog uses to page the admin). Surfaced so the dashboard can show it too.
    thresholds = default_thresholds(
        scan_interval_seconds=ws.SCAN_INTERVAL_SECONDS,
        market_poll_seconds=ws.MARKET_POLL_SECONDS,
    )
    now = datetime.now()
    unhealthy_jobs = []
    for name, threshold in thresholds.items():
        task = state.background_tasks.get(name)
        running = bool(task) and not task.done()
        ref = state.job_last_ok_at.get(name) or state.job_started_at.get(name)
        age = int((now - ref).total_seconds()) if ref else None
        h = classify_job(name, running=running, age_seconds=age, threshold_seconds=threshold)
        if not h.healthy:
            unhealthy_jobs.append({"job": name, "reason": h.reason, "age_seconds": h.age_seconds})
    healthy = not info.get("missing_background_tasks") and info.get("accounts_ok") and not unhealthy_jobs
    info.update(
        {
            "status": "ok" if healthy else "degraded",
            "unhealthy_jobs": unhealthy_jobs,
            "time": now_iso(),
            "version": APP_VERSION,
            "db_size_bytes": db_size_bytes,
            "last_scan_finished_at": state.last_scan_finished_at.isoformat(timespec="seconds")
            if state.last_scan_finished_at
            else None,
            "last_scan_status": state.last_scan_status,
            "scan_running": bool(state.scan_status.get("running")),
            "shutting_down": state.shutting_down,
            "live_subscribers": state.live_hub.subscriber_count(),
        }
    )
    return info


@router.get("/api/session")
async def session(response: Response, x_pulse_token: Optional[str] = Header(default=None), role: str = Depends(get_current_role)):
    if x_pulse_token:
        response.set_cookie("pulse_token", x_pulse_token, httponly=False, samesite="strict", path="/")
    return {"role": role, "public_share_mode": PUBLIC_SHARE_MODE}


@router.get("/api/share-guide", dependencies=[Depends(require_admin)])
async def share_guide():
    base_url = app_base_url()
    return {
        "recommended": "cloudflare_quick_tunnel",
        "public_share_mode": PUBLIC_SHARE_MODE,
        "local_url": base_url,
        "tunnel_command": f"cloudflared tunnel --url {base_url}",
        "viewer_token_configured": bool(VIEWER_TOKEN),
        "admin_token_configured": bool(ADMIN_TOKEN),
        "viewer_token_looks_weak": is_weak_token(VIEWER_TOKEN),
        "admin_token_looks_weak": is_weak_token(ADMIN_TOKEN),
        "viewer_capabilities": ["dashboard", "pings", "market", "analytics"],
        "admin_capabilities": ["accounts", "settings", "scan", "logs", "exports"],
        "never_share": [".env", "*.session", "*.db", "app.log", "ADMIN_TOKEN"],
        "friend_message_template": (
            "Открой ссылку Pulse Desk: {tunnel_url}\n"
            "Когда попросит токен, введи VIEWER_TOKEN, который я отправлю отдельно."
        ),
    }


@router.get("/api/status")
async def status(role: str = Depends(get_current_role)):
    latest_scan = await get_latest_scan_run()
    db_size_bytes = database.DB_PATH.stat().st_size if database.DB_PATH.exists() else 0
    return {
        "status": "ok",
        "version": APP_VERSION,
        "role": role,
        "public_share_mode": PUBLIC_SHARE_MODE,
        "accounts_online": len(state.clients),
        "accounts_total": len(state.accounts_state) or len(state.session_names),
        "tracked_usernames": state.ping_usernames,
        "scan": state.scan_status,
        "last_scan": latest_scan,
        "last_scan_error": state.scan_status.get("last_error") or (latest_scan or {}).get("last_error"),
        "uptime_seconds": int((datetime.now() - state.started_at).total_seconds()),
        "background_tasks": sorted(state.background_task_names),
        "schema_version": await get_schema_version(),
        "db_size_bytes": db_size_bytes,
        "db_size_mb": round(db_size_bytes / 1024 / 1024, 2),
        "auto_join_giveaways": AUTO_JOIN_GIVEAWAYS,
        "dry_run_giveaways": ws.DRY_RUN_GIVEAWAYS,
        "giveaway_action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}",
        "giveaway_review_mode": ws.GIVEAWAY_REVIEW_MODE,
        "giveaway_min_action_delay_seconds": ws.GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
        "runtime_settings": ws.runtime_settings_payload(),
    }


@router.get("/api/diagnostics", dependencies=[Depends(require_admin)])
async def diagnostics():
    latest_scan = await get_latest_scan_run()
    db_size_bytes = database.DB_PATH.stat().st_size if database.DB_PATH.exists() else 0
    outbox = await get_outbox_stats()
    scan_health = await get_scan_run_health()
    problem_events = await get_recent_problem_events(limit=8)
    runtime = runtime_health(state, accounts_online=len(state.clients), accounts_configured=len(state.session_names))
    recommendations: list[str] = []
    if scan_health["running"]:
        recommendations.append("Есть активный scan-run. Если скан давно не движется, перезапустите приложение.")
    if scan_health["recent_interrupted"]:
        recommendations.append("Последний запуск нашел прерванные scan-runs и пометил их interrupted.")
    if outbox.get("pressure") == "high":
        recommendations.append("Live outbox большой: фоновой cleanup должен удерживать последние события, проверьте SSE/refresh если очередь снова растет.")
    if runtime["missing_background_tasks"]:
        recommendations.append("Некоторые фоновые задачи не активны: проверьте логи и перезапустите приложение.")
    if not recommendations:
        recommendations.append("Критичных проблем диагностика не видит.")
    return {
        "status": "ok",
        "version": APP_VERSION,
        "schema_version": await get_schema_version(),
        "db": {
            "path": str(database.DB_PATH),
            "size_bytes": db_size_bytes,
            "size_mb": round(db_size_bytes / 1024 / 1024, 2),
            "stats": await get_db_stats(),
            "backup_count": len(list_db_backups(limit=500)),
        },
        "live": {
            "outbox": outbox,
            "sse_connected_hint": "EventSource uses the pulse_token cookie after /api/session.",
        },
        "runtime": runtime,
        "scan": {
            "current": state.scan_status,
            "latest": latest_scan,
            "health": scan_health,
            "background_tasks": sorted(state.background_task_names),
        },
        "recent_problem_events": problem_events,
        "recommendations": recommendations,
        "accounts": {
            "online": len(state.clients),
            "configured": len(state.session_names),
            "known_state": state.accounts_state,
        },
        "security": {
            "public_share_mode": PUBLIC_SHARE_MODE,
            "admin_token_configured": bool(ADMIN_TOKEN),
            "viewer_token_configured": bool(VIEWER_TOKEN),
            "admin_token_looks_weak": is_weak_token(ADMIN_TOKEN),
            "viewer_token_looks_weak": is_weak_token(VIEWER_TOKEN),
            "query_token_enabled": ALLOW_QUERY_TOKEN,
        },
        "giveaways": {
            "auto_join": AUTO_JOIN_GIVEAWAYS,
            "dry_run": ws.DRY_RUN_GIVEAWAYS,
            "review_mode": ws.GIVEAWAY_REVIEW_MODE,
            "action_account": f"@{ws.GIVEAWAY_ACTION_ACCOUNT}",
            "min_action_delay_seconds": ws.GIVEAWAY_MIN_ACTION_DELAY_SECONDS,
            "strict_rule": "chat_type == channel and text contains one configured giveaway keyword",
        },
        "runtime_settings": ws.runtime_settings_payload(),
    }


@router.get("/api/setup-check", dependencies=[Depends(require_admin)])
async def setup_check():
    checks = [
        {"key": "api_credentials", "label": "TELEGRAM_API_ID/API_HASH", "ok": bool(API_ID and API_HASH)},
        {"key": "admin_token", "label": "ADMIN_TOKEN задан и не слабый", "ok": bool(ADMIN_TOKEN and not is_weak_token(ADMIN_TOKEN))},
        {"key": "viewer_token", "label": "VIEWER_TOKEN задан", "ok": bool(VIEWER_TOKEN)},
        {"key": "sessions", "label": "Найдены Telegram-сессии", "ok": bool(state.session_names or state.accounts_state)},
        {"key": "database", "label": "SQLite база доступна", "ok": database.DB_PATH.exists()},
        {"key": "logs", "label": "Папка логов доступна", "ok": LOG_FILE.parent.exists()},
        {"key": "giveaway_rule", "label": "Розыгрыш = канал + ключевое слово", "ok": True, "details": state.giveaway_keywords},
    ]
    return {"ready": all(item["ok"] for item in checks), "checks": checks}


@router.get("/api/report-html", dependencies=[Depends(require_admin)], response_class=HTMLResponse)
async def report_html():
    data = await get_report_data(limit=120)
    totals = data["totals"]
    rows = "\n".join(
        f"<tr><td>{html.escape(str(row.get('detected_at') or ''))}</td>"
        f"<td>{html.escape(str(row.get('priority_score') or 0))}</td>"
        f"<td>{html.escape(str(row.get('status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('giveaway_status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('chat') or ''))}</td>"
        f"<td>{html.escape(str(row.get('sender') or ''))}</td>"
        f"<td>{html.escape((row.get('text') or '')[:220])}</td></tr>"
        for row in data["recent"]
    )
    chats = "\n".join(
        f"<li>{html.escape(str(row.get('chat') or 'unknown'))}: {row.get('count', 0)} упоминаний, "
        f"{row.get('wins', 0)} побед, средний приоритет {float(row.get('avg_priority') or 0):.1f}</li>"
        for row in data["top_chats"]
    )
    return HTMLResponse(f"""
    <!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Pulse Desk Report</title>
    <style>body{{font-family:Inter,Arial,sans-serif;margin:32px;color:#181816}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:8px;padding:12px;min-width:140px}}</style>
    </head><body>
    <h1>Pulse Desk Report</h1><p>Сформировано: {html.escape(data["generated_at"])}</p>
    <div class="cards">
      <div class="card"><strong>{totals.get('total', 0)}</strong><br>Всего</div>
      <div class="card"><strong>{totals.get('new_count', 0)}</strong><br>Новые</div>
      <div class="card"><strong>{totals.get('important_count', 0)}</strong><br>Важные</div>
      <div class="card"><strong>{totals.get('wins', 0)}</strong><br>Победы</div>
      <div class="card"><strong>{totals.get('giveaways', 0)}</strong><br>Розыгрыши</div>
    </div>
    <h2>Лучшие источники</h2><ul>{chats}</ul>
    <h2>Важные упоминания</h2>
    <table><thead><tr><th>Дата</th><th>Приоритет</th><th>Статус</th><th>Розыгрыш</th><th>Чат</th><th>Автор</th><th>Текст</th></tr></thead><tbody>{rows}</tbody></table>
    </body></html>
    """)


@router.get("/api/events", dependencies=[Depends(require_admin)])
async def read_events(limit: int = Query(100, ge=1, le=500), level: Optional[str] = None):
    return {"events": await get_events(limit=limit, level=level)}


@router.get("/api/logs", dependencies=[Depends(require_admin)])
async def get_logs(limit: int = Query(50, ge=1, le=500), level: Optional[str] = None):
    if not LOG_FILE.exists():
        return {"logs": []}
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    if level:
        level_upper = level.upper()
        lines = [line for line in lines if f"[{level_upper}]" in line]
    return {"logs": lines[-limit:]}


@router.get("/api/export-csv-legacy", include_in_schema=False, dependencies=[Depends(require_admin)])
async def _export_csv_legacy_redirect():
    return {"removed": True, "note": "use /api/export-csv"}
