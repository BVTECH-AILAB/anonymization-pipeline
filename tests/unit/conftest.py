"""Unit-test app: real routers and middleware, job manager on a fake OCR server and NER."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anonmyzation_pipeline.config import Settings
from anonmyzation_pipeline.main import create_app
from anonmyzation_pipeline.services.anonymization.pipeline import Pipeline
from anonmyzation_pipeline.services.jobs import JobManager, get_job_manager
from tests.documents import FakeNER, FakeReader, letter_pdf


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
