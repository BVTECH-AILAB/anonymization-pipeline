"""Integration tests against the compose Postgres (``just test-int``).

They apply every migration on an empty ``app_test`` database, exercise the
API on real tables, then downgrade to base: this also proves that every
migration's downgrade() works.
"""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from agenas_service.config import get_settings
from agenas_service.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def migrated() -> Iterator[None]:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    yield
    command.downgrade(config, "base")


@pytest.fixture
def client(migrated: None) -> Iterator[TestClient]:
    with TestClient(create_app(get_settings())) as test_client:
        yield test_client


def test_readyz_with_real_database(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}


def test_item_round_trip(client: TestClient) -> None:
    created = client.post("/items", json={"name": "integrazione", "description": "x"})
    assert created.status_code == 201
    item_id = created.json()["id"]

    fetched = client.get(f"/items/{item_id}").json()

    assert fetched["name"] == "integrazione"
    assert fetched["created_at"]
