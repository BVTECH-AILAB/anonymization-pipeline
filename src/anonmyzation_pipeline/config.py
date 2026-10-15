"""Service configuration.

Every setting is read from an environment variable with the ``ANONPIP_``
prefix (``ANONPIP_LOG_LEVEL``, ``ANONPIP_MAX_UPLOAD_MB`` ...). Every field
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

    app_name: str = "anonmyzation-pipeline"
    # Defaults to "prod" on purpose: a forgotten variable must fall back to
    # the most privacy-conservative behaviour, never the most verbose one.
    environment: Environment = "prod"

    log_level: str = "INFO"
    # Allows user-entered text in logs (staging debugging only). Forced off
    # in prod by ``_enforce_prod_privacy``.
    extended_log_context: bool = False
    log_mask_prefix: int = 1
    log_mask_suffix: int = 1

    # --- Web UI (demo page served at "/"): off unless explicitly enabled ---
    ui_enabled: bool = False

    # --- Models ---
    # "auto" uses the GPU when one is available, otherwise the CPU.
    device: str = "auto"
    # Load OCR and NER at startup; /readyz reports 503 until they are loaded.
    preload_models: bool = True
    ocr_det_arch: str = "db_resnet50"
    ocr_reco_arch: str = "parseq"
    ocr_dpi: int = Field(default=300, ge=100, le=600)
    # Width of the side strips read again rotated by 90 degrees (vertical text).
    ocr_margin_pt: float = Field(default=60, ge=0, le=200)
    ner_enabled: bool = True
    ner_model: str = "urchade/gliner_multi_pii-v1"
    ner_threshold: float = Field(default=0.5, ge=0, le=1)
    ner_person_threshold: float = Field(default=0.4, ge=0, le=1)

    # --- Jobs and limits ---
    max_upload_mb: int = Field(default=30, ge=1, le=200)
    max_pages: int = Field(default=50, ge=1, le=500)
    # Documents are kept only for this long, then deleted from disk and memory.
    job_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    max_jobs: int = Field(default=20, ge=1, le=500)
    # Where uploaded and anonymized files live while a job exists; empty = a temp folder.
    work_dir: str = ""

    # --- Redaction policy ---
    # Quasi-identifiers are detected but kept by default.
    redact_clinical_dates: bool = False
    redact_age: bool = False
    redact_organizations: bool = False
    # black (solid box) | label (light box with the PII type)
    redaction_style: Literal["black", "label"] = "black"
    # Share of rendered pixels allowed to change outside the boxes.
    verify_pixel_tolerance: float = Field(default=2e-4, ge=0, le=0.01)

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
    def redaction_policy(self) -> dict[str, bool]:
        """Overrides of the taxonomy defaults for the quasi-identifiers."""
        return {
            "DATE": self.redact_clinical_dates,
            "AGE": self.redact_age,
            "ORGANIZATION": self.redact_organizations,
        }

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI docs are served everywhere except prod."""
        return self.environment != "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, built once."""
    return Settings()
