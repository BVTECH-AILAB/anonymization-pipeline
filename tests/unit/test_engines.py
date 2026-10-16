"""OCR, NER and visual detectors with fake models: no weights, no network."""

import threading
import time
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.detect.ner import (
    CombinedNER,
    Gliner2NER,
    GlinerNER,
)
from anonmyzation_pipeline.services.anonymization.device import resolve_device
from anonmyzation_pipeline.services.anonymization.layers import Word
from anonmyzation_pipeline.services.anonymization.ocr import (
    RapidOcrEngine,
    parse_words,
    render_page,
)
from tests.documents import letter_pdf, native_words, scanned_pdf


def test_words_are_mapped_to_pdf_points() -> None:
    calls = []

    def reader(img: np.ndarray) -> list:
        calls.append(img.shape)
        h, w = img.shape[:2]
        return [("Rossi", 0.98, (0.1 * w, 0.2 * h, 0.3 * w, 0.25 * h), 0)]

    ocr = RapidOcrEngine(dpi=72, margin_pt=0, reader=reader)
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        words = ocr.pages_words([page])[0]

    assert ocr.ready
    assert len(calls) == 1
    assert words[0].text == "Rossi"
    assert words[0].rect == pytest.approx((60, 160, 180, 200))


def test_pages_are_read_in_parallel_and_returned_in_order() -> None:
    active, peak, lock = [0], [0], threading.Lock()

    def reader(img: np.ndarray) -> list:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return [(str(img.shape[1]), 0.9, (1, 1, 5, 5), 0)]  # width tells the page

    ocr = RapidOcrEngine(dpi=72, margin_pt=0, workers=3, reader=reader)
    progress: list[int] = []
    with pymupdf.open() as doc:
        for i in range(7):
            doc.new_page(width=100 + i, height=200)
        words = ocr.pages_words(list(doc), progress.append)

    assert [w[0].text for w in words] == [str(100 + i) for i in range(7)]
    assert peak[0] > 1
    assert progress[-1] == 7


def test_rotated_margins_are_read() -> None:
    def reader(img: np.ndarray) -> list:
        h, w = img.shape[:2]
        if w > h:  # a rotated margin strip
            return [("Majorana", 0.95, (0.1 * w, 0.2 * h, 0.4 * w, 0.8 * h), 0)]
        return []

    ocr = RapidOcrEngine(dpi=72, margin_pt=40, reader=reader)
    with pymupdf.open() as doc:
        words = ocr.pages_words([doc.new_page(width=600, height=800)])[0]

    vertical = [w for w in words if w.source == "ocr-vertical"]
    assert {w.text for w in vertical} == {"Majorana"}
    assert all(w.rect[2] - w.rect[0] < w.rect[3] - w.rect[1] for w in vertical)
    assert {w.line for w in vertical} <= set(range(10_000, 30_000))


def test_low_confidence_margin_words_are_dropped() -> None:
    def reader(img: np.ndarray) -> list:
        h, w = img.shape[:2]
        return [("x", 0.2, (0.1 * w, 0.1 * h, 0.2 * w, 0.2 * h), 0)]

    ocr = RapidOcrEngine(dpi=72, margin_pt=40, reader=reader)
    with pymupdf.open() as doc:
        words = ocr.pages_words([doc.new_page(width=600, height=800)])[0]

    assert all(w.source == "ocr" for w in words)


def test_parse_words_skips_the_empty_entries_of_a_blank_image() -> None:
    quad = ((10, 20), (50, 20), (50, 40), (10, 40))
    lines = ((("ROSSI", 0.9, quad),), ((),))

    assert parse_words(lines) == [("ROSSI", 0.9, (10.0, 20.0, 50.0, 40.0), 0)]
    assert parse_words(((),)) == []
    assert parse_words((("", 1.0, None),)) == []  # RapidOCR on a blank strip


class FakeGliner2:
    """GLiNER2 stand-in: the shape of ``extract_entities`` with spans and confidence."""

    def extract_entities(self, text: str, labels: list[str], **kwargs) -> dict:
        start = text.index("Verdani")
        return {
            "entities": {
                "person": [
                    {
                        "text": "Verdani",
                        "start": start,
                        "end": start + 7,
                        "confidence": 0.9,
                    }
                ],
                "card_cvv": [{"text": "123", "start": 0, "end": 3, "confidence": 0.9}],
            }
        }


