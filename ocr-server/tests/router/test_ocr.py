"""The HTTP side of the OCR server: status codes, bodies, logs without the text."""

import logging

import pytest
from fastapi.testclient import TestClient

from src.config import Settings
from src.main import create_app
from src.services import ocr as ocr_service
from tests.conftest import SECRET, FakeEngine, client_for, png


@pytest.fixture
def seen_dpi(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every ``dpi`` the router passes to the page reading."""
    seen: list[float] = []
    original = ocr_service.page_results

    def spy(img, dpi, *args):  # noqa: ANN001, ANN202 - same signature as page_results
        seen.append(dpi)
        return original(img, dpi, *args)

    monkeypatch.setattr(ocr_service, "page_results", spy)
    return seen


def test_ocr_follows_liteparse_contract_and_never_logs_text(
    caplog: pytest.LogCaptureFixture, seen_dpi: list[float]
) -> None:
    engine = FakeEngine()
    caplog.set_level(logging.DEBUG)
    with client_for(engine) as client:
        r = client.post(
            "/ocr",
            files={"file": ("p.png", png(), "image/png")},
            data={"language": "it"},
            headers={"X-Render-DPI": "300"},
        )
    assert r.status_code == 200
    assert r.json() == {
        "results": [
            {
                "text": SECRET,
                "bbox": [0.0, 0.0, 10.0, 10.0],
                "confidence": 0.9,
                "polygon": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            }
        ]
    }
    assert seen_dpi == [300]
    assert SECRET not in caplog.text


def test_recognize_reads_one_line_and_never_logs_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with client_for(FakeEngine()) as client:
        r = client.post("/recognize", files={"file": ("w.png", png(), "image/png")})
        bad = client.post("/recognize", files={"file": ("w.png", b"x", "image/png")})
    with client_for(FakeEngine(fail=True)) as client:
        failed = client.post(
            "/recognize", files={"file": ("w.png", png(), "image/png")}
        )
    assert r.status_code == 200
    assert r.json() == {"text": SECRET, "confidence": 0.9123}
    assert bad.status_code == 400
    assert failed.status_code == 500 and failed.json() == {"error": "OCR failed"}
    assert SECRET not in caplog.text


def test_recognize_is_503_until_models_load() -> None:
    client = TestClient(create_app(Settings(), FakeEngine(ready=False)))  # type: ignore[arg-type]
    r = client.post("/recognize", files={"file": ("w.png", png(), "image/png")})
    assert r.status_code == 503


def test_default_dpi_without_header(seen_dpi: list[float]) -> None:
    with client_for(FakeEngine(), default_dpi=150) as client:
        client.post("/ocr", files={"file": ("p.png", png(), "image/png")})
    assert seen_dpi == [150]


def test_health_is_503_until_models_load() -> None:
    engine = FakeEngine(ready=False)
    app = create_app(Settings(), engine)  # type: ignore[arg-type]
    client = TestClient(app)  # no lifespan: nothing loads
    assert client.get("/health").status_code == 503
    assert (
        client.post("/ocr", files={"file": ("p.png", png(), "image/png")}).status_code
        == 503
    )
    with TestClient(app) as started:
        assert started.get("/health").json() == {"status": "healthy"}


def test_bad_image_and_engine_failure(caplog: pytest.LogCaptureFixture) -> None:
    with client_for(FakeEngine()) as client:
        r = client.post("/ocr", files={"file": ("p.png", b"not an image", "image/png")})
        assert r.status_code == 400 and r.json() == {"error": "unreadable image"}
    with client_for(FakeEngine(), max_upload_mb=1) as client:
        r = client.post(
            "/ocr", files={"file": ("p.png", b"0" * (1024 * 1024 + 1), "image/png")}
        )
        assert r.status_code == 400
    with client_for(FakeEngine(fail=True)) as client:
        r = client.post("/ocr", files={"file": ("p.png", png(), "image/png")})
    assert r.status_code == 500 and r.json() == {"error": "OCR failed"}
    assert SECRET not in caplog.text
