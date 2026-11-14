"""Application settings loaded from environment variables (and an optional ``.env`` file).

Every tunable value in PitWall goes through :class:`Settings`, so nothing is hard-coded and
no secret ever needs to live in git. Variables use the ``PITWALL_`` prefix, for example
``PITWALL_OPENF1_REQUESTS_PER_MINUTE=20``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["json", "console"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Typed, validated configuration for every PitWall component."""

    model_config = SettingsConfigDict(
        env_prefix="PITWALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ general
    env: Literal["local", "ci", "prod"] = "local"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"
    data_dir: Path = Field(
        default=Path("data"),
        description="Root folder for local data (raw HTTP cache, DuckDB files).",
    )

    # ------------------------------------------------------------------ HTTP
    user_agent: str = Field(
        default="PitWall/0.1 (unofficial F1 analytics project)",
        description=(
            "Sent with every request so API operators can identify the client. "
            "Override it to add your repo URL or a contact address."
        ),
    )
    http_timeout_s: float = Field(default=30.0, gt=0)
    http_max_retries: int = Field(default=5, ge=0, le=10)
    http_backoff_base_s: float = Field(default=1.0, gt=0)
    http_backoff_max_s: float = Field(default=60.0, gt=0)

    # ------------------------------------------------------------------ OpenF1
    openf1_base_url: HttpUrl = HttpUrl("https://api.openf1.org/v1")
    openf1_requests_per_minute: int = Field(
        default=30,
        gt=0,
        description="Client-side request budget. Keep at or below the free-tier limit.",
    )
    openf1_requests_per_second: int = Field(
        default=2,
        gt=0,
        description="Burst limit. 3/s still drew an HTTP 429 in testing, so the default is 2.",
    )
    openf1_cache_enabled: bool = True

    # ------------------------------------------------------------------ object storage
    s3_endpoint_url: str = "http://localhost:9000"
    s3_bucket: str = "pitwall-bronze"
    s3_access_key_id: str = "pitwall"
    s3_secret_access_key: SecretStr = SecretStr("change-me-locally")

    @property
    def http_cache_dir(self) -> Path:
        """Folder where raw API responses are cached on disk."""
        return self.data_dir / "http_cache"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (read once, then cached)."""
    return Settings()
