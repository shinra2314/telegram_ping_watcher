"""Ключи доступа: список и панель управления одним ключом.

Панель владеет всей жизнью ключа после создания — метка, роль, срок, гранты,
держатели, ссылка, отзыв/возврат и удаление с подтверждением. Отозванные ключи
остаются в списке именно потому, что вернуть их можно только отсюда.

Аккаунты в грантах адресуются **индексом** в ``grantable_accounts()``: имена
кириллицей не влезают в 64 байта callback-данных, поэтому отрисовка и клик
обязаны собрать один и тот же список.
"""
from __future__ import annotations

from database import (
    delete_bot_key, get_bot_key, list_bot_key_members, list_bot_keys, set_bot_key_expiry,
    set_bot_key_label, set_bot_key_max_uses, set_bot_key_permissions, set_bot_key_revoked,
    set_bot_key_role, set_bot_member_permissions,
)

from ...app_ctx import state
from ...bot_permissions import (
    ALL_FEATURES, ALL_NOTIFY, dump_permissions, format_delay, parse_delay_input,
    parse_permissions,
    permission_delay_minutes, set_accounts, set_delay, set_features, set_notify,
    toggle_account, toggle_feature, toggle_notify,
)
from ...common import record_app_event
from ..cards import (
    key_accounts_card, key_delay_card, key_delete_card, key_expiry_card, key_features_card,
    key_members_card, key_notify_card, key_panel_card, key_state_badge, keys_card,
)
from ..keyboards import (
    KEY_PANEL_ACCOUNTS_PAGE, key_accounts_keyboard, key_delay_keyboard, key_delete_keyboard,
    key_expiry_keyboard, key_features_keyboard, key_members_keyboard, key_notify_keyboard,
    key_panel_keyboard, keys_keyboard,
)
from ..pending import InputRejected, prompt_pending, register_prompt
from ..reply import safe_edit
from ..router import CallbackRouter, Click
from ..views import DIV, expiry_from_days, format_expiry, key_expired, parse_expiry_days

# `key:<action>:<id>` callbacks that act on the key itself rather than on its
# grants — they are addressed by name, so they must never be read as a key id.
LIFECYCLE_ACTIONS = ("rm", "on", "del", "delgo")


async def render_list():
    # Revoked keys stay listed — the panel is where they get restored or
    # deleted for good.
    keys = await list_bot_keys(include_revoked=True)
    items = [
        (int(k["id"]), f"{key_state_badge(k)} #{k['id']} {k.get('label') or 'без метки'}"[:40])
        for k in keys
    ]
    return keys_card(keys), keys_keyboard(items)


def grantable_accounts() -> list[str]:
    """Tracked usernames, '@' stripped — the whitelist the grant addresses.

    Rebuilt identically on render and on click, because account toggles travel
    as an index into this list (callback data is capped at 64 B).
    """
    return [name.lstrip("@") for name in (state.ping_usernames or [])]


async def render_panel(key_id: int):
    key = await get_bot_key(key_id)
    if not key:
        return None
    grants = parse_permissions(key.get("permissions"))
    accounts = grantable_accounts()
    return key_panel_card(key, grants, accounts), key_panel_keyboard(key, grants, len(accounts))


async def render_panel_into(event, key_id: int) -> None:
    """Re-render the panel in place after an edit; the key may be gone."""
    screen = await render_panel(key_id)
    if screen is None:
        text, kb = await render_list()
        await safe_edit(event, text, buttons=kb)
        return
    text, kb = screen
    await safe_edit(event, text, buttons=kb)


def link_text(key: dict) -> str:
    """Secret + invite link for one key, ready to forward."""
    secret = key.get("secret") or ""
    username = state.bot_username
    link = f"https://t.me/{username}?start={secret}" if username else ""
    body = f"🔑 **Ключ #{key.get('id')}** · {key.get('label') or '—'}\n{DIV}\n🔐 `{secret}`"
    body += f"\n🔗 {link}" if link else "\n__Передайте ключ вручную:__ `/redeem <ключ>`"
    if key.get("revoked"):
        body += "\n\n🚫 __Ключ отозван — ссылка не сработает, пока его не вернуть.__"
    elif key_expired(key.get("expires_at")):
        body += "\n\n⌛ __Срок ключа истёк — продлите его в панели.__"
    return body


