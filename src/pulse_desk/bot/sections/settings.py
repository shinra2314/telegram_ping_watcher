"""Настройки мониторинга владельца: юзернеймы, ключевые слова, уведомления, дайджест.

Правило одно: ничего не писать в базу напрямую — все изменения идут через
:mod:`pulse_desk.bot.persist`, который сохраняет, применяет к работающему
``watch_settings``, пишет аудит и публикует событие. Сохранённое, но не
применённое значение оставило бы сканер на старых правилах до перезапуска.

``st_x`` (отмена ввода) — единственный колбэк этого раздела, доступный не
владельцу: он просто очищает подвисшую заявку на ввод.
"""
from __future__ import annotations

import re
from typing import Any

from telethon import Button

from database import get_setting, set_setting
from telegram_ping_watcher import normalize_usernames

from ... import watch_settings as ws
from ...app_ctx import state
from ...bot_prefs import (
    KEYWORD_SCOPES, parse_hhmm, parse_quiet_hours_input, render_keyword_list_text,
    render_notification_settings_text, render_tracking_text,
)
from ..pending import InputRejected, cancel_pending, clear_pending, prompt_pending, register_prompt
from ..persist import (
    save_digest, save_keywords, save_notifications, save_runtime, save_tracking,
)
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..settings_schema import RUNTIME_FIELDS, apply_value, sanitized_runtime
from ..views import DIV

BACK = [Button.inline("⬅️ Назад", b"st")]


def resolve_removal(items: list[str], text: str) -> int | None:
    """Match a removal request against a numbered list: by index or exact text."""
    cleaned = text.strip().lstrip("@")
    if cleaned.isdigit():
        idx = int(cleaned) - 1
        return idx if 0 <= idx < len(items) else None
    lowered = cleaned.lower()
    for i, item in enumerate(items):
        if item.lstrip("@").lower() == lowered:
            return i
    return None


# ---- menus --------------------------------------------------------------
def root_menu() -> tuple[str, list[list[Button]]]:
    text = "⚙️ **Настройки**\n" + DIV + "\nУправление мониторингом прямо из бота."
    buttons = [
        [Button.inline("👁 Юзернеймы", b"st_u"), Button.inline("🔑 Ключевые слова", b"st_k")],
        [Button.inline("🔔 Уведомления", b"st_n"), Button.inline("🎛 Работа", b"st_r")],
        [Button.inline("🧩 Правила", b"st_l"), Button.inline("🧾 Чеки", b"ck")],
        [Button.inline("⬅️ Меню", b"menu_main")],
    ]
    return text, buttons


def usernames_menu() -> tuple[str, list[list[Button]]]:
    buttons = [
        [Button.inline("➕ Добавить", b"st_u_add"), Button.inline("➖ Удалить", b"st_u_rm")],
        BACK,
    ]
    return render_tracking_text(state.ping_usernames), buttons


def keywords_root_menu() -> tuple[str, list[list[Button]]]:
    text = "🔑 **Ключевые слова**\n" + DIV + "\nВыберите список."
    scope_buttons = [Button.inline(label, f"st_k_{code}".encode())
                     for code, (_, label) in KEYWORD_SCOPES.items()]
    buttons = [scope_buttons[i:i + 2] for i in range(0, len(scope_buttons), 2)]
    buttons.append(BACK)
    return text, buttons


async def keyword_list_menu(code: str) -> tuple[str, list[list[Button]]]:
    key, label = KEYWORD_SCOPES[code]
    values = await ws.load_keyword_settings()
    buttons = [
        [Button.inline("➕ Добавить", f"st_k_{code}_add".encode()),
         Button.inline("➖ Удалить", f"st_k_{code}_rm".encode())],
        [Button.inline("⬅️ Назад", b"st_k")],
    ]
    return render_keyword_list_text(label, values.get(key) or []), buttons


