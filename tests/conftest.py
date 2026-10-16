"""Shared fixtures.

Test data is synthetic: the codice fiscale below is the textbook example
(Mario Rossi), never a real person's.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from anonmyzation_pipeline.config import Settings
from anonmyzation_pipeline.logging_setup import setup_logging

FAKE_CF = "RSSMRA85M01H501U"


class _ExplicitSettings(Settings):
    """Settings whose only source is the values passed to the constructor."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def make_settings(**overrides: Any) -> Settings:
    """Settings built only from explicit values.

    Neither ``.env`` nor the developer's environment can change the outcome of
    a test (a local ``.env`` must not reach the unit tests).
    """
    # Model weights are never loaded in unit tests: fakes stand in for OCR and NER.
    values: dict[str, Any] = {
        "environment": "local",
        "log_level": "DEBUG",
        "preload_models": False,
    }
    return _ExplicitSettings(**(values | overrides))


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def log_stream(settings: Settings) -> Iterator[io.StringIO]:
    """Everything the service writes to stdout, after the production
    handler (format + redaction filter) has processed it."""
    stream = io.StringIO()
    setup_logging(settings, stream=stream)
    yield stream
