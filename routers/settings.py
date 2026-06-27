"""Settings endpoints: usernames, keywords, runtime tunables, notifications, rules UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from database import get_setting, set_setting
from pulse_desk import watch_settings as ws
from pulse_desk.api_models import (
    KeywordsRequest,
    NotificationSettingsRequest,
    RulesUiRequest,
    RuntimeSettingsRequest,
    UsernamesRequest,
)
from pulse_desk.app_ctx import AUTO_JOIN_GIVEAWAYS, require_admin, state
from pulse_desk.common import record_app_event
from pulse_desk.live import publish_live_event

router = APIRouter()


@router.get("/api/settings/usernames", dependencies=[Depends(require_admin)])
async def get_usernames():
    tracking = await ws.load_tracking_settings()
    return {
        "usernames": state.ping_usernames,
        "saved_usernames": tracking["usernames"],
        "source": tracking.get("source", "runtime"),
        "win_keywords": state.win_keywords,
        "giveaway_keywords": state.giveaway_keywords,
        "auto_join_giveaways": AUTO_JOIN_GIVEAWAYS,
    }


@router.put("/api/settings/usernames", dependencies=[Depends(require_admin)])
async def update_usernames(data: UsernamesRequest):
    ws.apply_tracking_settings({"usernames": data.usernames})
    await set_setting("tracking", {"usernames": state.ping_usernames})
    await record_app_event("INFO", "settings", "Tracked usernames updated", {"count": len(state.ping_usernames)})
    await publish_live_event("settings-updated", {"scope": "tracking", "usernames": len(state.ping_usernames)})
    return {"status": "ok", "usernames": state.ping_usernames}


@router.get("/api/settings/keywords", dependencies=[Depends(require_admin)])
async def get_keywords():
    return await ws.load_keyword_settings()


@router.put("/api/settings/keywords", dependencies=[Depends(require_admin)])
async def update_keywords(data: KeywordsRequest):
    values = {
        "win_keywords": [item.strip() for item in data.win_keywords if item.strip()] or state.win_keywords,
        "giveaway_keywords": [item.strip().lower() for item in data.giveaway_keywords if item.strip()] or state.giveaway_keywords,
        "high_priority_keywords": [item.strip() for item in data.high_priority_keywords if item.strip()],
        "ignore_keywords": [item.strip() for item in data.ignore_keywords if item.strip()],
    }
    await set_setting("keywords", values)
    ws.apply_keyword_settings(values)
    await record_app_event("INFO", "settings", "Keyword rules updated", {"giveaway_keywords": values["giveaway_keywords"]})
    await publish_live_event("settings-updated", {"scope": "keywords"})
    return {"status": "ok", **values}


@router.get("/api/settings/runtime", dependencies=[Depends(require_admin)])
async def get_runtime_settings():
    saved = await get_setting("runtime", None)
    return {
        "status": "ok",
        "settings": ws.runtime_settings_payload(),
        "saved": ws.sanitize_runtime_settings(saved if isinstance(saved, dict) else None),
        "source": "saved" if isinstance(saved, dict) else "env",
    }


@router.put("/api/settings/runtime", dependencies=[Depends(require_admin)])
async def update_runtime_settings(data: RuntimeSettingsRequest):
    cleaned = ws.apply_runtime_settings(data.model_dump())
    await set_setting("runtime", cleaned)
    await record_app_event("INFO", "settings", "Runtime settings updated", cleaned)
    await publish_live_event("settings-updated", {"scope": "runtime"})
    return {"status": "ok", "settings": cleaned}


@router.get("/api/settings/notifications", dependencies=[Depends(require_admin)])
async def get_notification_settings():
    return await ws.load_notification_settings()


@router.put("/api/settings/notifications", dependencies=[Depends(require_admin)])
async def update_notification_settings(data: NotificationSettingsRequest):
    settings = data.model_dump()
    await set_setting("notifications", settings)
    await record_app_event("INFO", "settings", "Notification rules updated", {"rules": settings})
    await publish_live_event("settings-updated", {"scope": "notifications"})
    return {"status": "ok", "settings": settings}


@router.get("/api/settings/rules-ui", dependencies=[Depends(require_admin)])
async def get_rules_ui():
    saved = await get_setting("rules_ui", None)
    if saved is None:
        notifications = await ws.load_notification_settings()
        saved = {
            "enabled": notifications.get("enabled", True),
            "quiet_hours": notifications.get("quiet_hours", {"enabled": False, "from": "23:00", "to": "08:00"}),
            "rules": notifications.get("rules", []),
        }
    return saved


@router.put("/api/settings/rules-ui", dependencies=[Depends(require_admin)])
async def update_rules_ui(data: RulesUiRequest):
    payload = {
        "enabled": data.enabled,
        "quiet_hours": data.quiet_hours,
        "rules": data.rules[:50],
    }
    await set_setting("rules_ui", payload)
    notifications = await ws.load_notification_settings()
    notifications["enabled"] = data.enabled
    notifications["quiet_hours"] = data.quiet_hours
    notifications["rules"] = data.rules[:50]
    await set_setting("notifications", notifications)
    await record_app_event("INFO", "settings", "Visual notification rules updated", {"rules": len(data.rules)})
    return payload