async def notifications_menu() -> tuple[str, list[list[Button]]]:
    from ...autoclean import SETTINGS_KEY, label, owner_hours
    from ...ignored_chats import load as ignored_load

    notif = await ws.load_notification_settings()
    digest_cfg = await ws.load_digest_settings()
    autoclean = label(owner_hours(await get_setting(SETTINGS_KEY, None)))
    quiet = notif.get("quiet_hours") or {}
    mark = lambda value: "✅" if value else "❌"  # noqa: E731
    buttons = [
        [Button.inline(f"{mark(notif.get('enabled', True))} Уведомления", b"st_n_en"),
         Button.inline(f"{mark(quiet.get('enabled'))} Тихие часы", b"st_n_q")],
        [Button.inline(f"{mark(notif.get('include_giveaways', True))} Розыгрыши", b"st_n_gw"),
         Button.inline(f"{mark(notif.get('include_wins', True))} Победы", b"st_n_wn")],
        [Button.inline(
            "🛡 Модерация рассылок: вкл" if notif.get("moderation_mode") == "moderated"
            else "📤 Модерация рассылок: авто",
            b"st_n_md",
        )],
        [Button.inline("🕘 Часы тишины…", b"st_n_qt"), Button.inline("⏱ Кулдаун…", b"st_n_cd")],
        [Button.inline(f"{mark(digest_cfg.get('enabled'))} Дайджест", b"st_d_en"),
         Button.inline("🕘 Время дайджеста…", b"st_d_tm")],
        [Button.inline(f"🧹 Удалять мелкие: {autoclean}", b"st_n_ac")],
        [Button.inline(f"🔇 Игнор-чаты: {len(await ignored_load())}", b"igc")],
        BACK,
    ]
    text = render_notification_settings_text(notif, digest_cfg)
    text += (f"\n🧹 Мелкие уведомления (упоминания, курсы, «восстановилось»): удалять {autoclean}."
             "\n__Победы и розыгрыши не удаляются никогда.__")
    return text, buttons


# ---- typed input --------------------------------------------------------
async def _consume_track_add(event, pending: dict, raw: str) -> None:
    additions = normalize_usernames(re.split(r"[\s,;]+", raw))
    if not additions:
        raise InputRejected("❌ Не распознал юзернеймы.")
    merged = list(state.ping_usernames)
    added = [u for u in additions if u not in merged]
    merged.extend(added)
    if added:
        await save_tracking(merged)
    note = "✅ Добавлено: " + ", ".join(added) if added else "ℹ️ Уже в списке."
    text, buttons = usernames_menu()
    await event.respond(f"{note}\n\n{text}", buttons=buttons)


async def _consume_track_rm(event, pending: dict, raw: str) -> None:
    items = list(state.ping_usernames)
    idx = resolve_removal(items, raw)
    if idx is None:
        raise InputRejected("❌ Не нашёл такой юзернейм. Пришлите номер из списка.")
    removed = items.pop(idx)
    await save_tracking(items)
    note = f"🗑 Удалён: {removed}"
    if not items:
        note += "\n⚠️ Список пуст — восстановлены юзернеймы из .env."
    text, buttons = usernames_menu()
    await event.respond(f"{note}\n\n{text}", buttons=buttons)


async def _consume_keywords(event, pending: dict, raw: str) -> None:
    kind = pending.get("kind")
    code = pending.get("scope") or "w"
    key, _label = KEYWORD_SCOPES[code]
    values = await ws.load_keyword_settings()
    items = list(values.get(key) or [])
    if kind == "kw_add":
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if code == "g":
            parts = [p.lower() for p in parts]
        added = [p for p in parts if p not in items]
        if not added:
            raise InputRejected("ℹ️ Нечего добавлять — пусто или уже в списке.")
        items.extend(added)
        note = "✅ Добавлено: " + ", ".join(added)
    else:
        idx = resolve_removal(items, raw)
        if idx is None:
            raise InputRejected("❌ Не нашёл такое слово. Пришлите номер из списка.")
        if code in ("w", "g") and len(items) == 1:
            await event.respond("⚠️ Этот список не может быть пустым — удаление отменено.")
            return
        note = f"🗑 Удалено: {items.pop(idx)}"
    values[key] = items
    await save_keywords(values)
    text, buttons = await keyword_list_menu(code)
    await event.respond(f"{note}\n\n{text}", buttons=buttons)


