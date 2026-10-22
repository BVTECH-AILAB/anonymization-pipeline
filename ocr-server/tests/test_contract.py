"""The server's side of the engine's OCR contract.

backend/core/OCR_CONTRACT.md; the reader's side is
backend/core/tests/test_ocr_contract.py. Requests are built
exactly as the reader sends them.
"""

import io
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from paddle_ocr_server.app import create_app
from paddle_ocr_server.config import Settings
from paddle_ocr_server.words import Line, results

H_QUAD = ((10.0, 20.0), (110.0, 20.0), (110.0, 40.0), (10.0, 40.0))
V_QUAD = ((200.0, 10.0), (200.0, 210.0), (180.0, 210.0), (180.0, 10.0))


class ContractEngine:
    """Answers with what the real word splitting produces."""

    ready = True

    def load(self) -> None:
        return None

    def read(self, _img: np.ndarray, _dpi: float) -> list[dict]:
        return results(
            [
                Line("Mario Rossi", 0.91, H_QUAD),
                Line("Stampato da Rossi", 0.8, V_QUAD),
            ]
        )

    def recognize(self, _img: np.ndarray) -> tuple[str, float]:
        return "RSSMRA85M01H501U", 0.91234


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


def _client() -> TestClient:
    return TestClient(create_app(Settings(), ContractEngine()))  # type: ignore[arg-type]


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def test_health_answers_200_when_ready() -> None:
    with _client() as client:
        assert client.get("/health").status_code == 200


def test_ocr_answers_one_result_per_word_in_the_contract_shape() -> None:
    with _client() as client:
        r = client.post(
            "/ocr",
            files={"file": ("page.png", _png(), "image/png")},
            data={"language": "it"},
            headers={"X-Render-DPI": "300"},
        )

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"results"}
    for item in body["results"]:
        assert set(item) <= {"text", "bbox", "confidence", "polygon"}
        assert isinstance(item["text"], str) and item["text"].strip()
        x0, y0, x1, y1 = item["bbox"]
        assert all(_number(v) for v in (x0, y0, x1, y1)) and x0 <= x1 and y0 <= y1
        assert 0 <= item["confidence"] <= 1
        polygon = item["polygon"]
        assert len(polygon) == 4 and all(len(p) == 2 for p in polygon)
    horizontal = [i for i in body["results"] if i["text"] in ("Mario", "Rossi")]
    assert [i["text"] for i in horizontal] == ["Mario", "Rossi"]
    # TL -> TR is the reading direction: left to right on a horizontal line
    (tl, tr, _, _) = horizontal[0]["polygon"]
    assert tr[0] > tl[0]
    # a vertical line stays one result: the reader splits it
    assert any(i["text"] == "Stampato da Rossi" for i in body["results"])


def test_recognize_answers_text_and_confidence() -> None:
    with _client() as client:
        r = client.post("/recognize", files={"file": ("word.png", _png(), "image/png")})

    assert r.status_code == 200
    assert set(r.json()) == {"text", "confidence"}
    assert r.json()["text"] == "RSSMRA85M01H501U"
    assert 0 <= r.json()["confidence"] <= 1
