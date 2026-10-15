"""OCR words and layer, visual detection, and scanned pages through the service: fake OCR model,
no weights, no network."""

import io
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from anonymization_pipeline.detection import visual
from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import load_engine as load_ner
from anonymization_pipeline.layers.ocr import ocr_layer
from anonymization_pipeline.ocr import (
    LEFT_MARGIN_LINES,
    RIGHT_MARGIN_LINES,
    Direction,
    DoctrEngine,
    Reading,
    Word,
    load_engine,
    page_words,
)
from anonymization_pipeline.pdf.geometry import Rect
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.services.anonymization import anonymize
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings
from tests.documents import PATIENT, letter_pdf, signature_png


def _cfg(**ocr: object) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    data = cfg.model_dump()
    data["ocr"].update(ocr)
    return PipelineConfig.model_validate(data)


def doctr_result(
    words: list[tuple[str, float, tuple[float, float, float, float]]],
) -> object:
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


class FakeOcr:
    """OCR stand-in: the given words for full pages, nothing for the rotated margin strips."""

    def __init__(
        self, words: list[tuple[str, float, tuple[float, float, float, float]]]
    ) -> None:
        self.engine = DoctrEngine(lambda images: doctr_result(words))
        self.lock = threading.Lock()

    def read(self, image: np.ndarray) -> Reading:
        h, w = image.shape[:2]
        return [] if w > h else self.engine.read(image)


def test_doctr_words_are_mapped_to_points() -> None:
    engine = FakeOcr([("Rossi", 0.98, (0.1, 0.2, 0.3, 0.25))])
    image = np.zeros((800, 600, 3), np.uint8)

    (word,) = page_words(engine, image, 600, _cfg(margin_pt=0).ocr)

    assert (word.text, word.line) == ("Rossi", 0)
    assert word.rect == pytest.approx((60, 160, 180, 200))


def test_margins_are_read_rotated_with_their_own_lines() -> None:
    def predictor(images: list[np.ndarray]) -> object:
        h, w = images[0].shape[:2]
        if w > h:  # a rotated margin strip
            return doctr_result([("Majorana", 0.95, (0.1, 0.2, 0.4, 0.8))])
        return doctr_result([])

    engine = DoctrEngine(predictor)
    image = np.zeros((800, 600, 3), np.uint8)

    words = page_words(engine, image, 600, _cfg(margin_pt=60).ocr)

    lines = sorted({w.line for w in words})
    assert lines == [
        LEFT_MARGIN_LINES,
        RIGHT_MARGIN_LINES,
    ]  # never one line for both margins
    left = next(w for w in words if w.line == LEFT_MARGIN_LINES)
    assert (
        left.rect[2] <= 60 and left.rect[3] - left.rect[1] > left.rect[2] - left.rect[0]
    )
    assert left.direction in ("btt", "ttb")


@pytest.mark.parametrize(
    ("direction", "first_char_at_top"), [("btt", False), ("ttb", True)]
)
def test_vertical_words_get_characters_in_reading_order(
    direction: Direction, first_char_at_top: bool
) -> None:
    word = Word("ABCD", (10, 100, 20, 140), 0.9, 1, direction)

    (token,) = ocr_layer(0, [word]).tokens

    top = min(range(4), key=lambda i: token.chars[i][1])
    assert (top == 0) == first_char_at_top
    assert all(c[3] - c[1] == pytest.approx(10) for c in token.chars)


def test_codes_are_found_on_the_rendered_page(tmp_path: Path) -> None:
    pdf = pdfium.PdfDocument(
        letter_pdf(tmp_path / "in.pdf", qr_text="https://x.example.it/r?id=2026123456")
    )
    try:
        image = np.asarray(pdf[0].render(scale=200 / 72).to_pil().convert("RGB"))
    finally:
        pdf.close()

    (box,) = visual.codes(0, image, 200 / 72)

    # the code is drawn in (450, 650)-(540, 740) with its quiet zone: the modules are inside
    assert box.category == "QR_CODE"
    assert 448 <= box.rect[0] < box.rect[2] <= 542
    assert 648 <= box.rect[1] < box.rect[3] <= 742
    assert box.rect[2] - box.rect[0] > 60


def _png_page(draw: Callable[[ImageDraw.ImageDraw], None]) -> np.ndarray:
    image = Image.new("RGB", (400, 200), "white")
    draw(ImageDraw.Draw(image))
    return np.asarray(image)


def test_signature_and_stamp_are_told_apart() -> None:
    signature = np.asarray(Image.open(io.BytesIO(signature_png())).convert("RGB"))
    stamp = _png_page(
        lambda d: d.rectangle((5, 5, 395, 195), outline=(40, 40, 160), width=6)
    )

    assert visual.ink_category(signature) == "FIRMA"
    assert visual.ink_category(stamp) == "TIMBRO"


