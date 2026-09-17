"""🏖 Отпуск: пауза мелких уведомлений владельцу и заместитель на время.

`vc` — экран; `vc:on:<дней>` — начать; `vc:off` — закончить досрочно;
`vc:w` — присылать ли победы; `vc:dl` — выбрать заместителя из участников;
`vc:d:<tg_id>` — назначить; `vc:dx` — снять заместителя.

Заместитель получает роль владельца на период (и копии владельческих
уведомлений); по окончании — прежнюю роль, это делает джоб ``bot-janitor``
через :func:`end_vacation`. Сам владелец остаётся владельцем всегда: его права
идут из ``ADMIN_ID``, а не из таблицы участников.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from telethon import Button

from database import get_bot_member, get_setting, list_bot_members, set_bot_member_role, set_setting

from ...app_ctx import logger, state
from ...common import record_app_event
from ...vacation import (
    PRESET_DAYS, SETTINGS_KEY, ends_label, expired, is_active, normalize, start, stop,
)
from ..chrome import empty, header, kv
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV


async def load() -> dict[str, Any]:
    return normalize(await get_setting(SETTINGS_KEY, None))


async def save(cfg: dict[str, Any]) -> None:
    await set_setting(SETTINGS_KEY, cfg)


def card(cfg: dict[str, Any], now: datetime) -> str:
    active = is_active(cfg, now)
    delegate = cfg.get("delegate")
    lines = [
        header("🏖", "Отпуск", "Управление › Отпуск"),
        kv("🔔", "Режим", f"включён до {ends_label(cfg)}" if active else "выключен"),
        kv("🏆", "Победы", "без звука" if cfg.get("mute_wins") else "со звуком"),
        kv("👤", "Заместитель", delegate.get("name") if delegate else "нет"),
        DIV,
        "Пока режим включён, упоминания и розыгрыши приходят без звука — ни одно не теряется; "
        "курсы и «восстановилось» не приходят.",
        "Сбои задач и проблемы аккаунтов приходят всегда.",
    ]
    if delegate:
        lines.append(f"\n👤 __{delegate.get('name')} на это время получает права владельца и копии уведомлений; "
                     "после — прежнюю роль автоматически.__")
    return "\n".join(lines)


def keyboard(cfg: dict[str, Any], now: datetime) -> list[list[Button]]:
    rows: list[list[Button]] = []
    if is_active(cfg, now):
        rows.append([Button.inline("⏹ Закончить отпуск", b"vc:off")])
    else:
        rows.append([Button.inline(f"🏖 {d} дн", f"vc:on:{d}".encode()) for d in PRESET_DAYS])
    rows.append([
        Button.inline("🏆 Победы: без звука" if not cfg.get("mute_wins") else "🏆 Победы: со звуком",
                      b"vc:w"),
    ])
    if cfg.get("delegate"):
        rows.append([Button.inline("👤 Снять заместителя", b"vc:dx")])
    else:
        rows.append([Button.inline("👤 Выбрать заместителя", b"vc:dl")])
    rows.append([Button.inline("⬅️ Управление", b"adm:home"), Button.inline("🔄 Обновить", b"vc")])
    return rows


async def _show(click: Click, note: str = "") -> None:
    cfg = await load()
    now = datetime.now()
    text = card(cfg, now)
    await safe_edit(click.event, f"{note}\n\n{text}" if note else text, buttons=keyboard(cfg, now))


async def _elevate(cfg: dict[str, Any]) -> None:
    delegate = cfg.get("delegate")
    if delegate:
        await set_bot_member_role(int(delegate["tg_id"]), "admin")
        state.access_cache.pop(int(delegate["tg_id"]), None)


async def _restore(cfg: dict[str, Any]) -> None:
    delegate = cfg.get("delegate")
    if delegate:
        await set_bot_member_role(int(delegate["tg_id"]), str(delegate.get("prev_role") or "viewer"))
        state.access_cache.pop(int(delegate["tg_id"]), None)


async def end_vacation(reason: str = "срок вышел") -> bool:
    """Wind vacation down: delegate back to their role, both sides told. True if it ran."""
    from ...bot_notify import send_admin_bot_message, send_member_bot_message

    cfg = await load()
    if not cfg.get("active"):
        return False
    await _restore(cfg)
    await save(stop(cfg))
    delegate = cfg.get("delegate")
    await record_app_event("INFO", "vacation", "Vacation ended", {"reason": reason})
    await send_admin_bot_message(f"🏖 **Отпуск закончился** ({reason}). Уведомления снова приходят как обычно.")
    if delegate:
        try:
            await send_member_bot_message(int(delegate["tg_id"]),
                                          "🏖 Отпуск владельца закончился — права заместителя сняты. Спасибо!")
        except Exception:
            logger.debug("Could not notify the vacation delegate", exc_info=True)
    return True


async def check_expiry(now: Optional[datetime] = None) -> bool:
    """Called by the janitor every minute."""
    cfg = await load()
    if expired(cfg, now or datetime.now()):
        return await end_vacation()
    return False


async def handle(click: Click) -> None:
    action = click.arg(1)
    cfg = await load()
    now = datetime.now()
    if action == "on":
        days = click.int_arg(2) or PRESET_DAYS[0]
        cfg = start(cfg, now, days)
        await save(cfg)
        await _elevate(cfg)
        await record_app_event("INFO", "vacation", "Vacation started", {"until": cfg["until"]})
        delegate = cfg.get("delegate")
        if delegate:
            from ...bot_notify import send_member_bot_message

            await send_member_bot_message(
                int(delegate["tg_id"]),
                f"🏖 Владелец в отпуске до {ends_label(cfg)}. На это время у вас права владельца — /menu.",
            )
        await click.event.answer(f"Отпуск до {ends_label(cfg)}")
        await _show(click)
        return
    if action == "off":
        await end_vacation("закончен вручную")
        await click.event.answer("Отпуск закончен")
        await _show(click)
        return
    if action == "w":
        cfg["mute_wins"] = not cfg.get("mute_wins")
        await save(cfg)
        await _show(click)
        return
    if action == "dl":
        members = [m for m in await list_bot_members() if not m.get("blocked")]
        rows = [[Button.inline(f"👤 {m.get('name') or m.get('tg_username') or m['tg_id']}"[:40],
                               f"vc:d:{m['tg_id']}".encode())] for m in members[:20]]
        rows.append([Button.inline("⬅️ Отпуск", b"vc")])
        text = "👤 **Кто заменит вас на время отпуска?**\n" + DIV + "\n"
        text += "Он получит права владельца, пока отпуск идёт." if members else empty("Участников пока нет.")
        await safe_edit(click.event, text, buttons=rows)
        return
    if action == "d":
        member = await get_bot_member(click.int_arg(2) or 0)
        if not member:
            await click.event.answer("Участник не найден", alert=True)
            return
        await _restore(cfg)
        cfg["delegate"] = {
            "tg_id": int(member["tg_id"]),
            "name": member.get("name") or (f"@{member['tg_username']}" if member.get("tg_username") else str(member["tg_id"])),
            # A delegate re-picked mid-vacation must not remember 'admin' as their old role.
            "prev_role": member.get("role") if member.get("role") != "admin" else "viewer",
        }
        await save(cfg)
        if is_active(cfg, now):
            await _elevate(cfg)
        await _show(click, f"👤 Заместитель: {cfg['delegate']['name']}")
        return
    if action == "dx":
        await _restore(cfg)
        cfg["delegate"] = None
        await save(cfg)
        await _show(click, "Заместитель снят")
        return
    await _show(click)


def register(router: CallbackRouter) -> None:
    router.group("vc", admin=True)(handle)
