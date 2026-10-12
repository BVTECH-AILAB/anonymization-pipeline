"""Service configuration.

Every setting is read from an environment variable with the ``ANONPIP_``
prefix (``ANONPIP_LOG_LEVEL``, ``ANONPIP_VLM_URL`` ...). Every field
has a safe default: a pod must never crash on startup because a variable is
missing from its ConfigMap.

When you add a setting, add it in three places: here, ``.env.example`` and
``docker-compose.yml`` (``${VAR:-default}`` syntax), then list it in
``docs/DEPLOY_HANDOVER.md``.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "staging", "prod"]
VLMBackend = Literal["ollama", "openai"]
RedactionMode = Literal["pseudonym", "tag", "redaction"]
VerificationMode = Literal["off", "report", "block"]
ExtractionEngine = Literal["docling", "vlm"]


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

    # VLM backend: a local Ollama (native API) or an OpenAI-compatible server
    # (vLLM, llama.cpp). Documents never leave the deployment.
    vlm_backend: VLMBackend = "ollama"
    vlm_url: str = "http://ollama:11434"
    # Credential: only for OpenAI-compatible backends that require it.
    vlm_api_key: str = ""
    vlm_concurrency: int = Field(default=2, ge=1)
    # Off by default: at runtime the backend has no internet access, models are
    # downloaded beforehand (`just models`).
    vlm_pull_missing: bool = False

    default_mode: RedactionMode = "pseudonym"
    default_verification: VerificationMode = "block"
    default_extraction: ExtractionEngine = "docling"
    # Also a PDF with the layout of the original (censored) and its structure, for every request
    # that does not choose.
    default_layout_output: bool = False
    docling_models_dir: str = "/models/docling"
    # Optional YAML file with pipeline overrides (see resources/default.yaml).
    pipeline_config_file: str = ""
    # Optional YAML file with the VLM catalog and the model of each phase
    # (see resources/models.yaml, the default when empty).
    models_file: str = ""
    # Optional YAML file with the docling options to change (see resources/docling.yaml).
    docling_file: str = ""
    # Credential: key or passphrase of the pseudonym map. When empty, requests
    # in pseudonym mode must send their own key.
    vault_key: str = ""

    max_upload_mb: float = Field(default=50, gt=0)
    # Documents processed at the same time (the GPU is the bottleneck).
    max_concurrent_jobs: int = Field(default=1, ge=1)
    # Seconds a synchronous request waits for a free slot before a 503.
    job_wait_s: float = Field(default=30, ge=0)
    max_queued_jobs: int = Field(default=20, ge=1)
    max_jobs: int = Field(default=50, ge=1)
    job_ttl_s: float = Field(default=3600, gt=0)
    # A request may ask for the transcript IN CLEAR TEXT (debugging the web UI).
    # Forced off in prod by ``_enforce_prod_privacy``.
    allow_transcript: bool = False

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
            self.allow_transcript = False
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