async def save_permissions(key_id: int, grants: dict) -> None:
    await set_bot_key_permissions(key_id, dump_permissions(grants))
    synced = await sync_holders_delay(key_id, permission_delay_minutes(grants))
    await record_app_event("INFO", "bot", "Bot key grants updated", {"id": key_id, "via": "bot", "holders_synced": synced})


async def sync_holders_delay(key_id: int, minutes: int) -> int:
    """Carry the key's send delay onto everyone who already holds it.

    Holders keep a snapshot of the grants taken at redeem time, so that editing
    the key never strips an onboarded guest of their menu. The delay is not a
    menu, and the snapshot made it deaf to the panel: a key set to «мгновенно»
    still held its holder's copies back by the minute copied in June. Only the
    delay travels; sections, notification types and accounts stay the snapshot.
    """
    synced = 0
    for member in await list_bot_key_members(key_id):
        held = parse_permissions(member.get("permissions"))
        if permission_delay_minutes(held) == minutes:
            continue
        await set_bot_member_permissions(int(member["tg_id"]), dump_permissions(set_delay(held, minutes)))
        synced += 1
    return synced


# ---- typed input --------------------------------------------------------
async def _load_key(event, pending: dict):
    """The key the prompt was armed for — it may have been deleted since."""
    try:
        key_id = int(pending.get("scope") or 0)
    except (TypeError, ValueError):
        key_id = 0
    key = await get_bot_key(key_id) if key_id else None
    if not key:
        await event.respond("❌ Ключ не найден — возможно, он был удалён.")
        return 0, None
    return key_id, key


async def _consume_delay(event, pending: dict, raw: str) -> None:
    key_id, key = await _load_key(event, pending)
    if not key:
        return
    minutes = parse_delay_input(raw)
    if minutes is None:
        raise InputRejected("❌ Нужно целое число минут от 0 до 1440.")
    grants = set_delay(parse_permissions(key.get("permissions")), minutes)
    await save_permissions(key_id, grants)
    await event.respond(
        f"✅ Задержка: {format_delay(minutes)}\n\n{key_delay_card(grants)}",
        buttons=key_delay_keyboard(key_id, grants),
    )


async def _consume_label(event, pending: dict, raw: str) -> None:
    key_id, key = await _load_key(event, pending)
    if not key:
        return
    label = raw.strip()[:40]
    if not label:
        raise InputRejected("❌ Метка не может быть пустой.")
    await set_bot_key_label(key_id, label)
    await record_app_event("INFO", "bot", "Bot key label changed",
                           {"id": key_id, "label": label, "via": "bot"})
    screen = await render_panel(key_id)
    if screen:
        text, kb = screen
        await event.respond(f"✅ Метка: **{label}**\n\n{text}", buttons=kb)


async def _consume_expiry(event, pending: dict, raw: str) -> None:
    key_id, key = await _load_key(event, pending)
    if not key:
        return
    days = parse_expiry_days(raw)
    if days is None:
        raise InputRejected("❌ Нужно целое число дней от 0 до 365 (`0` — бессрочно).")
    expires_at = expiry_from_days(days)
    await set_bot_key_expiry(key_id, expires_at)
    await record_app_event("INFO", "bot", "Bot key expiry changed",
                           {"id": key_id, "expires_at": expires_at, "via": "bot"})
    key = await get_bot_key(key_id) or key
    await event.respond(
        f"✅ Срок: {format_expiry(expires_at)}\n\n{key_expiry_card(key)}",
        buttons=key_expiry_keyboard(key_id, key),
    )


LABEL_INPUT = register_prompt(
    "key_label", "Пришлите метку ключа — как вы будете его узнавать (до 40 символов).",
    _consume_label)
EXPIRY_INPUT = register_prompt(
    "key_expiry", "Пришлите срок в днях (0–365). `0` — бессрочно.", _consume_expiry)
DELAY_INPUT = register_prompt(
    "key_delay", "Пришлите задержку в минутах (0–1440). `0` — отправлять сразу.", _consume_delay)