async def _consume_quiet(event, pending: dict, raw: str) -> None:
    parsed = parse_quiet_hours_input(raw)
    if not parsed:
        raise InputRejected("❌ Формат: `23:00-08:00`.")
    notif = await ws.load_notification_settings()
    notif["quiet_hours"] = {"enabled": True, "from": parsed[0], "to": parsed[1]}
    await save_notifications(notif)
    text, buttons = await notifications_menu()
    await event.respond(f"✅ Тихие часы: {parsed[0]}–{parsed[1]}\n\n{text}", buttons=buttons)


async def _consume_cooldown(event, pending: dict, raw: str) -> None:
    try:
        value = max(0, min(3600, int(raw)))
    except ValueError:
        raise InputRejected("❌ Нужно число секунд (0–3600).")
    notif = await ws.load_notification_settings()
    notif["cooldown_seconds"] = value
    await save_notifications(notif)
    text, buttons = await notifications_menu()
    await event.respond(f"✅ Кулдаун: {value} сек\n\n{text}", buttons=buttons)


async def _consume_digest_time(event, pending: dict, raw: str) -> None:
    parsed_time = parse_hhmm(raw)
    if not parsed_time:
        raise InputRejected("❌ Формат: `09:00`.")
    cfg = await ws.load_digest_settings()
    cfg["time"] = parsed_time
    await save_digest(cfg)
    text, buttons = await notifications_menu()
    await event.respond(f"✅ Дайджест в {parsed_time}\n\n{text}", buttons=buttons)


TRACK_ADD = register_prompt(
    "track_add", "Пришлите юзернейм(ы) для отслеживания — через запятую или пробел.",
    _consume_track_add)
TRACK_RM = register_prompt(
    "track_rm", "Пришлите номер из списка или сам юзернейм для удаления.", _consume_track_rm)
KW_ADD = register_prompt(
    "kw_add", "Пришлите ключевое слово (или несколько через запятую).", _consume_keywords)
KW_RM = register_prompt(
    "kw_rm", "Пришлите номер из списка или точный текст слова для удаления.", _consume_keywords)
QUIET = register_prompt(
    "quiet", "Пришлите интервал тихих часов в формате `23:00-08:00`.", _consume_quiet)
COOLDOWN = register_prompt("cooldown", "Пришлите кулдаун в секундах (0–3600).", _consume_cooldown)
DIGEST_TIME = register_prompt(
    "digest_time", "Пришлите время дайджеста в формате `09:00`.", _consume_digest_time)


# ---- тонкие настройки («Работа» в вебе) ---------------------------------
# Поля адресуются **индексом** в RUNTIME_FIELDS: ключи вроде
# `giveaway_min_action_delay_seconds` сами по себе съедают половину callback-а,
# а список одинаков на отрисовке и на клике — он статический.
FIELD_PAGE = 7


async def runtime_values() -> dict[str, Any]:
    return await ws.load_runtime_settings()


def fields_menu(values: dict[str, Any], page: int = 1) -> tuple[str, list[list[Button]]]:
    fields = RUNTIME_FIELDS
    start = (max(1, page) - 1) * FIELD_PAGE
    window = fields[start:start + FIELD_PAGE]
    lines = ["🎛 **Работа**", DIV, "__Как часто сканировать, насколько глубоко, "
             "что считать мёртвым каналом.__"]
    rows: list[list[Button]] = [
        [Button.inline(f"{field.label}: {field.format(values.get(field.key))}"[:60],
                       f"st_f_{start + i}".encode())]
        for i, field in enumerate(window)
    ]
    pager: list[Button] = []
    if page > 1:
        pager.append(Button.inline("◀️", f"st_r_{page - 1}".encode()))
    if len(fields) > start + FIELD_PAGE:
        pager.append(Button.inline("▶️", f"st_r_{page + 1}".encode()))
    if pager:
        rows.append(pager)
    rows.append(BACK)
    return "\n".join(lines), rows


