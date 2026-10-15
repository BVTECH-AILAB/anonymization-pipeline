"""Shared fixtures.

Test data is synthetic: the codice fiscale below is the textbook example
(Mario Rossi), never a real person's.
"""

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from anonymization_pipeline.config import Settings
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.pipeline_config import categories
from anonymization_pipeline.pipeline_config.resources import parse_yaml, read_yaml

FAKE_CF = "RSSMRA85M01H501Q"
# pipeline overrides of the tests: every category active, whatever the packaged defaults
PIPELINE_FILE = str(Path(__file__).parent / "fixtures" / "pipeline.yaml")
# packaged defaults with the models switched off
DEFAULTS_FILE = str(Path(__file__).parent / "fixtures" / "defaults.yaml")
# category registry of the tests: generic families and partial categories always active
CATEGORIES_FILE = Path(__file__).parent / "fixtures" / "categories.yaml"


def _read_yaml(name: str) -> dict[str, Any]:
    if name == "categories.yaml":
        return parse_yaml(
            CATEGORIES_FILE.read_text(encoding="utf-8"), str(CATEGORIES_FILE)
        )
    return read_yaml(name)


# before any registry() call: every module shares the registry read once per process
pytest.MonkeyPatch().setattr(categories, "read_yaml", _read_yaml)
categories.registry.cache_clear()


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
        {
            "environment": "local",
            "log_level": "DEBUG",
            "pipeline_config_file": PIPELINE_FILE,
            **overrides,
        }
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
