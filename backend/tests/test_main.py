import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import __version__
from src.config import Settings
from src.main import create_app
from tests.conftest import FAKE_CF, make_settings


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(("models_ready", "status"), [(True, 200), (False, 503)])
def test_readyz_reflects_models(
    client: TestClient, models_ready: bool, status: int
) -> None:
    app = client.app
    app.state.pipeline.reader._ready = models_ready  # type: ignore[attr-defined]
    app.state.pipeline.ner._ready = models_ready  # type: ignore[attr-defined]

    response = client.get("/readyz")

    assert response.status_code == status
    if not models_ready:
        assert response.json() == {"status": "unavailable", "reason": "models"}


def test_version(client: TestClient) -> None:
    assert client.get("/version").json() == {
        "name": "anonymization-pipeline",
        "version": __version__,
    }


def test_not_found_is_italian(client: TestClient) -> None:
    response = client.get(f"/anonymizations/sessions/{'0' * 32}")

    assert response.status_code == 404
    assert response.json() == {
        "code": "JOB_NOT_FOUND",
        "detail": "Documento non trovato o scaduto.",
    }


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.post(
        f"/anonymizations/sessions/{'0' * 32}/edits/stream",
        json={"search": FAKE_CF * 20},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert FAKE_CF not in response.text


def test_job_id_must_look_like_an_id(client: TestClient) -> None:
    response = client.get(f"/anonymizations/sessions/{FAKE_CF}")

    assert response.status_code == 422
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
    response = client.get(
        f"/anonymizations/sessions/{'0' * 32}", headers={"X-Request-ID": "abcdef123456"}
    )

    assert response.headers["x-request-id"] == "abcdef123456"
    assert "[abcdef123456]" in log_stream.getvalue()


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": f"cf {FAKE_CF}"})

    assert FAKE_CF not in response.headers["x-request-id"]
    assert len(response.headers["x-request-id"]) == 32


def test_docs_disabled_in_prod() -> None:
    with TestClient(create_app(make_settings(environment="prod"))) as client:
        assert client.get("/docs").status_code == 404


def test_service_starts_without_models(settings: Settings) -> None:
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json() == {
            "status": "unavailable",
            "reason": "models",
        }


def test_the_service_serves_no_web_page(settings: Settings) -> None:
    # the API serves no page, even when the old ANONPIP_UI_ENABLED flag is set
    with TestClient(
        create_app(make_settings(ui_enabled=True)), raise_server_exceptions=False
    ) as client:
        response = client.get("/")

    assert response.status_code == 404
    assert not hasattr(settings, "ui_enabled")


def test_version_matches_the_project() -> None:
    # the project is not installed: __version__ is a constant kept by hand
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert __version__ == project["project"]["version"]
