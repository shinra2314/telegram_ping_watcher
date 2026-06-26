"""Pure composed-card renderers for the bot UI.

Take already-fetched data, return Markdown text. No I/O, no Telethon — so they
unit-test without a running bot. Data fetching lives in service.py.
"""
from __future__ import annotations

from typing import Optional

from .chrome import empty, header, kv
from .views import DIV, fmt_dt


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


_BADGES = {"critical": "🔥", "high": "⚡"}
_PING_TEXT_CAP = 3500


def feed_badge(priority_label: Optional[str]) -> str:
    return _BADGES.get(priority_label or "", "•")


def feed_header(active_label: str, count: int) -> str:
    out = header("📡", "Мониторинг", f"Домой › Мониторинг › {active_label}")
    if count == 0:
        return out + "\n" + empty("Пока ничего.")
    return out + f"\nЗаписей: `{count}` · нажми на строку 👇"


def ping_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Мониторинг › #{ping.get('id')}"
    tags = [f"🏷 {ping.get('priority_label') or 'normal'}"]
    if ping.get("is_giveaway"):
        tags.append("🎁 розыгрыш")
    if ping.get("is_win"):
        tags.append("🏆 победа")
    if ping.get("is_check"):
        tags.append("💸 чек")
    text = (ping.get("text") or "—")[:_PING_TEXT_CAP]
    lines = [
        header(badge, f"Пинг #{ping.get('id')}", crumb),
        f"📅 `{fmt_dt(ping.get('detected_at'))}`  ·  {ping.get('chat') or '?'}",
        "  ·  ".join(tags),
        DIV,
        text,
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)
