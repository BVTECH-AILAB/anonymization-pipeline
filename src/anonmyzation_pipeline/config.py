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
    # Load OCR and check the NER service at startup; /readyz reports 503 until both
    # are ready (retried every 30 s).
    preload_models: bool = True
    # PaddleOCR PP-OCRv5 (via RapidOCR): "mobile" or "server" text detector, on the GPU
    # (PyTorch) when ``device`` resolves to cuda; recognition: the Latin model (ONNX Runtime).
    # Stored in ocr_model_dir (empty: inside the package; in the image: /models/rapidocr)
    ocr_model_type: Literal["mobile", "server"] = "server"
    ocr_model_dir: str = ""
    # Pages read at the same time, one OCR engine each. More than 1 only with GPU memory
    # to spare (two server detectors at 300 dpi need about 6 GB): measure before raising it.
    ocr_workers: int = Field(default=1, ge=1, le=16)
    ocr_dpi: int = Field(default=300, ge=100, le=600)
    # Width of the side strips read again rotated by 90 degrees (vertical text).
    ocr_margin_pt: float = Field(default=60, ge=0, le=200)
    # NER: the AILAB NER service (POST /batch_ner). Empty URL: /readyz stays 503.
    ner_enabled: bool = True
    ner_url: str = ""
    ner_timeout_seconds: float = Field(default=30, gt=0, le=300)
    # Retries of a request that timed out or got 502/503/504; waits double: 1 s, 2 s ...
    ner_retries: int = Field(default=2, ge=0, le=5)
    ner_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)
    # PEM bundle of the CA that signed the NER certificate; empty: the system CAs.
    ner_ca_bundle: str = ""
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
