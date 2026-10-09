"""Unit-test app: real routers, middleware and pipeline, a fake VLM, no network."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline.main import create_app
from anonymization_pipeline.pipeline_config import PipelineConfig
from tests.conftest import FAKE_CF, make_settings
from tests.pipeline import FakeVLM, IdealModel

TEXT = f"Paziente: Mario Rossi\nCF: {FAKE_CF}\n"
VALUES = {"Mario Rossi": "PERSONA", FAKE_CF: "CODICE_FISCALE"}


@dataclass
class FakeBackend:
    """The VLM seen by the app: an ideal model and the models it lists (None: unreachable)."""

    model: IdealModel = field(default_factory=lambda: IdealModel(VALUES))
    available: set[str] | None = None

    def client(self, cfg: PipelineConfig) -> FakeVLM:
        return FakeVLM(cfg, self.model, self.available)


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def make_client(backend: FakeBackend) -> Iterator[Callable[..., TestClient]]:
    """``make_client(**settings)``: a started app on the fake backend."""
    clients: list[TestClient] = []

    def build(**overrides: Any) -> TestClient:
        app = create_app(make_settings(**overrides), vlm_client_factory=backend.client)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
