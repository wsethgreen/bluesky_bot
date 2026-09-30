"""Application settings loaded from environment / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
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
    bluesky_app_password: SecretStr = Field(
        default=SecretStr(""), description="Bluesky app password (never commit this)"
    )
    bluesky_pds_host: str = Field(default="https://bsky.social")

    # NHL APIs (see nhl-api.md)
    nhl_web_api_base_url: str = Field(default="https://api-web.nhle.com")
    nhl_stats_api_base_url: str = Field(default="https://api.nhle.com/stats/rest")

    # API keys
    rapid_api_key: SecretStr = Field(
        default=SecretStr(""), description="Rapidapi api key (never commit this)"
    )
    highlightly_api_key: SecretStr = Field(
        default=SecretStr(""), description="Highlightly API key, sent as x-rapidapi-key"
    )
    highlightly_base_url: str = Field(default="https://nhl.highlightly.net")

    # Shared HttpClient tuning (matches clients/http_client.py defaults)
    http_timeout: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    http_backoff_factor: float = Field(default=0.5, ge=0)

    # App
    log_level: str = Field(default="INFO")
    env: str = Field(default="dev")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so all services share one parsed Settings instance."""
    return Settings()
