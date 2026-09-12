"""Диагностика: готовность, проблемы, история сканов и правок настроек.

Перенос вкладки «Здоровье» и панели событий. Сами факты считает
:mod:`pulse_desk.health_report` — тот же источник, что отдаёт ``/api/health``,
поэтому «что видит бот» и «что видит вотчдог» не могут разойтись.
"""
from __future__ import annotations

from typing import Any

from telethon import Button

from database import get_events, get_scan_runs, get_settings_history

from ...health_report import diagnostics as build_diagnostics, setup_checks, setup_ready
from ..chrome import dot, empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, fmt_dt

TAIL = 8


def report_card(report: dict[str, Any], checks: list[dict[str, Any]]) -> str:
    db = report.get("db") or {}
    runtime = report.get("runtime") or {}
    scan = report.get("scan") or {}
    lines = [
        header("🩺", "Диагностика", "Управление › Здоровье"),
        f"{kv('💾', 'База', str(db.get('size_mb', 0)) + ' МБ')}   "
        f"{kv('🧬', 'Схема', report.get('schema_version'))}",
        f"{kv('⚙️', 'Джобы', len(scan.get('background_tasks') or []))}   "
        f"{kv('📦', 'Копий', db.get('backup_count', 0))}",
        DIV,
        "🧾 **Готовность**",
    ]
    for check in checks:
        lines.append(f"{dot(bool(check.get('ok')))} {check.get('label')}")
    missing = runtime.get("missing_background_tasks") or []
    if missing:
        lines.append(f"\n⚠️ Не запущены: `{', '.join(missing)}`")
    problems = report.get("recent_problem_events") or []
    lines.append("\n🚨 **Последние проблемы**")
    if not problems:
        lines.append(empty("Тихо."))
    for event in problems[:4]:
        lines.append(f"• `{fmt_dt(event.get('created_at'))}` {str(event.get('message'))[:80]}")
    lines.append("\n💡 " + "\n💡 ".join(report.get("recommendations") or []))
    return "\n".join(lines)


def keyboard() -> list[list[Button]]:
    return [
        [Button.inline("📜 События", b"dg:ev"), Button.inline("🔄 Сканы", b"dg:sc")],
        [Button.inline("🧾 История настроек", b"dg:hi")],
        [Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"dg")],
    ]


def back_keyboard() -> list[list[Button]]:
    return [[Button.inline("⬅️ Диагностика", b"dg")]]


async def _show_report(click: Click) -> None:
    report = await build_diagnostics()
    checks = setup_checks()
    text = report_card(report, checks)
    if not setup_ready(checks):
        text += "\n\n⚠️ __Установка настроена не полностью.__"
    await safe_edit(click.event, text, buttons=keyboard(), link_preview=False)


async def _show_events(click: Click) -> None:
    events = await get_events(limit=TAIL, level=None)
    lines = ["📜 **События**", DIV]
    if not events:
        lines.append(empty("Пусто."))
    for event in events:
        lines.append(f"`{fmt_dt(event.get('created_at'))}` · {event.get('level')} · "
                     f"{str(event.get('message'))[:90]}")
    await safe_edit(click.event, "\n".join(lines), buttons=back_keyboard())


async def _show_scans(click: Click) -> None:
    runs = await get_scan_runs(limit=TAIL)
    lines = ["🔄 **Сканы**", DIV]
    if not runs:
        lines.append(empty("Запусков не было."))
    for run in runs:
        lines.append(f"`{fmt_dt(run.get('started_at'))}` · {run.get('status')} · "
                     f"найдено `{run.get('found', 0)}`"
                     + (f" · ⚠️ {str(run.get('last_error'))[:60]}" if run.get("last_error") else ""))
    await safe_edit(click.event, "\n".join(lines), buttons=back_keyboard())


async def _show_history(click: Click) -> None:
    rows = await get_settings_history(limit=TAIL)
    lines = ["🧾 **История настроек**", DIV]
    if not rows:
        lines.append(empty("Правок не было."))
    for row in rows:
        lines.append(f"`{fmt_dt(row.get('created_at'))}` · {row.get('key')}")
    await safe_edit(click.event, "\n".join(lines), buttons=back_keyboard())


async def handle(click: Click) -> None:
    action = click.arg(1)
    if action == "ev":
        await _show_events(click)
        return
    if action == "sc":
        await _show_scans(click)
        return
    if action == "hi":
        await _show_history(click)
        return
    await _show_report(click)


def register(router: CallbackRouter) -> None:
    router.group("dg", admin=True)(handle)
