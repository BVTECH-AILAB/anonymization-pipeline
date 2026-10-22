"""Shared fixtures.

Test data is synthetic: the only codice fiscale is the textbook example
(Mario Rossi, ``tests.documents.FAKE_CF``), never a real person's. The app
fixtures run the real routers and middleware on a job manager whose OCR server
and NER are fakes.
"""

import io
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from src.config import Settings
from src.main import create_app
from src.services.anonymization import JobManager, get_job_manager
from src.services.anonymizer.pipeline import Pipeline
from src.utils.logging_setup import setup_logging
from tests.documents import FAKE_CF, FakeNER, FakeReader, letter_pdf

__all__ = ["FAKE_CF", "make_settings"]


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


@pytest.fixture
def raw_log_stream() -> Iterator[io.StringIO]:
    """Everything the engine logs, at every level and unfiltered.

    No redaction filter runs here: a value that reaches this stream would reach
    any caller's logs, whatever handler it installs.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(level)


@pytest.fixture
def fake_reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def pipeline(fake_reader: FakeReader) -> Pipeline:
    return Pipeline(fake_reader, FakeNER())


@pytest.fixture
def jobs(
    pipeline: Pipeline, settings: Settings, tmp_path: Path
) -> Iterator[JobManager]:
    manager = JobManager(pipeline, settings, tmp_path / "work")
    yield manager
    manager.shutdown()


@pytest.fixture
def client(settings: Settings, jobs: JobManager) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_job_manager] = lambda: jobs
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def letter(tmp_path: Path) -> Path:
    return letter_pdf(
        tmp_path / "lettera.pdf",
        qr_text="https://referti.example.it/r?id=2026123456",
        signature=True,
    )
