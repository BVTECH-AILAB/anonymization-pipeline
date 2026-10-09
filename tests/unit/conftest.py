"""Unit-test app: real routers and middleware, no network."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline.config import Settings
from anonymization_pipeline.main import create_app


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
