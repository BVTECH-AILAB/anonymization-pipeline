from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline import __version__
from anonymization_pipeline.config import Settings
from anonymization_pipeline.main import create_app
from tests.conftest import FAKE_CF


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(("db_up", "status"), [(True, 200), (False, 503)])
def test_readyz_reflects_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, db_up: bool, status: int
) -> None:
    monkeypatch.setattr("anonymization_pipeline.api.health.ping", AsyncMock(return_value=db_up))

    assert client.get("/readyz").status_code == status


def test_version(client: TestClient) -> None:
    assert client.get("/version").json() == {
        "name": "anonymization-pipeline",
        "version": __version__,
    }


def test_item_crud(client: TestClient) -> None:
    created = client.post("/items", json={"name": "a", "description": "b"})
    item_id = created.json()["id"]

    assert client.get(f"/items/{item_id}").json()["name"] == "a"
    assert [i["id"] for i in client.get("/items").json()] == [item_id]


def test_not_found_is_italian(client: TestClient) -> None:
    response = client.get("/items/999")

    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "detail": "Risorsa non trovata."}


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.post("/items", json={"name": FAKE_CF * 20})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert FAKE_CF not in response.text


def test_unhandled_error_is_generic(settings: Settings, log_stream) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(f"failure for {FAKE_CF}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert FAKE_CF not in response.text
    assert "RuntimeError" in log_stream.getvalue()
    assert FAKE_CF not in log_stream.getvalue()


def test_request_id_is_propagated(client: TestClient, log_stream) -> None:
    response = client.get("/items/1", headers={"X-Request-ID": "abcdef123456"})

    assert response.headers["x-request-id"] == "abcdef123456"
    assert "[abcdef123456]" in log_stream.getvalue()


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": f"cf {FAKE_CF}"})

    assert FAKE_CF not in response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 32


def test_docs_disabled_in_prod() -> None:
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings(environment="prod"))) as client:
        assert client.get("/docs").status_code == 404


def test_service_starts_without_database_url(settings: Settings) -> None:
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json() == {
            "status": "unavailable",
            "reason": "database",
        }
        response = client.get("/items")

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


def test_engine_is_built_when_database_url_is_set() -> None:
    from tests.conftest import make_settings

    app = create_app(make_settings(database_url="postgresql://user@localhost:1/db"))
    with TestClient(app):
        assert app.state.engine is not None
        assert app.state.sessionmaker is not None
