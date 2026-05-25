from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    phone: str = Field(min_length=5)
    session_name: str = Field(default="", max_length=64)
    force_sms: bool = False


class SignInRequest(BaseModel):
    phone: str
    code: str
    password: str = ""
    session_name: str = Field(default="", max_length=64)


class UsernamesRequest(BaseModel):
    usernames: list[str]


class NotificationSettingsRequest(BaseModel):
    enabled: bool = True
    usernames: list[str] = Field(default_factory=list)
    chats: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    cooldown_seconds: int = Field(default=120, ge=0, le=3600)
    include_giveaways: bool = True
    include_wins: bool = True
    quiet_hours: dict[str, Any] = Field(default_factory=dict)


class RulesUiRequest(BaseModel):
    enabled: bool = True
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    rules: list[dict[str, Any]] = Field(default_factory=list)


class PingMetaRequest(BaseModel):
    status: Optional[str] = None
    note: Optional[str] = None
    is_favorite: Optional[bool] = None
    giveaway_status: Optional[str] = None
    deadline_at: Optional[str] = None
    reminder_at: Optional[str] = None
    action_status: Optional[str] = None


class SavedFiltersRequest(BaseModel):
    filters: list[dict[str, Any]] = Field(default_factory=list)


class KeywordsRequest(BaseModel):
    win_keywords: list[str] = Field(default_factory=list)
    giveaway_keywords: list[str] = Field(default_factory=list)
    high_priority_keywords: list[str] = Field(default_factory=list)
    ignore_keywords: list[str] = Field(default_factory=list)


class RuntimeSettingsRequest(BaseModel):
    scan_interval_seconds: int = Field(default=900, ge=60, le=86400)
    scan_account_concurrency: int = Field(default=3, ge=1, le=8)
    scan_history_limit: int = Field(default=0, ge=0)
    edit_scan_recent_messages: int = Field(default=20, ge=0, le=500)
    startup_scan_delay_seconds: int = Field(default=8, ge=0, le=300)
    market_poll_seconds: int = Field(default=300, ge=60, le=86400)
    market_alert_change_pct: float = Field(default=5.0, ge=0.1, le=100.0)
    market_retention_days: int = Field(default=7, ge=1, le=365)
    giveaway_action_account: str = Field(default="", max_length=64)
    dry_run_giveaways: bool = True
    giveaway_review_mode: str = Field(default="manual", max_length=32)
    giveaway_analyze_recent_messages: int = Field(default=50, ge=5, le=300)
    giveaway_inactive_channel_days: int = Field(default=14, ge=1, le=365)
    giveaway_min_action_delay_seconds: int = Field(default=45, ge=0, le=3600)


class ActionResponse(BaseModel):
    status: str
    message: str = ""
