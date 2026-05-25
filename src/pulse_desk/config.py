from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env", override=True, encoding="utf-8-sig")


def _bool_from_env_default(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    base_dir: Path = BASE_DIR
    static_dir: Path = BASE_DIR / "static"
    data_dir: Path = BASE_DIR / "data"
    session_dir: Path = BASE_DIR / "sessions"
    log_dir: Path = BASE_DIR / "logs"
    backup_dir: Path = BASE_DIR / "backups"

    telegram_api_id: Optional[int] = Field(default=None, alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    admin_id: Optional[int] = Field(default=None, alias="ADMIN_ID")

    telegram_sessions: str = Field(default="", alias="TELEGRAM_SESSIONS")
    telegram_session_dir: Optional[Path] = Field(default=None, alias="PULSE_SESSION_DIR")
    pulse_db_path: Optional[Path] = Field(default=None, alias="PULSE_DB_PATH")
    pulse_log_dir: Optional[Path] = Field(default=None, alias="PULSE_LOG_DIR")

    usernames: str = Field(
        default="alga_kazakhst2n,w3v8f0rm,Fjfjfjfjds,Timofey02513,MuverGT,xdfusybau,davifd23,fsdfsdfdsg34",
        alias="USERNAMES",
    )
    extra_usernames: str = Field(default="", alias="EXTRA_USERNAMES")

    web_auth_token: str = Field(default="", alias="WEB_AUTH_TOKEN")
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")
    viewer_token: str = Field(default="", alias="VIEWER_TOKEN")
    public_share_mode: bool = Field(default=False, alias="PUBLIC_SHARE_MODE")
    allow_query_token: bool = Field(default=False, alias="ALLOW_QUERY_TOKEN")

    auto_join_giveaways: bool = Field(default=False, alias="AUTO_JOIN_GIVEAWAYS")
    dry_run_giveaways: bool = Field(default=True, alias="DRY_RUN_GIVEAWAYS")
    giveaway_action_account: str = Field(default="alga_kazakhst2n", alias="GIVEAWAY_ACTION_ACCOUNT")
    giveaway_review_mode: str = Field(default="manual", alias="GIVEAWAY_REVIEW_MODE")
    giveaway_analyze_recent_messages: int = Field(default=50, alias="GIVEAWAY_ANALYZE_RECENT_MESSAGES")
    giveaway_inactive_channel_days: int = Field(default=14, alias="GIVEAWAY_INACTIVE_CHANNEL_DAYS")
    giveaway_min_action_delay_seconds: int = Field(default=45, alias="GIVEAWAY_MIN_ACTION_DELAY_SECONDS")

    scan_interval_seconds: int = Field(default=900, alias="SCAN_INTERVAL_SECONDS")
    scan_account_concurrency: int = Field(default=3, alias="SCAN_ACCOUNT_CONCURRENCY")
    scan_history_limit: int = Field(default=0, alias="SCAN_HISTORY_LIMIT")
    edit_scan_recent_messages: int = Field(default=20, alias="EDIT_SCAN_RECENT_MESSAGES")
    startup_scan_delay_seconds: int = Field(default=8, alias="STARTUP_SCAN_DELAY_SECONDS")
    startup_scan_wait_seconds: int = Field(default=30, alias="STARTUP_SCAN_WAIT_SECONDS")
    market_poll_seconds: int = Field(default=300, alias="MARKET_POLL_SECONDS")
    market_alert_change_pct: float = Field(default=5.0, alias="MARKET_ALERT_CHANGE_PCT")
    market_retention_days: int = Field(default=7, alias="MARKET_RETENTION_DAYS")
    backup_retention: int = Field(default=10, alias="BACKUP_RETENTION")
    pending_auth_ttl_seconds: int = Field(default=600, alias="PENDING_AUTH_TTL_SECONDS")

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    telethon_log_level: str = Field(default="WARNING", alias="TELETHON_LOG_LEVEL")

    win_keywords: str = Field(
        default="победитель,выиграл,выиграла,вы выиграли,поздравляем,забрать приз,winner,win,congratulations,переможець,виграв",
        alias="WIN_KEYWORDS",
    )
    giveaway_keywords: str = Field(
        default="фаст,конкурс,розыгрыш,условия,условие,итоги",
        alias="GIVEAWAY_KEYWORDS",
    )
    join_button_keywords: str = Field(
        default="участвовать,participate,join,вступить,зарегистрироваться,register",
        alias="JOIN_BUTTON_KEYWORDS",
    )

    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")

    @field_validator("telegram_api_id", "admin_id", mode="before")
    @classmethod
    def _empty_int_to_none(cls, value):
        if value == "":
            return None
        return value

    @property
    def api_hash(self) -> str:
        return self.telegram_api_hash

    @property
    def api_id(self) -> Optional[int]:
        return self.telegram_api_id

    @property
    def bot_token(self) -> str:
        return self.telegram_bot_token

    @property
    def effective_admin_token(self) -> str:
        return self.admin_token.strip() or self.web_auth_token.strip()

    @property
    def effective_log_dir(self) -> Path:
        return self.pulse_log_dir or self.log_dir

    @property
    def effective_session_dir(self) -> Path:
        return self.telegram_session_dir or self.session_dir

    @property
    def db_path(self) -> Path:
        if self.pulse_db_path:
            return self.pulse_db_path
        legacy = self.base_dir / "pulse_desk.db"
        if legacy.exists():
            return legacy
        return self.data_dir / "pulse_desk.db"

    @property
    def log_file(self) -> Path:
        return self.effective_log_dir / "app.log"

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(exist_ok=True)
        self.effective_session_dir.mkdir(exist_ok=True)
        self.effective_log_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(exist_ok=True)
        self.db_path.parent.mkdir(exist_ok=True)

    def csv(self, name: str, default: str = "") -> list[str]:
        raw = getattr(self, name, default) or default
        return [item.strip() for item in str(raw).split(",") if item.strip()]

    def discover_sessions(self) -> list[str]:
        configured = [item.replace(".session", "").strip() for item in self.telegram_sessions.split(",") if item.strip()]
        if configured:
            return list(dict.fromkeys(configured))
        found: list[str] = []
        for directory in (self.effective_session_dir, self.base_dir):
            if not directory.exists():
                continue
            for file in directory.glob("*.session"):
                name = file.stem
                if name and not name.startswith(("pulse_bot", "pulse_desk_web")):
                    found.append(name)
        return list(dict.fromkeys(found))

    def session_path(self, session_name: str) -> Path:
        clean = session_name.replace(".session", "")
        modern = self.effective_session_dir / clean
        legacy = self.base_dir / clean
        if (modern.with_suffix(".session")).exists():
            return modern
        if (legacy.with_suffix(".session")).exists():
            return legacy
        return modern


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_dirs()
    return settings
