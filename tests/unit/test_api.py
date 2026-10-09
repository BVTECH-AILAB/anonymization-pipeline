from fastapi.testclient import TestClient

from anonymization_pipeline import __version__
from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import NotFoundError
from anonymization_pipeline.main import create_app
from tests.conftest import FAKE_CF


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}


def test_version(client: TestClient) -> None:
    assert client.get("/version").json() == {
        "name": "anonymization-pipeline",
        "version": __version__,
    }


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


def test_request_id_is_propagated(settings: Settings, log_stream) -> None:
    app = create_app(settings)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError()

    with TestClient(app) as client:
        response = client.get("/missing", headers={"X-Request-ID": "abcdef123456"})

    assert response.headers["x-request-id"] == "abcdef123456"
    assert response.json() == {"code": "NOT_FOUND", "detail": "Risorsa non trovata."}
    assert "[abcdef123456]" in log_stream.getvalue()


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": f"cf {FAKE_CF}"})

    assert FAKE_CF not in response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 32


def test_docs_disabled_in_prod() -> None:
    from tests.conftest import make_settings

    with TestClient(create_app(make_settings(environment="prod"))) as client:
        assert client.get("/docs").status_code == 404
