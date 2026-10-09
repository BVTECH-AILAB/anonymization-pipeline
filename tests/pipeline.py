"""Helpers shared by the pipeline tests: isolated configuration and fake VLMs."""

from typing import Any

from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from tests.conftest import make_settings


def pipeline_config(overrides: dict | None = None, **settings: Any) -> PipelineConfig:
    """Pipeline configuration from explicit settings only (no environment, no .env)."""
    return load_pipeline_config(make_settings(**settings), overrides)
