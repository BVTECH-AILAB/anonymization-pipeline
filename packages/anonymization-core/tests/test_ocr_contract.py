"""The reader's side of OCR_CONTRACT.md: requests sent, answers read.

The server's side is services/ocr-server/tests/test_contract.py.
"""

from pathlib import Path

import httpx
import numpy as np
import pytest

from anonymization_core.reader import LiteparseReader, server_words
from anonymization_core.testing import (
    FAKE_CF,
    OcrServerStub,
    _parser,
    letter_pdf,
    page_views,
    scanned_pdf,
)

DPI = 150


def _reader(stub: OcrServerStub) -> LiteparseReader:
    return LiteparseReader(
        "http://ocr.test", dpi=DPI, parser=_parser(DPI), client=stub.client()
    )


def test_health_gates_readiness() -> None:
    healthy, loading = OcrServerStub(), OcrServerStub(health=503)

    reader = _reader(healthy)
    reader.load()
    with pytest.raises(RuntimeError):
        _reader(loading).load()

    assert reader.ready
    assert [(r.method, r.url.path) for r in healthy.requests] == [("GET", "/health")]


def test_a_scanned_page_is_sent_and_read_as_the_contract_says(tmp_path: Path) -> None:
    scan = scanned_pdf(letter_pdf(tmp_path / "l.pdf"), tmp_path / "s.pdf", dpi=DPI)
    stub = OcrServerStub(
        results=[
            {
                "text": FAKE_CF,
                "bbox": [300.0, 400.0, 600.0, 430.0],
                "confidence": 0.97,
                "polygon": [[300, 400], [600, 400], [600, 430], [300, 430]],
            }
        ]
    )
    reader = _reader(stub)
    reader.load()

    pages = reader.read(scan, page_views(scan), lambda _: None)

    assert stub.violations == []
    ocr = [r for r in stub.requests if r.url.path == "/ocr"]
    assert ocr
    assert ocr[0].headers["x-render-dpi"] == str(DPI)
    assert b'name="language"' in ocr[0].content and b"\r\n\r\nit\r\n" in ocr[0].content
    words = [w for p in pages for w in (p.ocr_words or [])]
    (cf,) = [w for w in words if w.text == FAKE_CF]
    scale = 72 / DPI
    assert cf.rect == pytest.approx(
        (300 * scale, 400 * scale, 600 * scale, 430 * scale)
    )
    assert cf.conf == pytest.approx(0.97)


def test_answers_are_read_field_by_field() -> None:
    words = server_words(
        [
            {"text": "Rossi", "bbox": [0, 0, 50, 10]},  # no confidence, no polygon
            {"text": "  ", "bbox": [0, 20, 50, 30], "confidence": 0.9},  # empty
            {"text": "Bianchi", "bbox": [0, 40, 50], "confidence": 0.9},  # bad bbox
            {
                "text": "Verdi Neri",  # a vertical line in one result
                "bbox": [100, 0, 110, 120],
                "confidence": 0.8,
                "polygon": [[100, 0], [100, 120], [110, 120], [110, 0]],
            },
        ],
        1.0,
    )

    by_text = {w.text: w for w in words}
    assert set(by_text) == {"Rossi", "Verdi", "Neri"}
    assert by_text["Rossi"].conf == 0.0
    assert by_text["Rossi"].direction == "ltr"
    assert by_text["Verdi"].direction == "ttb"


def test_recognize_sends_one_png_and_reads_text_and_confidence() -> None:
    stub = OcrServerStub(recognized=(FAKE_CF, 0.91))
    reader = _reader(stub)
    reader.load()

    text, confidence = reader._recognize(np.full((20, 120, 3), 255, np.uint8))

    assert (text, confidence) == (FAKE_CF, 0.91)
    assert stub.violations == []
    assert stub.requests[-1].url.path == "/recognize"


def test_a_server_error_fails_the_reading(tmp_path: Path) -> None:
    scan = scanned_pdf(letter_pdf(tmp_path / "l.pdf"), tmp_path / "s.pdf", dpi=DPI)

    class Failing(OcrServerStub):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/ocr":
                return httpx.Response(500, json={"error": "OCR failed"})
            return super().__call__(request)

    reader = _reader(Failing())
    reader.load()

    with pytest.raises(httpx.HTTPStatusError):
        reader.read(scan, page_views(scan), lambda _: None)
