"""Shared fixtures.

Test data is synthetic: the codice fiscale below is the textbook example
(Mario Rossi), never a real person's.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from anonymization_pipeline.config import Settings
from anonymization_pipeline.logging_setup import setup_logging

FAKE_CF = "RSSMRA85M01H501U"

# main.py builds the app when imported (``main:app``) from Settings(): never from the developer's
# .env file (variables already exported, e.g. by ``just``, still apply).
Settings.model_config["env_file"] = None


class _ExplicitSettings(Settings):
    """Settings that read only the values passed to the constructor."""

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
    a test: pydantic-settings reads them even through ``model_validate``.
    """
    return _ExplicitSettings.model_validate(
        {"environment": "local", "log_level": "DEBUG", **overrides}
    )


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
