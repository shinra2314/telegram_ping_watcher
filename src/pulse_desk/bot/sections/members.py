"""Люди и расписанный доступ: хаб управления, карточка участника, окна доступа.

Расписание — источник правды для ``bot_role``: решение считается на лету
(:mod:`pulse_desk.access_control`), а джоб ``access-scheduler`` лишь греет кэш.
Поэтому каждое действие здесь сбрасывает ``state.access_cache`` для этого
человека — иначе закрытый доступ ещё несколько минут выглядел бы открытым.

Тот же набор операций доступен текстом через ``/access`` в service.py; обе
поверхности зовут функции этого модуля, чтобы не разъехаться.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon import Button

from database import (
    create_access_window, create_bot_key, create_disable_until_window, deactivate_access_window, get_access_audit,
    get_bot_member, list_access_windows, list_all_access_windows, list_bot_members,
    member_engagement_stats, record_access_audit, set_access_window_active,
    set_bot_member_blocked, set_member_default_policy,
)

from ...access_control import (
    find_undoable, parse_repeat_rule, plan_undo, resolve_access, window_from_row,
)
from ...app_ctx import state
from ...bot_permissions import dump_permissions, full_permissions
from ...bot_prefs import next_hhmm_datetime
from ...security import generate_access_key
from ...telegram_accounts import restart_monitoring
from ..cards import management_card, member_card, members_header
from ..keyboards import (
    management_grid, member_access_keyboard, member_card_keyboard, members_list_keyboard,
)
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, fmt_dt
from . import keys as keys_section

DAY_NAMES = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}
ACCESS_ACTIONS = ("close", "open", "close2h", "morning", "undo", "log")


async def render_management():
    return management_card(), management_grid()


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


async def resolve_member_arg(query: str) -> Optional[dict]:
    q = query.strip().lstrip("@").lower()
    members = await list_bot_members()
    for m in members:
        if q == str(m.get("tg_id")) or q == (m.get("tg_username") or "").lower():
            return m
    for m in members:
        if q and q in (m.get("name") or "").lower():
            return m
    return None


def describe_repeat(rep: dict, row: dict) -> str:
    tz = row.get("timezone") or "UTC"
    rtype = rep.get("type")
    if rtype == "daily":
        return f"ежедневно {rep.get('from', '?')}–{rep.get('to', '?')} ({tz})"
    if rtype == "weekly":
        days = ",".join(DAY_NAMES.get(d, str(d)) for d in rep.get("days", []))
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
            lines.append(f"`#{r['id']}` · {kind} · prio {r['priority']}\n   {describe_repeat(parse_repeat_rule(r['repeat_rule']), r)}")
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


async def open_member_access(tg: int, actor_id: int) -> int:
    """Cancel manual blackouts (the high-priority one-shots created by 'off')."""
    cancelled_ids: list[int] = []
    for w in await list_access_windows(tg):
        if not w["enabled"] and w["priority"] >= 1000:
            await deactivate_access_window(int(w["id"]))
            cancelled_ids.append(int(w["id"]))
    await record_access_audit(tg, None, "manual_on", f"admin:{actor_id}", None,
                              {"cancelled_ids": cancelled_ids})
    state.access_cache.pop(tg, None)
    return len(cancelled_ids)


async def add_access_window(tg: int, kind: str, frm: str, to: str, days: list[int],
                            tz: str, actor_id: int) -> dict:
    """«work» opens the member inside the range (and closes everything else),
    «mute» closes them inside it. Shared by ``/access … work|mute`` and the panel.
    """
    repeat: dict = {"type": "weekly" if days else "daily", "from": frm, "to": to}
    if days:
        repeat["days"] = sorted(set(days))
    enabled = kind == "work"
    if enabled:
        # A working-hours window only means something if outside it is closed.
        await set_member_default_policy(tg, "deny")
    row = await create_access_window(tg, enabled=enabled, repeat_rule=repeat, timezone=tz,
                                     priority=200, label=kind, created_by=actor_id)
    await record_access_audit(tg, int(row["id"]), "create", f"admin:{actor_id}", None, repeat)
    state.access_cache.pop(tg, None)
    return row


async def remove_access_window(tg: int, window_id: int, actor_id: int) -> None:
    await deactivate_access_window(window_id)
    await record_access_audit(tg, window_id, "delete", f"admin:{actor_id}", None, None)
    state.access_cache.pop(tg, None)


async def close_member_access(tg: int, until_iso: Optional[str], actor_id: int) -> dict:
    """Close now, until a UTC moment or for good (``None``) — ``/access … off``."""
    row = await create_disable_until_window(tg, until_iso, created_by=actor_id)
    await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{actor_id}", None, {"until": until_iso})
    state.access_cache.pop(tg, None)
    return row


async def apply_undo(tg: int, plan: dict) -> None:
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


async def _handle_admin_hub(click: Click) -> None:
    event = click.event
    action = click.arg(1)
    if action == "restart":
        result = await restart_monitoring()
        await event.answer(f"♻️ Перезапущено аккаунтов: {result.get('restarted', 0)}", alert=True)
        text, kb = await render_management()
        await safe_edit(event, text, buttons=kb)
        return
    if action == "home":
        text, kb = await render_management()
        await safe_edit(event, text, buttons=kb)
        return
    if action == "members":
        text, kb = await render_members()
        await safe_edit(event, text, buttons=kb)
        return
    if action == "access":
        await safe_edit(event, await render_access_overview(),
                        buttons=[[Button.inline("⬅️ Управление", b"adm:home")]])
        return
    if action in ("newkey", "newkeyp"):
        premium = action == "newkeyp"
        secret = generate_access_key()
        key = await create_bot_key(
            "премиум" if premium else "", secret,
            "premium" if premium else "viewer", None,
            dump_permissions(full_permissions()),
        )
        username = state.bot_username
        link = f"https://t.me/{username}?start={secret}" if username else ""
        title = "⚡ **Новый премиум-ключ**" if premium else "🔑 **Новый ключ**"
        body = title + "\n" + DIV + f"\n🔐 `{secret}`"
        if premium:
            body += "\n⚡ Держатель получает рассылки мгновенно, без модерации."
        if link:
            body += f"\n🔗 {link}"
        await event.respond(body, link_preview=False)
        await event.answer("Ключ создан")
        screen = await keys_section.render_panel(int(key["id"]))
        if screen:
            text, kb = screen
            await event.respond("⚙️ **Настройте ключ перед отправкой**\n\n" + text, buttons=kb)
        return
    await event.answer("Неизвестная команда", alert=True)


async def _handle_member(click: Click) -> None:
    event = click.event
    tg = click.int_arg(2)
    if tg is None:
        await event.answer("Некорректная команда", alert=True)
        return
    action = click.arg(1)
    if action == "open":
        res = await open_member_view(tg)
        if res is None:
            await event.answer("Участник не найден", alert=True)
            return
        text, kb = res
        await safe_edit(event, text, buttons=kb)
        return
    if action in ("block", "unblock"):
        await set_bot_member_blocked(tg, action == "block")
        state.access_cache.pop(tg, None)
        await event.answer("Заблокирован" if action == "block" else "Разблокирован")
        res = await open_member_view(tg)
        if res is not None:
            text, kb = res
            await safe_edit(event, text, buttons=kb)
        return
    if action == "access":
        member = await get_bot_member(tg)
        if not member:
            await event.answer("Участник не найден", alert=True)
            return
        await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))
        return
    await event.answer("Неизвестная команда", alert=True)


async def _handle_access(click: Click) -> None:
    event = click.event
    tg = click.int_arg(2)
    action = click.arg(1)
    if tg is None or action not in ACCESS_ACTIONS:
        await event.answer("Некорректная команда", alert=True)
        return
    member = await get_bot_member(tg)
    if not member:
        await event.answer("Участник не найден", alert=True)
        return
    if action == "log":
        log = await get_access_audit(tg)
        lines = ["🧾 **История доступа**", DIV]
        if not log:
            lines.append("📭 __Пусто.__")
        else:
            lines += [f"`{fmt_dt(a['created_at'])}` · {a['action']} · _{a['actor']}_" for a in log]
        await safe_edit(event, "\n".join(lines),
                        buttons=[[Button.inline("⬅️ Назад", f"mem:access:{tg}".encode())]])
        return
    if action == "open":
        cancelled = await open_member_access(tg, event.sender_id)
        await event.answer(f"🟢 Доступ открыт ({cancelled})")
    elif action == "undo":
        target = find_undoable(await get_access_audit(tg, limit=50))
        if not target:
            await event.answer("Нечего отменять", alert=True)
        else:
            plan = plan_undo(target)
            await apply_undo(tg, plan)
            await record_access_audit(
                tg, target.get("schedule_id"), "undo", f"admin:{event.sender_id}",
                None, {"undone_audit_id": int(target["id"]), "plan": plan},
            )
            state.access_cache.pop(tg, None)
            await event.answer("↩️ Отменено")
    else:
        until_iso = None
        note = "🔴 Доступ закрыт"
        if action == "close2h":
            until = datetime.now(timezone.utc) + timedelta(hours=2)
            until_iso = until.replace(microsecond=0, tzinfo=None).isoformat()
            note = "🔴 Закрыт на 2ч"
        elif action == "morning":
            target_local = next_hhmm_datetime(datetime.now().astimezone(), "08:00")
            if target_local:
                until_iso = target_local.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()
            note = "🔴 Закрыт до 08:00"
        row = await create_disable_until_window(tg, until_iso, created_by=event.sender_id)
        await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{event.sender_id}",
                                  None, {"until": until_iso})
        state.access_cache.pop(tg, None)
        await event.answer(note)
    member = await get_bot_member(tg)
    await safe_edit(event, await render_member_access(member), buttons=member_access_keyboard(tg))


async def _handle_legacy_block(click: Click) -> None:
    """`blockmember_<id>` / `unblockmember_<id>` — кнопки со старых сообщений."""
    try:
        member_id = int(click.tail())
    except ValueError:
        await click.event.answer("Некорректная команда", alert=True)
        return
    blocked = click.data.startswith("blockmember_")
    await set_bot_member_blocked(member_id, blocked)
    state.access_cache.pop(member_id, None)
    await click.event.answer("Пользователь заблокирован" if blocked else "Пользователь разблокирован")


async def _handle_legacy_access(click: Click) -> None:
    """`accshow_/accoff_/accon_<tg>` — быстрые действия со старых карточек."""
    try:
        tg = int(click.tail())
    except ValueError:
        await click.event.answer("Некорректная команда", alert=True)
        return
    member = await get_bot_member(tg)
    if not member:
        await click.event.answer("Участник не найден", alert=True)
        return
    if click.data.startswith("accoff_"):
        row = await create_disable_until_window(tg, None, created_by=click.sender_id)
        await record_access_audit(tg, int(row["id"]), "manual_off", f"admin:{click.sender_id}",
                                  None, {"until": None})
        state.access_cache.pop(tg, None)
        await click.event.answer("🔴 Доступ закрыт")
    elif click.data.startswith("accon_"):
        cancelled = await open_member_access(tg, click.sender_id)
        await click.event.answer(f"🟢 Доступ открыт ({cancelled})")
    else:
        state.access_cache.pop(tg, None)
    await safe_edit(click.event, await render_member_access(member))


def register(router: CallbackRouter) -> None:
    router.group("adm", admin=True)(_handle_admin_hub)
    router.group("mem", admin=True)(_handle_member)
    router.group("acc", admin=True)(_handle_access)
    router.group("blockmember", "unblockmember", sep="_", admin=True)(_handle_legacy_block)
    router.group("accshow", "accoff", "accon", sep="_", admin=True)(_handle_legacy_access)
