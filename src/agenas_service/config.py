"""Service configuration.

Every setting is read from an environment variable with the ``TEMPLATE_``
prefix (``TEMPLATE_DATABASE_URL``, ``TEMPLATE_LOG_LEVEL`` ...). Every field
has a safe default: a pod must never crash on startup because a variable is
missing from its ConfigMap.

When you add a setting, add it in three places: here, ``.env.example`` and
``docker-compose.yml`` (``${VAR:-default}`` syntax).
"""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "prod"]


class Settings(BaseSettings):
    """Typed service settings."""

    model_config = SettingsConfigDict(
        env_prefix="TEMPLATE_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "agenas-service"
    # Defaults to "prod" on purpose: a forgotten variable must fall back to
    # the most privacy-conservative behaviour, never the most verbose one.
    environment: Environment = "prod"

    log_level: str = "INFO"
    # Allows user-entered text in logs (staging debugging only). Forced off
    # in prod by ``_enforce_prod_privacy``.
    extended_log_context: bool = False
    log_mask_prefix: int = 1
    log_mask_suffix: int = 1

    # Credential: deliberately no default. It comes from the CNPG secret in
    # the cluster and from .env locally. When empty the service still starts,
    # and /readyz reports the database as unavailable.
    database_url: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_connect_timeout: float = 5.0

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, value: str) -> str:
        """Accept the plain URI that CloudNativePG puts in ``<cluster>-app``.

        CNPG exposes ``postgresql://user:pwd@host:5432/db`` under the ``uri``
        key; SQLAlchemy needs the driver in the scheme.
        """
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

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
