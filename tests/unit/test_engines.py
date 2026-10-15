"""OCR, NER and visual detectors with fake models: no weights, no network."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pymupdf
import pytest

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.detect.ner import GlinerNER
from anonmyzation_pipeline.services.anonymization.ocr import (
    DoctrOCR,
    render_page,
    resolve_device,
)
from tests.documents import letter_pdf, native_words, scanned_pdf


def doctr_result(
    words: list[tuple[str, float, tuple[float, float, float, float]]],
) -> SimpleNamespace:
    """The shape of a docTR document: pages -> blocks -> lines -> words (relative geometry)."""
    line = SimpleNamespace(
        words=[
            SimpleNamespace(
                value=t, confidence=c, geometry=((g[0], g[1]), (g[2], g[3]))
            )
            for t, c, g in words
        ]
    )
    return SimpleNamespace(
        pages=[SimpleNamespace(blocks=[SimpleNamespace(lines=[line])])]
    )


def test_doctr_words_are_mapped_to_pdf_points(tmp_path: Path) -> None:
    calls = []

    def predictor(images: list[np.ndarray]) -> SimpleNamespace:
        calls.append(images[0].shape)
        return doctr_result([("Rossi", 0.98, (0.1, 0.2, 0.3, 0.25))])

    ocr = DoctrOCR(dpi=72, margin_pt=0, predictor=predictor)
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        words = ocr.page_words(page)

    assert ocr.ready
    assert len(calls) == 1
    assert words[0].text == "Rossi"
    assert words[0].rect == pytest.approx((60, 160, 180, 200))


def test_doctr_reads_rotated_margins(tmp_path: Path) -> None:
    def predictor(images: list[np.ndarray]) -> SimpleNamespace:
        h, w = images[0].shape[:2]
        if w > h:  # a rotated margin strip
            return doctr_result([("Majorana", 0.95, (0.1, 0.2, 0.4, 0.8))])
        return doctr_result([])

    ocr = DoctrOCR(dpi=72, margin_pt=40, predictor=predictor)
    with pymupdf.open() as doc:
        words = ocr.page_words(doc.new_page(width=600, height=800))

    vertical = [w for w in words if w.source == "doctr-vertical"]
    assert {w.text for w in vertical} == {"Majorana"}
    assert all(w.rect[2] - w.rect[0] < w.rect[3] - w.rect[1] for w in vertical)


def test_low_confidence_margin_words_are_dropped() -> None:
    ocr = DoctrOCR(
        dpi=72,
        margin_pt=40,
        predictor=lambda images: doctr_result([("x", 0.2, (0.1, 0.1, 0.2, 0.2))]),
    )
    with pymupdf.open() as doc:
        words = ocr.page_words(doc.new_page(width=600, height=800))

    assert all(w.source == "doctr" for w in words)


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
    from anonmyzation_pipeline.services.anonymization.ocr import Word

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