def field_menu(index: int, values: dict[str, Any]) -> tuple[str, list[list[Button]]]:
    field = RUNTIME_FIELDS[index]
    lines = [
        f"🎛 **{field.label}**",
        DIV,
        f"Сейчас: `{field.format(values.get(field.key))}`",
    ]
    if field.range_hint():
        lines.append(f"Допустимо{field.range_hint()}.")
    if field.hint:
        lines.append(f"__{field.hint}__")
    presets = list(field.presets) or list(field.choices)
    chips = [
        Button.inline(str(preset), f"st_v_{index}_{i}".encode())
        for i, preset in enumerate(presets)
    ]
    rows = [chips[i:i + 4] for i in range(0, len(chips), 4)]
    rows.append([Button.inline("✍️ Ввести значение", f"st_i_{index}".encode())])
    rows.append([Button.inline("⬅️ К списку", b"st_r")])
    return "\n".join(lines), rows


async def apply_field(index: int, raw: Any) -> tuple[bool, str]:
    """Проверить значение схемой, сохранить группу целиком, применить на лету."""
    field = RUNTIME_FIELDS[index]
    value, error = field.parse(str(raw))
    if error:
        return False, error
    values = apply_value(await runtime_values(), field, value)
    cleaned = sanitized_runtime(values)
    await save_runtime(cleaned)
    return True, f"✅ {field.label}: {field.format(cleaned.get(field.key))}"


async def _consume_field(event, pending: dict, raw: str) -> None:
    try:
        index = int(pending.get("scope") or -1)
    except (TypeError, ValueError):
        return
    if not 0 <= index < len(RUNTIME_FIELDS):
        await event.respond("❌ Настройка не найдена — откройте список заново.")
        return
    ok, note = await apply_field(index, raw)
    if not ok:
        raise InputRejected(note)
    text, buttons = field_menu(index, await runtime_values())
    await event.respond(f"{note}\n\n{text}", buttons=buttons)


FIELD_INPUT = register_prompt(
    "runtime_field", "Пришлите новое значение.", _consume_field)


# ---- правила уведомлений -------------------------------------------------
# Веб давал визуальный конструктор с пятью полями. В боте форма короче, потому
# что настоящая задача одна: заткнуть шумный источник или, наоборот, поднять
# нужный. Настоящее хранилище — `notifications["rules"]`; ключ `rules_ui` был
# зеркалом для браузера и уходит вместе с ним.
RULE_KINDS = {
    "чат": "chats",
    "слово": "keywords",
    "аккаунт": "usernames",
}
RULE_LIMIT = 50
RULE_HELP = (
    "Формат: `- чат @spamchan` — не уведомлять из этого чата.\n"
    "`+ слово розыгрыш` — уведомлять, если в тексте есть слово.\n"
    "Виды: `чат`, `слово`, `аккаунт`. Знак в начале: `-` молчать, `+` уведомлять."
)


def parse_rule(raw: str) -> tuple[dict | None, str]:
    """Строку человека — в правило. Возвращает (правило, ошибка)."""
    text = (raw or "").strip()
    if not text:
        return None, "❌ Пустое правило."
    notify = True
    if text[0] in "+-":
        notify = text[0] == "+"
        text = text[1:].strip()
    parts = text.split(None, 1)
    if len(parts) < 2:
        return None, "❌ Нужно два слова: вид и значение.\n" + RULE_HELP
    kind, value = parts[0].lower(), parts[1].strip()
    field = RULE_KINDS.get(kind)
    if not field:
        return None, "❌ Неизвестный вид.\n" + RULE_HELP
    if not value:
        return None, "❌ Пустое значение."
    rule = {"name": f"{kind} {value}"[:40], "enabled": True, "notify": notify,
            "usernames": [], "chats": [], "keywords": []}
    rule[field] = [value.lstrip("@")]
    return rule, ""


def describe_rule(rule: dict) -> str:
    mark = "🔔" if rule.get("notify", True) else "🔕"
    return f"{mark} {rule.get('name') or 'правило'}"