def test_embedded_images_except_logos_and_the_scan_itself() -> None:
    page = np.full((842, 595, 3), 255, np.uint8)
    bounds: list[Rect] = [(300, 700, 420, 740), (20, 10, 120, 50), (0, 0, 595, 842)]

    boxes = visual.embedded_images(0, page, 1.0, (595, 842), bounds)

    assert [b.rect for b in boxes] == [(300, 700, 420, 740)]
    assert boxes[0].category == "FIRMA"


def test_stamp_from_the_words_around_omceo() -> None:
    words = [
        Word("Dott.", (400, 700, 425, 710), 0.9, 1),
        Word("Luca", (428, 700, 450, 710), 0.9, 1),
        Word("OMCeO", (400, 712, 440, 722), 0.9, 2),
        Word("MI", (443, 712, 455, 722), 0.9, 2),
        Word("referto", (40, 100, 80, 110), 0.9, 3),
    ]

    (box,) = visual.stamp_from_text(0, words)

    assert box.category == "TIMBRO" and box.rect[0] < 400 and box.rect[2] > 455


def test_codes_win_over_images_covering_the_same_area() -> None:
    boxes = [
        visual.Box(0, (0, 0, 100, 100), "FIRMA", "visual"),
        visual.Box(0, (5, 5, 95, 95), "QR_CODE", "visual"),
        visual.Box(0, (200, 200, 300, 300), "TIMBRO", "visual"),
    ]

    assert [b.category for b in visual.dedupe(boxes)] == ["QR_CODE", "TIMBRO"]


def _scan(tmp_path: Path) -> bytes:
    """The letter rasterised at 150 dpi, like a scanner."""
    pdf = pdfium.PdfDocument(letter_pdf(tmp_path / "in.pdf"))
    try:
        image = pdf[0].render(scale=150 / 72).to_pil().convert("RGB")
    finally:
        pdf.close()
    buf = io.BytesIO()
    image.save(buf, "PNG", dpi=(150, 150))
    return buf.getvalue()


def test_scan_is_read_by_ocr_and_redacted(tmp_path: Path) -> None:
    # fake OCR: the codice fiscale line of the letter (x 40..300 pt, baseline 188 pt of 842)
    words = [
        ("Codice", 0.99, (40 / 595, 178 / 842, 72 / 595, 190 / 842)),
        ("fiscale:", 0.99, (75 / 595, 178 / 842, 110 / 595, 190 / 842)),
        (FAKE_CF, 0.99, (113 / 595, 178 / 842, 215 / 595, 190 / 842)),
    ]
    cfg = _cfg(enabled=True)

    result = anonymize(_scan(tmp_path), cfg, ocr=FakeOcr(words))

    assert result.status == "done", result.failures
    assert result.scanned_pages == [0] and result.strategies == {0: "scan"}
    assert result.counts["CODICE_FISCALE"] == 1
    box = next(b for b in result.boxes if b.category == "CODICE_FISCALE")
    assert box.rect[0] < 113 < box.rect[2]


def test_scan_without_ocr_is_blocked(tmp_path: Path) -> None:
    result = anonymize(_scan(tmp_path), _cfg(enabled=False))

    assert (result.status, result.reason) == ("blocked", "ocr_unavailable")


def test_enabled_ocr_without_a_model_never_runs(tmp_path: Path) -> None:
    with pytest.raises(DetectorUnavailable):
        anonymize(_scan(tmp_path), _cfg(enabled=True))


def test_visual_categories_count_and_redact(tmp_path: Path) -> None:
    data = letter_pdf(
        tmp_path / "in.pdf",
        qr_text="https://x.example.it/r?id=2026123456",
        signature=True,
    ).read_bytes()

    result = anonymize(data, _cfg())

    assert result.status == "done", result.failures
    assert result.counts["QR_CODE"] == 1 and result.counts["FIRMA"] == 1
    assert PATIENT not in repr(result)


@pytest.mark.models
def test_real_ocr_reads_and_redacts_a_scan(tmp_path: Path) -> None:
    """With the ocr and ner extras and the models in the caches (`just models`)."""
    cfg = load_pipeline_config(make_settings(pipeline_config_file=""))
    try:
        ocr_engine = load_engine(cfg.ocr.model_copy(update={"enabled": True}))
        ner_engine = load_ner(cfg.detection.ner)
    except DetectorUnavailable:
        pytest.skip("models missing in the caches")
    cfg = cfg.model_copy(update={"ocr": cfg.ocr.model_copy(update={"enabled": True})})

    result = anonymize(_scan(tmp_path), cfg, ner=ner_engine, ocr=ocr_engine)

    assert result.status == "done", result.failures
    assert result.counts.get("CODICE_FISCALE") == 1
    assert result.counts.get("PERSONA", 0) + result.counts.get("NOME", 0) >= 1
