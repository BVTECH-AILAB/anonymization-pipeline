"""Shared fixtures.

Test data is synthetic: the codice fiscale below is the textbook example
(Mario Rossi), never a real person's.
"""

import io
from collections.abc import Iterator
from typing import Any

import pytest

from anonymization_pipeline.config import Settings
from anonymization_pipeline.logging_setup import setup_logging

FAKE_CF = "RSSMRA85M01H501U"


def make_settings(**overrides: Any) -> Settings:
    """Settings built only from explicit values.

    ``model_validate`` skips the settings sources, so neither ``.env`` nor
    the developer's environment can change the outcome of a test.
    """
    return Settings.model_validate(
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