async def rules_menu() -> tuple[str, list[list[Button]]]:
    notif = await ws.load_notification_settings()
    rules = [r for r in (notif.get("rules") or []) if isinstance(r, dict)]
    lines = ["🧩 **Правила уведомлений**", DIV]
    if not rules:
        lines.append("📭 __Правил нет — уведомления идут по общим настройкам.__")
    else:
        lines += [f"{i + 1}. {describe_rule(rule)}" for i, rule in enumerate(rules)]
    lines.append(DIV)
    lines.append(RULE_HELP)
    rows: list[list[Button]] = [
        [Button.inline(f"🗑 {describe_rule(rule)}"[:40], f"st_ld_{i}".encode())]
        for i, rule in enumerate(rules[:10])
    ]
    rows.append([Button.inline("➕ Добавить правило", b"st_la")])
    rows.append(BACK)
    return "\n".join(lines), rows


async def _consume_rule(event, pending: dict, raw: str) -> None:
    rule, error = parse_rule(raw)
    if error:
        raise InputRejected(error)
    notif = await ws.load_notification_settings()
    rules = [r for r in (notif.get("rules") or []) if isinstance(r, dict)]
    rules.append(rule)
    notif["rules"] = rules[-RULE_LIMIT:]
    await save_notifications(notif)
    text, buttons = await rules_menu()
    await event.respond(f"✅ Правило добавлено.\n\n{text}", buttons=buttons)


RULE_INPUT = register_prompt("notify_rule", RULE_HELP, _consume_rule)


# ---- callbacks ----------------------------------------------------------
async def handle(click: Click) -> None:
    event, data = click.event, click.data
    # Cancelling an armed prompt is the one action a member may take here.
    if data in ("st", "st_x"):
        if data == "st_x":
            # The prompt message (this button's own) goes away; the screen the
            # prompt was opened from is still above it, so nothing is re-shown.
            await event.answer("Отменено")
            await cancel_pending(event)
            return
        if click.role != "admin":
            await event.answer("Только владелец", alert=True)
            return
        else:
            clear_pending(click.sender_id)
        text, buttons = root_menu()
        await safe_edit(event, text, buttons=buttons)
        return
    if click.role != "admin":
        await event.answer("Только владелец", alert=True)
        return
    if data == "st_u":
        text, buttons = usernames_menu()
        await safe_edit(event, text, buttons=buttons)
        return
    if data == "st_u_add":
        await prompt_pending(event, TRACK_ADD)
        return
    if data == "st_u_rm":
        await prompt_pending(event, TRACK_RM)
        return
    if data == "st_k":
        text, buttons = keywords_root_menu()
        await safe_edit(event, text, buttons=buttons)
        return
    if data.startswith("st_k_"):
        rest = data[len("st_k_"):]
        code = rest[:1]
        if code not in KEYWORD_SCOPES:
            await event.answer()
            return
        if rest.endswith("_add"):
            await prompt_pending(event, KW_ADD, code)
            return
        if rest.endswith("_rm"):
            await prompt_pending(event, KW_RM, code)
            return
        text, buttons = await keyword_list_menu(code)
        await safe_edit(event, text, buttons=buttons)
        return
    if data in ("st_n_en", "st_n_gw", "st_n_wn", "st_n_md", "st_n_q"):
        notif = await ws.load_notification_settings()
        if data == "st_n_en":
            notif["enabled"] = not notif.get("enabled", True)
        elif data == "st_n_gw":
            notif["include_giveaways"] = not notif.get("include_giveaways", True)
        elif data == "st_n_wn":
            notif["include_wins"] = not notif.get("include_wins", True)
        elif data == "st_n_md":
            notif["moderation_mode"] = "moderated" if notif.get("moderation_mode") != "moderated" else "auto"
        else:
            quiet = dict(notif.get("quiet_hours") or {})
            quiet.setdefault("from", "23:00")
            quiet.setdefault("to", "08:00")
            quiet["enabled"] = not quiet.get("enabled")
            notif["quiet_hours"] = quiet
        await save_notifications(notif)
        text, buttons = await notifications_menu()
        await safe_edit(event, text, buttons=buttons)
        await event.answer("Сохранено")
        return
    if data == "st_n_qt":
        await prompt_pending(event, QUIET)
        return
    if data == "st_n_cd":
        await prompt_pending(event, COOLDOWN)
        return
    if data == "st_d_en":
        cfg = await ws.load_digest_settings()
        cfg["enabled"] = not cfg.get("enabled", True)
        await save_digest(cfg)
        text, buttons = await notifications_menu()
        await safe_edit(event, text, buttons=buttons)
        await event.answer("Сохранено")
        return
    if data == "st_d_tm":
        await prompt_pending(event, DIGEST_TIME)
        return
    if data == "st_n_ac":
        from ...autoclean import SETTINGS_KEY, label, next_choice, owner_hours

        hours = next_choice(owner_hours(await get_setting(SETTINGS_KEY, None)))
        await set_setting(SETTINGS_KEY, {"hours": hours})
        text, buttons = await notifications_menu()
        await safe_edit(event, text, buttons=buttons)
        await event.answer(f"Мелкие уведомления: {label(hours)}")
        return
    if data == "st_n":
        text, buttons = await notifications_menu()
        await safe_edit(event, text, buttons=buttons)
        return
    if data == "st_r" or data.startswith("st_r_"):
        page = int(data[5:]) if data[5:].isdigit() else 1
        text, buttons = fields_menu(await runtime_values(), page)
        await safe_edit(event, text, buttons=buttons)
        return
    if data.startswith("st_f_"):
        index = int(data[5:]) if data[5:].isdigit() else -1
        if not 0 <= index < len(RUNTIME_FIELDS):
            await event.answer("Настройка не найдена", alert=True)
            return
        text, buttons = field_menu(index, await runtime_values())
        await safe_edit(event, text, buttons=buttons)
        return
    if data.startswith("st_i_"):
        index = int(data[5:]) if data[5:].isdigit() else -1
        if not 0 <= index < len(RUNTIME_FIELDS):
            await event.answer("Настройка не найдена", alert=True)
            return
        await prompt_pending(event, FIELD_INPUT, scope=str(index))
        return
    if data.startswith("st_v_"):
        await _set_preset(click, data)
        return
    if data == "st_l":
        text, buttons = await rules_menu()
        await safe_edit(event, text, buttons=buttons)
        return
    if data == "st_la":
        await prompt_pending(event, RULE_INPUT)
        return
    if data.startswith("st_ld_"):
        await _delete_rule(click, data)
        return
    await event.answer()


