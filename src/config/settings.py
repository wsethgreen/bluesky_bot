"""Application settings loaded from environment / .env file."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Single source of truth for config. Values come from env vars or .env."""

    model_config = SettingsConfigDict(
        env_file=(str(_PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Bluesky
    bluesky_handle: str = Field(
        default="", description="Bluesky handle, e.g. user.bsky.social"
    )

    # Brightcove
    brightcove_account_id: str = Field(
        default="6415718365001", description="Brightcove account ID"
    )
    brightcove_base_url: str = Field(description="The brightcove API base URL")
    brightcove_policy_key: SecretStr = Field(
        default=SecretStr(""), description="Brightcove playback policy key"
    )

    # Shared HttpClient tuning (matches clients/http_client.py defaults)
    http_timeout: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    http_backoff_factor: float = Field(default=0.5, ge=0)

    # NHL APIs (see nhl-api.md)
    nhl_web_api_base_url: str = Field(description="")
    nhl_stats_api_base_url: str = Field(description="")

    # NHL team focus
    nhl_team_abbreviation: str = Field(
        default="", description="Team to follow, e.g. CBJ"
    )

    # Postgres: split parts, URLs derived (never store URLs in .env).
    db_user: str = Field(default="bluesky")
    db_password: str = Field(description="Matches POSTGRES_PASSWORD in compose")
    db_name: str = Field(default="bluesky_bot")
    db_host: str = Field(
        default="db", description="'db' in compose, 'localhost' on the host"
    )
    db_port: int = Field(default=5432)

    @computed_field
    @property
    def db_url(self) -> str:
        """Runtime URL (inside containers db_host is the compose service)."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @computed_field
    @property
    def db_migration_url(self) -> str:
        """Host-side URL for alembic (via published 127.0.0.1:5432)."""
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@localhost:{self.db_port}/{self.db_name}"
        )

    # App
    log_level: str = Field(default="INFO")
    env: str = Field(default="dev")
    run_workers: bool = Field(
        default=True, description="Set false to run the API without background jobs"
    )
    app_timezone: str = Field(
        default="America/Toronto",
        description="IANA timezone used when 'today' needs a date, e.g. /matches/today",
    )

    @field_validator("app_timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Invalid IANA timezone: {v!r}") from exc
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so all services share one parsed Settings instance."""
    return Settings()
