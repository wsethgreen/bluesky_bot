"""Application settings loaded from environment / .env file.

Config is grouped by concern — ``settings.nhl.*``, ``settings.nba.*``,
``settings.poller.*``, ``settings.poster.*``, ``settings.cbj.*``,
``settings.cavs.*`` — instead of one flat
class. Each group is its own ``BaseSettings`` that reads the same
``.env`` file under an ``env_prefix`` matching the flat keys already in
use (``NHL_TEAM_ABBREVIATION``, ``POLL_INTERVAL_MINUTES``, ...), so the
grouping is Python-side only and no environment variable is renamed.

Each Bluesky bot account is its own group too — ``settings.cbj.*`` and
``settings.cavs.*`` read ``CBJ_BLUESKY_*`` / ``CAVS_BLUESKY_*`` — so the
NHL and NBA bots post from separate accounts.

Shared concerns (Brightcove, HTTP tuning, DB, app) stay on the
top-level :class:`Settings`, as does ``final_grace_minutes`` (a poll
tuning knob but kept top-level alongside the other scheduler knobs).
"""

from functools import lru_cache

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _group_config(env_prefix: str) -> SettingsConfigDict:
    """model_config for a nested settings group.

    Groups are built by the parent via ``default_factory`` (the parent's
    env source does not populate nested models without
    ``env_nested_delimiter``), so each group names its env file and
    prefix explicitly rather than inheriting the parent's config.
    """
    return SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix=env_prefix,
        extra="ignore",
        case_sensitive=False,
    )


class NhlSettings(BaseSettings):
    """NHL APIs and the team to follow (env keys: ``NHL_*``)."""

    model_config = _group_config("nhl_")

    # NHL APIs (see nhl-api.md)
    web_api_base_url: str = Field(description="Base NHL web API URL")
    stats_api_base_url: str = Field(description="Base NHL stats API URL")

    # Team focus
    team_abbreviation: str = Field(description="Team to follow, e.g. CBJ")


class NbaSettings(BaseSettings):
    """NBA APIs and the team to follow (env keys: ``NBA_*``).

    ``content_base_url``/``team_id`` feed
    ``clients/nba_content_client.py`` (team-site highlight videos);
    ``site_api_base_url``/``team_abbreviation`` feed
    ``clients/nba_live_client.py`` — the scoreboard (game state) and
    the per-game summary (quarter scores + team leaders for
    end-of-quarter posts), which the ``nba_live_poller`` job runs on
    ``poll_interval_minutes``.
    """

    model_config = _group_config("nba_")

    content_base_url: str = Field(
        default="https://content-api-prod.nba.com",
        description="Base NBA content API URL",
    )
    team_id: int = Field(
        default=1610612739,
        description="NBA team ID to follow, e.g. 1610612739 (Cavaliers)",
    )
    site_api_base_url: str = Field(
        default="https://site.api.espn.com",
        description="ESPN site API base URL (NBA scoreboard / game state)",
    )
    team_abbreviation: str = Field(
        default="CLE",
        description="Team to follow, e.g. CLE",
    )
    poll_interval_minutes: int = Field(
        default=1,
        ge=1,
        description="NBA poller interval in minutes",
    )


class PollerSettings(BaseSettings):
    """Game poller cadence (env keys: ``POLL_*``)."""

    model_config = _group_config("poll_")

    interval_minutes: int = Field(default=1, ge=1)


class PosterSettings(BaseSettings):
    """Bluesky poster cadence (env keys: ``POST_*``)."""

    model_config = _group_config("post_")

    interval_minutes: int = Field(
        default=1, ge=1, description="Poster interval in minutes (1 = every minute)"
    )
    window_hours: int = Field(
        default=4,
        ge=1,
        description=(
            "Publish a game's posts only within this many hours of its "
            "start; --force bypasses the window"
        ),
    )


class BlueskyAccountSettings(BaseSettings):
    """Credentials for one team's Bluesky bot account.

    Not instantiated directly — concrete subclasses set ``env_prefix``
    so the same field names read ``CBJ_BLUESKY_*`` / ``CAVS_BLUESKY_*``
    (see :func:`_group_config` on why config is per-class).
    """

    bluesky_handle: str = Field(description="Bluesky handle, e.g. user.bsky.social")
    bluesky_app_password: SecretStr = Field(
        default=SecretStr(""), description="Bluesky app password"
    )


class CbjBlueskySettings(BlueskyAccountSettings):
    """Blue Jackets (NHL) bot account (env keys: ``CBJ_BLUESKY_*``)."""

    model_config = _group_config("cbj_")


class CavsBlueskySettings(BlueskyAccountSettings):
    """Cavaliers (NBA) bot account (env keys: ``CAVS_BLUESKY_*``)."""

    model_config = _group_config("cavs_")


class Settings(BaseSettings):
    """Single source of truth for config. Values come from env vars or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Nested groups (see module docstring)
    nhl: NhlSettings = Field(default_factory=NhlSettings)
    nba: NbaSettings = Field(default_factory=NbaSettings)
    poller: PollerSettings = Field(default_factory=PollerSettings)
    poster: PosterSettings = Field(default_factory=PosterSettings)

    # Bluesky (one account per league bot; see BlueskyAccountSettings)
    cbj: CbjBlueskySettings = Field(default_factory=CbjBlueskySettings)
    cavs: CavsBlueskySettings = Field(default_factory=CavsBlueskySettings)

    # Brightcove
    brightcove_account_id: str = Field(description="Brightcove account ID")
    brightcove_base_url: str = Field(description="The brightcove API base URL")
    brightcove_policy_key: SecretStr = Field(
        default=SecretStr(""), description="Brightcove playback policy key"
    )

    # Shared HttpClient tuning (matches clients/http_client.py defaults)
    http_timeout: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0)
    http_backoff_factor: float = Field(default=0.5, ge=0)

    # Database
    db_user: str
    db_password: SecretStr
    db_name: str
    db_host: str
    db_port: int = Field(default=5432)

    @computed_field
    @property
    def db_migration_url(self) -> str:
        # Honors db_host: localhost on the host, `db` inside compose.
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password.get_secret_value()}@{self.db_host}:{self.db_port}/{self.db_name}"

    # App
    log_level: str = Field(default="INFO")
    env: str = Field(
        default="local",
        description=(
            "Deployment env; 'local' (the safe default) makes the poster "
            "log what would be posted (text or caption + highlight URL) "
            "instead of calling Bluesky — prod sets 'prod' explicitly"
        ),
    )
    run_workers: bool = Field(
        default=True, description="Set false to run the API without background jobs"
    )
    app_timezone: str = Field(
        default="America/Toronto",
        description="IANA timezone used when 'today' needs a date, e.g. /matches/today",
    )

    # Scheduler (Compose+APScheduler singleton). Jobs self-gate on the DB.
    final_grace_minutes: int = Field(
        default=15,
        ge=0,
        description=(
            "How long after a FINAL/OFF game ends to keep polling for "
            "highlight clips the NHL attaches late — the hard cap on "
            "the data-driven clip wait (polling also stops as soon as "
            "every goal has its clip, so a larger value costs nothing "
            "once clips resolve). 0 disables the wait."
        ),
    )
    job_run_retention_days: int = Field(
        default=7,
        ge=1,
        description="job_runs rows older than this are pruned opportunistically",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so all services share one parsed Settings instance."""
    return Settings()
