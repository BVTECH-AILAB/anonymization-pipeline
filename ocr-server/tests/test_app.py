import io
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from paddle_ocr_server.app import create_app
from paddle_ocr_server.config import Settings
from paddle_ocr_server.engine import (
    PaddleEngine,
    RapidEngine,
    make_engine,
    margin_results,
    page_results,
    parse_rapid,
    parse_result,
)
from paddle_ocr_server.words import Line, results

SECRET = "RSSMRA85M01H501U"  # noqa: S105 - textbook codice fiscale, not a credential


class FakeEngine:
    def __init__(self, ready: bool = True, fail: bool = False) -> None:
        self._ready = ready
        self.fail = fail
        self.dpi: float | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self._ready = True

    def read(self, _img: np.ndarray, dpi: float) -> list[dict]:
        if self.fail:
            raise RuntimeError(SECRET)
        self.dpi = dpi
        return [{"text": SECRET, "bbox": [0, 0, 10, 10], "confidence": 0.9}]


def _png(w: int = 40, h: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _client(engine: FakeEngine, **settings: object) -> TestClient:
    return TestClient(create_app(Settings(**settings), engine))  # type: ignore[arg-type]


def test_ocr_follows_liteparse_contract_and_never_logs_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine()
    caplog.set_level(logging.DEBUG)
    with _client(engine) as client:
        r = client.post(
            "/ocr",
            files={"file": ("p.png", _png(), "image/png")},
            data={"language": "it"},
            headers={"X-Render-DPI": "300"},
        )
    assert r.status_code == 200
    assert r.json() == {
        "results": [{"text": SECRET, "bbox": [0, 0, 10, 10], "confidence": 0.9}]
    }
    assert engine.dpi == 300
    assert SECRET not in caplog.text


def test_default_dpi_without_header() -> None:
    engine = FakeEngine()
    with _client(engine, default_dpi=150) as client:
        client.post("/ocr", files={"file": ("p.png", _png(), "image/png")})
    assert engine.dpi == 150


def test_health_is_503_until_models_load() -> None:
    engine = FakeEngine(ready=False)
    app = create_app(Settings(), engine)  # type: ignore[arg-type]
    client = TestClient(app)  # no lifespan: nothing loads
    assert client.get("/health").status_code == 503
    assert (
        client.post("/ocr", files={"file": ("p.png", _png(), "image/png")}).status_code
        == 503
    )
    with TestClient(app) as started:
        assert started.get("/health").json() == {"status": "healthy"}


def test_bad_image_and_engine_failure(caplog: pytest.LogCaptureFixture) -> None:
    with _client(FakeEngine()) as client:
        r = client.post("/ocr", files={"file": ("p.png", b"not an image", "image/png")})
        assert r.status_code == 400 and r.json() == {"error": "unreadable image"}
    with _client(FakeEngine(), max_upload_mb=1) as client:
        r = client.post(
            "/ocr", files={"file": ("p.png", b"0" * (1024 * 1024 + 1), "image/png")}
        )
        assert r.status_code == 400
    with _client(FakeEngine(fail=True)) as client:
        r = client.post("/ocr", files={"file": ("p.png", _png(), "image/png")})
    assert r.status_code == 500 and r.json() == {"error": "OCR failed"}
    assert SECRET not in caplog.text


def test_parse_result_reads_paddle_mapping() -> None:
    poly = np.array([[0, 0], [100, 0], [100, 10], [0, 10]])
    word = ((0, 0), (40, 0), (40, 10), (0, 10))
    res = {
        "rec_texts": ["Mario Rossi", "x"],
        "rec_scores": [0.9, 0.8],
        "rec_polys": [poly, poly],
        "text_word": [["Mario", " ", "Rossi"], ["x"]],
        "text_word_region": [[word, word, word], []],
    }
    lines = parse_result(res)
    assert [x.text for x in lines] == ["Mario Rossi", "x"]
    assert len(lines[0].words) == 3
    assert lines[1].words == []  # counts do not match: no word boxes
    assert lines[0].quad[2] == (100.0, 10.0)


def test_margin_pass_keeps_the_confident_rotation() -> None:
    img = np.zeros((300, 200, 3), np.uint8)
    upright = ((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0))
    calls: list[tuple[int, int]] = []

    def predict(strip: np.ndarray) -> list[Line]:
        calls.append(strip.shape[:2])
        # first call of each strip is the clockwise rotation: make it the good one
        return [Line("Mario Rossi", 0.9 if len(calls) % 2 else 0.3, upright)]

    out = margin_results(img, 20, predict, 0.5)
    assert calls == [(20, 300)] * 4  # rotated strips: 20 px high, page-height wide
    assert [r["text"] for r in out] == ["Mario", "Rossi", "Mario", "Rossi"]
    # clockwise reading: bottom to top on the page, inside the strip
    left = out[0]["polygon"]
    assert left[1][1] < left[0][1]
    assert all(0 <= x <= 20 for x, _ in left)
    right = out[2]["bbox"]
    assert right[0] >= 180


def test_page_results_margin_width_follows_dpi() -> None:
    img = np.zeros((100, 400, 3), np.uint8)
    widths: list[int] = []

    def predict(a: np.ndarray) -> list[Line]:
        widths.append(a.shape[0])
        return []

    page_results(img, 144, predict, margin_pt=36, min_score=0.5)
    assert widths == [100, 72, 72, 72, 72]  # page, then 36 pt at 144 dpi = 72 px strips
    widths.clear()
    page_results(img, 144, predict, margin_pt=0, min_score=0.5)
    assert widths == [100]


def test_rapid_result_keeps_its_word_boxes() -> None:
    class Res:
        txts = ("Mario Rossi", "x")
        scores = (0.9, 0.8)
        boxes = [
            np.array([[0, 0], [100, 0], [100, 10], [0, 10]]),
            np.array([[0, 20], [10, 20], [10, 30], [0, 30]]),
        ]
        word_results = (
            (
                ("Mario", 0.9, [[0, 0], [45, 0], [45, 10], [0, 10]]),
                ("Rossi", 0.9, [[52, 0], [100, 0], [100, 10], [52, 10]]),
            ),
            (("", 1.0, None),),
        )

    lines = parse_rapid(Res())
    assert [line.text for line in lines] == ["Mario Rossi", "x"]
    assert not lines[0].widen and len(lines[0].words) == 2
    out = results(lines)
    # recogniser boxes used as they are: no widening
    assert out[0]["bbox"] == [0.0, 0.0, 45.0, 10.0]
    assert out[1]["bbox"] == [52.0, 0.0, 100.0, 10.0]


def test_engine_chosen_by_setting() -> None:
    assert isinstance(make_engine(Settings()), RapidEngine)  # the default
    assert isinstance(make_engine(Settings(engine="paddle")), PaddleEngine)
