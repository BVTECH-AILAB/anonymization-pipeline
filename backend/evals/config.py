"""Settings of the evaluation tools.

They read the same ``ANONPIP_*`` variables as the service, so one ``.env``
drives both and an evaluation measures the configuration that is deployed.
Only what the engine needs is read: no service setting is involved.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.services.anonymizer.config import PipelineConfig


class EvalSettings(BaseSettings):
    """Engine settings for evaluation runs (same names and defaults as the service)."""

    model_config = SettingsConfigDict(
        env_prefix="ANONPIP_",
        env_file=".env",
        extra="ignore",
    )

    ocr_server_url: str = ""
    ocr_dpi: int = Field(default=300, ge=100, le=600)
    ner_enabled: bool = True
    ner_url: str = ""
    ner_timeout_seconds: float = Field(default=30, gt=0, le=300)
    ner_retries: int = Field(default=2, ge=0, le=5)
    ner_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=30)
    ner_ca_bundle: str = ""
    ner_threshold: float = Field(default=0.5, ge=0, le=1)
    ner_person_threshold: float = Field(default=0.4, ge=0, le=1)
    redact_clinical_dates: bool = False
    redact_age: bool = False
    redact_organizations: bool = False
    redaction_style: Literal["black", "label"] = "black"
    verify_pixel_tolerance: float = Field(default=2e-4, ge=0, le=0.01)

    def pipeline_config(self) -> PipelineConfig:
        """The engine configuration: OCR, NER and redaction policy."""
        return PipelineConfig(
            ocr_server_url=self.ocr_server_url,
            ocr_dpi=self.ocr_dpi,
            ner_enabled=self.ner_enabled,
            ner_url=self.ner_url,
            ner_timeout_seconds=self.ner_timeout_seconds,
            ner_retries=self.ner_retries,
            ner_retry_backoff_seconds=self.ner_retry_backoff_seconds,
            ner_ca_bundle=self.ner_ca_bundle,
            ner_threshold=self.ner_threshold,
            ner_person_threshold=self.ner_person_threshold,
            redaction_policy={
                "DATE": self.redact_clinical_dates,
                "AGE": self.redact_age,
                "ORGANIZATION": self.redact_organizations,
            },
        )


@lru_cache(maxsize=1)
def get_settings() -> EvalSettings:
    """Return the process-wide settings, built once."""
    return EvalSettings()