async def _delete_rule(click: Click, data: str) -> None:
    index = int(data[6:]) if data[6:].isdigit() else -1
    notif = await ws.load_notification_settings()
    rules = [r for r in (notif.get("rules") or []) if isinstance(r, dict)]
    if not 0 <= index < len(rules):
        await click.event.answer("Список правил изменился — откройте заново", alert=True)
        return
    removed = rules.pop(index)
    notif["rules"] = rules
    await save_notifications(notif)
    await click.event.answer(f"Удалено: {describe_rule(removed)}"[:180])
    text, buttons = await rules_menu()
    await safe_edit(click.event, text, buttons=buttons)


async def _set_preset(click: Click, data: str) -> None:
    """`st_v_<поле>_<пресет>` — оба числа, потому что ключи длинные."""
    parts = data[5:].split("_")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await click.event.answer("Некорректная команда", alert=True)
        return
    index, choice = int(parts[0]), int(parts[1])
    if not 0 <= index < len(RUNTIME_FIELDS):
        await click.event.answer("Настройка не найдена", alert=True)
        return
    field = RUNTIME_FIELDS[index]
    presets = list(field.presets) or list(field.choices)
    if not 0 <= choice < len(presets):
        await click.event.answer("Список изменился — откройте заново", alert=True)
        return
    ok, note = await apply_field(index, presets[choice])
    await click.event.answer(note[:180], alert=not ok)
    text, buttons = field_menu(index, await runtime_values())
    await safe_edit(click.event, text, buttons=buttons)


def register(router: CallbackRouter) -> None:
    # Not `admin=True`: `st_x` has to reach a member so they can cancel a prompt.
    router.group("st", sep="_")(handle)
