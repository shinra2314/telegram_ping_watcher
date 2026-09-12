"""Writing settings from the bot: persist, apply in-process, audit, announce.

A handler must never call ``set_setting`` on its own — a saved value that is not
also applied to ``watch_settings`` leaves the running scanner on the old rules
until restart, and one that is not announced leaves every other surface stale.
These four steps belong together, so each setting group gets exactly one
function and every screen goes through it.
"""
from __future__ import annotations

from database import set_setting

from .. import watch_settings as ws
from ..app_ctx import state
from ..common import record_app_event


async def save_tracking(usernames: list[str]) -> None:
    ws.apply_tracking_settings({"usernames": usernames})
    await set_setting("tracking", {"usernames": state.ping_usernames})
    await record_app_event("INFO", "settings", "Tracked usernames updated",
                           {"count": len(state.ping_usernames), "via": "bot"})


async def save_keywords(values: dict[str, list[str]]) -> None:
    await set_setting("keywords", values)
    ws.apply_keyword_settings(values)
    await record_app_event("INFO", "settings", "Keyword rules updated", {"via": "bot"})


async def save_notifications(settings_dict: dict) -> None:
    await set_setting("notifications", settings_dict)
    await record_app_event("INFO", "settings", "Notification rules updated", {"via": "bot"})


async def save_digest(cfg: dict) -> None:
    await set_setting("digest", cfg)
    await record_app_event("INFO", "settings", "Digest settings updated", {"via": "bot", **cfg})


async def save_runtime(values: dict) -> None:
    """Тонкие настройки скана и рынка.

    ``apply_runtime_settings`` не просто пишет модульные глобалы — он прогоняет
    значения через тот же санитайзер, что и веб, и возвращает уже вычищенное.
    Сохраняем именно его результат, иначе в базе окажется то, чего приложение
    не приняло.
    """
    cleaned = ws.apply_runtime_settings(values)
    await set_setting("runtime", cleaned)
    await record_app_event("INFO", "settings", "Runtime settings updated", {"via": "bot"})


async def save_roulette(cfg: dict) -> None:
    await set_setting("roulette", cfg)
    await record_app_event("INFO", "roulette", "Roulette reminder updated", {"via": "bot", **cfg})
