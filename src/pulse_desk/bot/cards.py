"""Pure composed-card renderers for the bot UI.

Take already-fetched data, return Markdown text. No I/O, no Telethon — so they
unit-test without a running bot. Data fetching lives in service.py.
"""
from __future__ import annotations

from typing import Optional

from .chrome import empty, header, kv
from .views import DIV


def home_card(
    *,
    role: str,
    new_pings: int,
    urgent: int,
    accounts_online: int,
    accounts_total: int,
    fresh_checks: int,
    last_scan: str,
) -> str:
    """Live dashboard shown on the home screen."""
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    accounts = f"{accounts_online}/{accounts_total}"
    return "\n".join([
        header("🛰", "Pulse Desk", badge),
        f"{kv('🆕', 'Новых пингов', new_pings)}   {kv('🎁', 'Срочных', urgent)}",
        f"{kv('🛰', 'Аккаунты', accounts)}   {kv('💸', 'Чеки', fresh_checks)}",
        kv("🔄", "Скан", last_scan),
        DIV,
        "Выберите раздел 👇",
    ])


def summary_card(*, analytics: dict, market: Optional[dict], system: dict) -> str:
    """Combined read-only card: pings stats + market + system status."""
    db_str = f"{system['db_mb']:.1f} MB"
    accounts = f"{system['accounts_online']}/{system['accounts_total']}"
    lines = [
        header("📊", "Сводка"),
        f"{kv('📨', 'Записей', analytics['total_pings'])}   "
        f"{kv('🆕', 'Новых', analytics['new_pings'])}   "
        f"{kv('⭐', 'Избр', analytics['favorites'])}",
        DIV,
        "💹 **Курсы**",
    ]
    if market:
        lines.append(f"🟠 BTC `${market['btc']:,}`   🔷 ETH `${market['eth']:,}`")
        lines.append(f"💎 TON `${market['ton']:.3f}`   🟣 SOL `${market['sol']:.2f}`")
    else:
        lines.append(empty("Курсы пока недоступны."))
    lines += [
        DIV,
        f"🛰 **Система** · `v{system['version']}`",
        f"{kv('⏱', 'Uptime', system['uptime'])}   {kv('💾', 'База', db_str)}",
        f"{kv('🛰', 'Аккаунты', accounts)}   {kv('🔄', 'Скан', system['last_scan'])}",
    ]
    return "\n".join(lines)
