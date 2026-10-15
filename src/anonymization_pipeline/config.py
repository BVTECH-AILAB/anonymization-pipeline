"""Service configuration.

Every setting is read from an environment variable with the ``ANONPIP_``
prefix (``ANONPIP_LOG_LEVEL``, ``ANONPIP_PIPELINE_CONFIG_FILE`` ...). Every field
has a safe default: a pod must never crash on startup because a variable is
missing from its ConfigMap.

When you add a setting, add it in three places: here, ``.env.example`` and
``docker-compose.yml`` (``${VAR:-default}`` syntax).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "prod"]


class Settings(BaseSettings):
    """Typed service settings."""

    model_config = SettingsConfigDict(
        env_prefix="ANONPIP_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "anonymization-pipeline"
    # Defaults to "prod" on purpose: a forgotten variable must fall back to
    # the most privacy-conservative behaviour, never the most verbose one.
    environment: Environment = "prod"

    log_level: str = "INFO"
    # Allows user-entered text in logs (staging debugging only). Forced off
    # in prod by ``_enforce_prod_privacy``.
    extended_log_context: bool = False
    log_mask_prefix: int = 1
    log_mask_suffix: int = 1

    # YAML with the pipeline keys to change (resources/default.yaml); empty: packaged defaults.
    pipeline_config_file: str = ""

    # Uploads and jobs (in memory: a restart loses them, one replica sees only its own jobs)
    max_upload_mb: float = Field(default=30, gt=0, le=200)
    max_queued_jobs: int = Field(default=20, ge=1, le=500)  # waiting or running
    max_kept_jobs: int = Field(
        default=50, ge=1, le=1000
    )  # finished: the oldest go first
    job_ttl_s: int = Field(
        default=1800, ge=60, le=86400
    )  # a finished job is then deleted

    # Key of the pseudonym map when a request gives none (credential: no default; empty = a
    # pseudonym request without its own key is refused).
    vault_key: str = ""

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return level

    @model_validator(mode="after")
    def _enforce_prod_privacy(self) -> "Settings":
        """Never allow verbose or user-text logging in production."""
        if self.environment == "prod":
            self.extended_log_context = False
            if self.log_level == "DEBUG":
                self.log_level = "INFO"
        return self

    @property
    def log_user_text(self) -> bool:
        """True when user-entered text may appear verbatim in logs."""
        return self.extended_log_context and self.environment != "prod"

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI docs are served everywhere except prod."""
        return self.environment != "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once."""
    return Settings()
