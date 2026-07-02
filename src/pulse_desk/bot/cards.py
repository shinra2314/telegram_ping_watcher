"""Pure composed-card renderers for the bot UI.

Take already-fetched data, return Markdown text. No I/O, no Telethon — so they
unit-test without a running bot. Data fetching lives in service.py.
"""
from __future__ import annotations

from typing import Optional

from .chrome import bar, chip, empty, header, kv
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
    urgent_cell = f"{kv('🎁', 'Срочных', urgent)}{' 🔥' if urgent else ''}"
    return "\n".join([
        header("🛰", "Pulse Desk", badge),
        f"{kv('🆕', 'Новых пингов', new_pings)}   {urgent_cell}",
        f"{kv('🛰', 'Аккаунты', accounts)} {bar(accounts_online, accounts_total)}"
        f"   {kv('💸', 'Чеки', fresh_checks)}",
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
    tags = [chip(f"🏷 {ping.get('priority_label') or 'normal'}")]
    if ping.get("is_giveaway"):
        tags.append(chip("🎁 розыгрыш"))
    if ping.get("is_win"):
        tags.append(chip("🏆 победа"))
    if ping.get("is_check"):
        tags.append(chip("💸 чек"))
    text = (ping.get("text") or "—")[:_PING_TEXT_CAP]
    lines = [
        header(badge, f"Пинг #{ping.get('id')}", crumb),
        f"📅 `{fmt_dt(ping.get('detected_at'))}`  ·  {ping.get('chat') or '?'}",
        " ".join(tags),
        DIV,
        text,
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)


def giveaways_header(stats: dict, need_count: int) -> str:
    out = header("🎁", "Розыгрыши", "Домой › Розыгрыши")
    line1 = f"{kv('🟢', 'К действию', need_count)}   {kv('🏆', 'Призы', stats.get('claim_prize', 0))}"
    line2 = f"{kv('⏰', 'Просрочено', stats.get('overdue', 0))}   {kv('⏳', 'Ждут', stats.get('waiting_result', 0))}"
    body = f"{line1}\n{line2}"
    if need_count == 0:
        body += "\n" + empty("Срочных нет — всё под контролем.")
    return f"{out}\n{body}"


def giveaway_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Розыгрыши › #{ping.get('id')}"
    deadline = ping.get("deadline_at")
    lines = [
        header(badge, f"Розыгрыш #{ping.get('id')}", crumb),
        f"⏰ Дедлайн: `{fmt_dt(deadline) if deadline else '—'}`  ·  {ping.get('chat') or '?'}",
        DIV,
        (ping.get("text") or "—")[:_PING_TEXT_CAP],
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)


def management_card() -> str:
    return header("⚙️", "Управление", "Домой › Управление") + "\nВыберите раздел 👇"


def scan_card(status: dict, last_scan: str) -> str:
    """Scan control panel: live progress while running, last result when idle."""
    running = bool(status.get("running"))
    out = [header("🔄", "Скан", "Домой › Управление › Скан")]
    if running:
        done = int(status.get("processed_accounts") or 0)
        total = int(status.get("total_accounts") or 0)
        out.append(f"🟡 **Идёт сканирование** {bar(done, total)} `{done}/{total}`")
        current = status.get("current_channel") or status.get("current_account")
        if current:
            out.append(kv("📡", "Сейчас", current))
        out.append(kv("🆕", "Найдено", status.get("found") or 0))
    else:
        out.append("⚪️ Скан не запущен.")
        out.append(kv("🕐", "Последний", last_scan))
        if status.get("last_error"):
            out.append(kv("⚠️", "Ошибка", status["last_error"]))
    return "\n".join(out)


def restart_confirm_card() -> str:
    return "\n".join([
        header("♻️", "Перезапуск", "Домой › Управление › Рестарт"),
        "Переподключить все аккаунты мониторинга?",
        "__Активный скан будет прерван.__",
    ])


def keys_card(keys: list[dict]) -> str:
    """Keys panel text; the revoke buttons live in `keys_keyboard`."""
    out = [header("🔑", "Ключи доступа", "Домой › Управление › Ключи")]
    if not keys:
        out.append(empty("Ключей нет — создайте кнопкой ниже."))
        return "\n".join(out)
    for k in keys:
        exp = fmt_dt(k.get("expires_at")) if k.get("expires_at") else "бессрочно"
        out.append(f"`#{k['id']}` **{k.get('label') or '—'}** · 👥 {k.get('member_count', 0)} · ⏳ {exp}")
    return "\n".join(out)


def members_header(count: int) -> str:
    out = header("👥", "Люди", "Домой › Управление › Люди")
    if count == 0:
        return out + "\n" + empty("Пока никого.")
    return out + f"\nУчастников: `{count}` · нажми на запись 👇"


def member_card(member: dict, access_open: bool) -> str:
    tg = member.get("tg_id")
    uname = f"@{member['tg_username']}" if member.get("tg_username") else "—"
    blocked = bool(member.get("blocked"))
    crumb = f"Домой › Управление › Люди › {member.get('name') or tg}"
    state_line = (
        f"{'🚫 заблокирован' if blocked else '🟢 активен'}  ·  "
        f"⏰ {'🟢 открыт' if access_open else '🔴 закрыт'}"
    )
    return "\n".join([
        header("👤", str(member.get("name") or tg), crumb),
        uname,
        kv("🔑", "Ключ", member.get("key_label") or "—"),
        state_line,
        kv("🕐", "Был", fmt_dt(member.get("last_seen_at"))),
    ])