def test_gliner2_spans_are_mapped_and_unknown_labels_ignored() -> None:
    ner = Gliner2NER(model=FakeGliner2())
    text = "123 visita del Sig. Verdani"

    spans = ner.detect(text)

    assert [(s.label, text[s.start : s.end], s.source) for s in spans] == [
        ("PERSON", "Verdani", "gliner2")
    ]


def test_combined_ner_joins_the_spans_and_is_ready_only_when_all_are() -> None:
    first = Gliner2NER(model=FakeGliner2())
    second = Gliner2NER()  # not loaded
    combined = CombinedNER(first, second)

    assert not combined.ready
    second._model = FakeGliner2()
    assert combined.ready
    assert len(combined.detect("Sig. Verdani")) == 2


def test_resolve_device() -> None:
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("cpu", "cuda")


def test_render_page_shape() -> None:
    with pymupdf.open() as doc:
        img = render_page(doc.new_page(width=72, height=144), dpi=72)

    assert img.shape == (144, 72, 3)


class FakeGliner:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def predict_entities(
        self, text: str, labels: list[str], threshold: float
    ) -> list[dict]:
        self.chunks.append(text)
        i = text.find("Mario Rossi")
        return (
            [
                {
                    "start": i,
                    "end": i + 11,
                    "label": "person",
                    "score": 0.97,
                    "text": "Mario Rossi",
                }
            ]
            if i >= 0
            else []
        )


def test_gliner_offsets_survive_chunking() -> None:
    model = FakeGliner()
    ner = GlinerNER(max_words=5, model=model)
    text = "riga uno con cinque parole\nriga due\nPaziente Mario Rossi oggi\n"

    spans = ner.detect(text)

    assert ner.ready
    assert len(model.chunks) >= 2
    assert [(s.label, text[s.start : s.end]) for s in spans] == [
        ("PERSON", "Mario Rossi")
    ]


def test_visual_codes_and_signatures_on_native_pages(tmp_path: Path) -> None:
    src = letter_pdf(
        tmp_path / "in.pdf", qr_text="https://x.example.it/r?id=1", signature=True
    )
    with pymupdf.open(src) as doc:
        found = visual.embedded_images(doc[0])
        codes = visual.codes(doc[0])

    assert sorted(f.label for f in found) == ["QRCODE", "SIGNATURE"]
    assert [c.label for c in codes] == ["QRCODE"]


def test_stamp_needs_a_frame() -> None:
    img = np.full((80, 200, 3), 255, np.uint8)
    img[2:5, :] = img[-5:-2, :] = (40, 50, 160)
    img[:, 2:5] = img[:, -5:-2] = (40, 50, 160)
    img[35:45, 30:170] = (40, 50, 160)

    assert visual._ink_class(img)[0] == "STAMP"
    img[:, :8] = img[:, -8:] = 255
    img[:8, :] = 255
    assert visual._ink_class(img)[0] == "SIGNATURE"


def test_unexplained_ink_finds_a_signature_on_a_scan(tmp_path: Path) -> None:
    native = letter_pdf(tmp_path / "native.pdf")
    scan = scanned_pdf(native, tmp_path / "scan.pdf", scribble=True)
    words = native_words(native)[0]

    with pymupdf.open(scan) as doc:
        found = visual.unexplained_ink(doc[0], words)

    assert any(
        f.rect[0] < 330 and f.rect[2] > 400 and 690 < f.rect[3] < 760 for f in found
    )


def test_stamp_from_text() -> None:
    words = [
        Word("Dott.", (400, 100, 425, 110)),
        Word("Bianchi", (428, 100, 470, 110)),
        Word("Medico", (400, 112, 435, 122)),
        Word("Chirurgo", (438, 112, 480, 122)),
        Word("OMCeO", (400, 124, 435, 134)),
        Word("lontano", (100, 600, 140, 610)),
    ]

    stamps = visual.stamp_from_text(0, words)

    assert len(stamps) == 1
    assert stamps[0].rect[0] < 400 and stamps[0].rect[3] > 134