async def _handle_panel(click: Click) -> None:
    """`key:<id>` root, `key:<section>:<id>[:<arg>]` for the rest."""
    event, seg = click.event, click.seg
    try:
        key_id = int(seg[1]) if len(seg) == 2 else int(seg[2])
    except (ValueError, IndexError):
        await event.answer("Некорректная команда", alert=True)
        return
    key = await get_bot_key(key_id)
    if not key:
        await event.answer("Ключ не найден", alert=True)
        return
    grants = parse_permissions(key.get("permissions"))
    section = seg[1] if len(seg) > 2 else ""
    arg = click.arg(3)
    accounts = grantable_accounts()

    if section == "link":
        await event.respond(link_text(key), link_preview=False)
        await event.answer()
        return

    if section == "name":
        await prompt_pending(event, LABEL_INPUT, scope=str(key_id))
        return

    if section == "once":
        once = int(key.get("max_uses") or 0) != 1
        await set_bot_key_max_uses(key_id, 1 if once else 0)
        await record_app_event("INFO", "bot", "Bot key max uses changed",
                               {"id": key_id, "max_uses": 1 if once else 0, "via": "bot"})
        await event.answer("🎟 Одноразовый: закроется после первого входа" if once else "Без лимита входов")
        await render_panel_into(event, key_id)
        return

    if section == "role":
        new_role = "viewer" if (key.get("role") or "viewer") == "premium" else "premium"
        await set_bot_key_role(key_id, new_role)
        await record_app_event("INFO", "bot", "Bot key role changed",
                               {"id": key_id, "role": new_role, "via": "bot"})
        await event.answer("⚡ Премиум" if new_role == "premium" else "👁 Просмотр")
        await render_panel_into(event, key_id)
        return

    if section == "m":
        members = await list_bot_key_members(key_id)
        items = [
            (int(m["tg_id"]),
             f"{'🚫' if m.get('blocked') else '🟢'} "
             f"{m.get('name') or m.get('tg_username') or m['tg_id']}")
            for m in members
        ]
        await safe_edit(event, key_members_card(key, members),
                        buttons=key_members_keyboard(key_id, items))
        return

    if section == "e":
        if arg == "_x":
            await prompt_pending(event, EXPIRY_INPUT, scope=str(key_id))
            return
        if arg:
            days = 0 if arg == "_off" else (int(arg) if arg.isdigit() else 0)
            expires_at = expiry_from_days(days)
            await set_bot_key_expiry(key_id, expires_at)
            await record_app_event("INFO", "bot", "Bot key expiry changed",
                                   {"id": key_id, "expires_at": expires_at, "via": "bot"})
            key = await get_bot_key(key_id) or key
            await event.answer(f"Срок: {format_expiry(expires_at)}")
        await safe_edit(event, key_expiry_card(key), buttons=key_expiry_keyboard(key_id, key))
        return

    if section == "f" and arg:
        if arg == "_all":
            grants = set_features(grants, ALL_FEATURES)
        elif arg == "_none":
            grants = set_features(grants, [])
        else:
            grants = toggle_feature(grants, arg)
        await save_permissions(key_id, grants)
        await event.answer("Сохранено")
    elif section == "n" and arg:
        if arg == "_all":
            grants = set_notify(grants, ALL_NOTIFY)
        elif arg == "_none":
            grants = set_notify(grants, [])
        else:
            grants = toggle_notify(grants, arg)
        await save_permissions(key_id, grants)
        await event.answer("Сохранено")
    elif section == "a" and arg:
        if arg == "_all":
            grants = set_accounts(grants, [])
            await save_permissions(key_id, grants)
            await event.answer("Все аккаунты")
        elif arg.startswith("_p"):
            page = int(arg[2:]) if arg[2:].isdigit() else 0
            await safe_edit(event, key_accounts_card(grants, accounts),
                            buttons=key_accounts_keyboard(key_id, grants, accounts, page))
            return
        else:
            index = int(arg) if arg.isdigit() else -1
            if not 0 <= index < len(accounts):
                await event.answer("Список аккаунтов изменился — откройте заново", alert=True)
                return
            name = accounts[index]
            # An empty whitelist means "all", so materialise it before removing
            # the first entry.
            base = grants.get("accounts") or list(accounts)
            updated = toggle_account(set_accounts(grants, base), name)
            if not updated.get("accounts"):
                await event.answer("Нельзя снять последний аккаунт", alert=True)
                return
            if len(updated["accounts"]) == len(accounts):
                updated = set_accounts(updated, [])
            grants = updated
            await save_permissions(key_id, grants)
            await event.answer(f"@{name}")
        page = (int(arg) // KEY_PANEL_ACCOUNTS_PAGE) if arg.isdigit() else 0
        await safe_edit(event, key_accounts_card(grants, accounts),
                        buttons=key_accounts_keyboard(key_id, grants, accounts, page))
        return
    elif section == "d" and arg:
        if arg == "_x":
            await prompt_pending(event, DELAY_INPUT, scope=str(key_id))
            return
        grants = set_delay(grants, int(arg) if arg.isdigit() else 0)
        await save_permissions(key_id, grants)
        await event.answer(f"Задержка: {format_delay(permission_delay_minutes(grants))}")

    if section == "f":
        await safe_edit(event, key_features_card(grants), buttons=key_features_keyboard(key_id, grants))
    elif section == "n":
        await safe_edit(event, key_notify_card(grants), buttons=key_notify_keyboard(key_id, grants))
    elif section == "a":
        await safe_edit(event, key_accounts_card(grants, accounts),
                        buttons=key_accounts_keyboard(key_id, grants, accounts))
    elif section == "d":
        await safe_edit(event, key_delay_card(grants), buttons=key_delay_keyboard(key_id, grants))
    else:
        await render_panel_into(event, key_id)


async def _handle_lifecycle(click: Click) -> None:
    event = click.event
    key_id = click.int_arg(2)
    if key_id is None:
        await event.answer("Некорректная команда", alert=True)
        return
    action = click.arg(1)
    if action in ("rm", "on"):
        revoked = action == "rm"
        await set_bot_key_revoked(key_id, revoked)
        await record_app_event("INFO", "bot",
                               "Bot key revoked" if revoked else "Bot key restored",
                               {"id": key_id, "via": "bot"})
        await event.answer("🚫 Ключ отозван — ссылка больше не откроет доступ"
                           if revoked else "♻️ Ключ снова работает")
        await render_panel_into(event, key_id)
        return
    if action == "del":
        # Deleting is the one key action with no undo, so confirm.
        key = await get_bot_key(key_id)
        if not key:
            await event.answer("Ключ уже удалён", alert=True)
            text, kb = await render_list()
            await safe_edit(event, text, buttons=kb)
            return
        await safe_edit(event, key_delete_card(key), buttons=key_delete_keyboard(key_id))
        return
    deleted = await delete_bot_key(key_id)
    if deleted is None:
        await event.answer("Ключ уже удалён", alert=True)
    else:
        # Deleting drops the invite only; people who already joined keep their
        # access and their grants.
        joined = int(deleted.get("member_count") or 0)
        note = "🗑 Ключ удалён"
        if joined:
            note += f"\nВошедшие ({joined}) сохраняют доступ — отключить можно в «Люди»."
        await event.answer(note, alert=bool(joined))
        await record_app_event("INFO", "bot", "Bot access key deleted",
                               {"id": key_id, "label": deleted.get("label"), "via": "bot"})
    text, kb = await render_list()
    await safe_edit(event, text, buttons=kb)


async def handle(click: Click) -> None:
    if len(click.seg) < 2:
        await click.event.answer("Неизвестная команда", alert=True)
        return
    if click.arg(1) in LIFECYCLE_ACTIONS:
        if len(click.seg) < 3:
            await click.event.answer("Неизвестная команда", alert=True)
            return
        await _handle_lifecycle(click)
        return
    await _handle_panel(click)


async def handle_menu(click: Click) -> None:
    text, kb = await render_list()
    await safe_edit(click.event, text, buttons=kb)


async def handle_legacy_revoke(click: Click) -> None:
    """`revokekey_<id>` — кнопка со старых сообщений."""
    try:
        key_id = int(click.tail())
    except ValueError:
        await click.event.answer("Некорректная команда", alert=True)
        return
    await set_bot_key_revoked(key_id, True)
    await click.event.answer("Ключ отозван")
    text, kb = await render_list()
    await safe_edit(click.event, text, buttons=kb)


def register(router: CallbackRouter) -> None:
    router.group("key", admin=True)(handle)
    router.exact("menu_keys", admin=True)(handle_menu)
    router.group("revokekey", sep="_", admin=True)(handle_legacy_revoke)
