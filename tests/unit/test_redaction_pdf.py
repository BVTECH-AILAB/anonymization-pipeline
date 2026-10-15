import io
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from anonymization_pipeline.detection import Outcome, detect
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf.geometry import intersects
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.render.boxes import Box, from_outcome, prepare
from anonymization_pipeline.render.pdf import Redacted, redact
from anonymization_pipeline.verify.pdf import strong_values, verify
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings
from tests.documents import (
    HEADER_COLOUR,
    HEIGHT,
    PATIENT,
    WIDTH,
    letter_pdf,
    overlay,
    trapped_letter,
)


@dataclass
class Run:
    src: bytes
    layers: list[TextLayer]
    outcome: Outcome
    redacted: Redacted
    cfg: PipelineConfig

    @property
    def values(self) -> set[tuple[str, str]]:
        return self.outcome.values(self.layers)


def _layers(data: bytes) -> list[TextLayer]:
    pdf = pdfium.PdfDocument(data)
    try:
        return [native_layer(pdf[i], i) for i in range(len(pdf))]
    finally:
        pdf.close()


def _cfg(**redaction: object) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    return cfg.model_copy(
        update={"redaction": cfg.redaction.model_copy(update=redaction)}
    )


def _run(
    src: bytes, cfg: PipelineConfig | None = None, scanned: set[int] | None = None
) -> Run:
    cfg = cfg or _cfg()
    layers = _layers(src)
    outcome = detect(layers, cfg)
    redacted = redact(src, from_outcome(layers, outcome), cfg.redaction, scanned)
    return Run(src, layers, outcome, redacted, cfg)


def _render(data: bytes, dpi: int = 50, page: int = 0) -> np.ndarray:
    pdf = pdfium.PdfDocument(data)
    try:
        bitmap = pdf[page].render(scale=dpi / 72)
        return np.asarray(bitmap.to_pil().convert("RGB"), dtype=int)
    finally:
        pdf.close()


@pytest.fixture
def run(tmp_path: Path) -> Run:
    return _run(trapped_letter(tmp_path / "in.pdf").read_bytes())


def test_personal_data_is_gone_from_the_whole_file(run: Run) -> None:
    data = run.redacted.pdf
    text = " ".join(layer.text for layer in _layers(data))

    for value in (FAKE_CF, PATIENT, "Richiamare", "Laura Bianchi"):
        assert value not in text
        assert value.encode() not in data
    assert run.redacted.strategies == {0: "surgical"}


def test_eponyms_and_clinical_text_get_no_box(run: Run) -> None:
    layer = run.layers[0]
    words = [t for t in layer.tokens if t.text.rstrip(".") in ("Crohn", "Babinski")]

    assert len(words) == 2
    assert not any(
        intersects(box.rect, word.rect) for box in run.redacted.boxes for word in words
    )


def test_metadata_annotations_and_history_are_removed(run: Run) -> None:
    pdf = pdfium.PdfDocument(run.redacted.pdf)
    try:
        page = pdf[0]
        assert pdfium_c.FPDF_GetTrailerEnds(pdf.raw, None, 0) == 1
        assert pdfium_c.FPDFPage_GetAnnotCount(page.raw) == 0
        metadata = {k: v for k, v in pdf.get_metadata_dict().items() if v}
    finally:
        pdf.close()
    assert metadata == {"Producer": "anonymization-pipeline"}


def test_header_colour_and_layout_outside_the_boxes_are_preserved(run: Run) -> None:
    before, after = _render(run.src), _render(run.redacted.pdf)

    header = after[2:12, 5:400]  # above the white text of the header
    assert np.abs(header - np.array(HEADER_COLOUR) * 255).max() < 6
    assert np.abs(before[2:12, 5:400] - header).max() < 6


def test_boxes_are_black_where_the_values_were(run: Run) -> None:
    image = _render(run.redacted.pdf, dpi=72)
    box = next(b for b in run.redacted.boxes if b.category == "CODICE_FISCALE")
    x0, y0, x1, y1 = (int(v) for v in box.rect)

    assert image[y0 + 1 : y1 - 1, x0 + 1 : x1 - 1].max() < 10


def test_verification_passes_on_the_redacted_file(run: Run) -> None:
    report = verify(run.src, run.redacted, run.values, run.cfg)

    assert report.ok, report.failures
    assert set(report.changed_outside) == {0}


def test_verification_fails_on_the_original_file(run: Run) -> None:
    unredacted = replace(run.redacted, pdf=run.src)

    report = verify(run.src, unredacted, run.values, run.cfg)

    checks = {f.check for f in report.failures}
    assert {
        "incremental_revisions",
        "metadata",
        "page_parts",
        "text_under_box",
        "value_in_text",
        "value_in_file",
        "value_detected_again",
    } <= checks


def _red_square(x: float, y: float) -> Callable[[canvas.Canvas], None]:
    def draw(cv: canvas.Canvas) -> None:
        cv.setFillColorRGB(1, 0, 0)
        cv.rect(x, HEIGHT - y - 20, 20, 20, stroke=0, fill=1)

    return draw


def test_verification_fails_on_a_change_outside_the_boxes(
    run: Run, tmp_path: Path
) -> None:
    path = tmp_path / "out.pdf"
    path.write_bytes(run.redacted.pdf)
    overlay(path, _red_square(500, 400))  # an empty area of the letter

    report = verify(
        run.src, replace(run.redacted, pdf=path.read_bytes()), run.values, run.cfg
    )

    assert [f.check for f in report.failures] == ["pixels_outside_boxes"]


def test_pixel_mask_uses_only_the_boxes_of_the_same_page(tmp_path: Path) -> None:
    # page 1 has no personal data where page 2 has the codice fiscale: a change on page 1 at the
    # coordinates of the box of page 2 must be seen
    path = tmp_path / "two.pdf"
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    cv.setFont("Helvetica", 10)
    cv.drawString(40, HEIGHT - 100, "Pagina senza dati")
    cv.showPage()
    cv.setFont("Helvetica", 10)
    cv.drawString(40, HEIGHT - 300, f"Codice fiscale: {FAKE_CF}")
    cv.save()
    result = _run(path.read_bytes())
    box = next(b for b in result.redacted.boxes if b.category == "CODICE_FISCALE")
    assert box.page == 1
    out = tmp_path / "out.pdf"
    out.write_bytes(result.redacted.pdf)
    overlay(out, _red_square(box.rect[0], box.rect[1] - 5), page=0)

    report = verify(
        result.src,
        replace(result.redacted, pdf=out.read_bytes()),
        result.values,
        result.cfg,
    )

    assert ("pixels_outside_boxes", 0) in {(f.check, f.page) for f in report.failures}
    assert result.redacted.strategies == {1: "surgical"}  # page 1 kept as it was


def test_text_outside_the_page_is_redacted(tmp_path: Path) -> None:
    path = tmp_path / "off.pdf"
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    cv.setFont("Helvetica", 10)
    cv.drawString(40, HEIGHT - 100, "Referto")
    cv.drawString(WIDTH + 50, 400, f"CF {FAKE_CF}")
    cv.save()

    result = _run(path.read_bytes())

    assert FAKE_CF.encode() not in result.redacted.pdf
    assert FAKE_CF not in _layers(result.redacted.pdf)[0].text
    assert verify(result.src, result.redacted, result.values, result.cfg).ok


def test_rotated_page_is_redacted_where_the_value_is_shown(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=letter_pdf(tmp_path / "in.pdf"))
    writer.pages[0].rotate(90)
    buf = io.BytesIO()
    writer.write(buf)

    result = _run(buf.getvalue())

    report = verify(result.src, result.redacted, result.values, result.cfg)
    assert report.ok, report.failures
    image = _render(result.redacted.pdf, dpi=72)
    assert image.shape[1] > image.shape[0]  # still landscape
    box = next(b for b in result.redacted.boxes if b.category == "CODICE_FISCALE")
    x0, y0, x1, y1 = (int(v) for v in box.rect)
    assert image[y0 + 1 : y1 - 1, x0 + 1 : x1 - 1].max() < 10


def test_label_style_writes_the_category(tmp_path: Path) -> None:
    result = _run(letter_pdf(tmp_path / "in.pdf").read_bytes(), _cfg(style="label"))

    image = _render(result.redacted.pdf, dpi=144)
    box = next(b for b in result.redacted.boxes if b.category == "CODICE_FISCALE")
    x0, y0, x1, y1 = (int(v * 2) for v in box.rect)
    inside = image[y0 + 2 : y1 - 2, x0 + 2 : x1 - 2]
    assert (inside == [222, 235, 252]).all(axis=2).mean() > 0.5  # light background
    assert (inside[..., 2] - inside[..., 0] > 60).any()  # blue text of the tag


def test_scanned_pages_are_stored_as_jpeg(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()

    result = _run(src, scanned={0})

    page = PdfReader(io.BytesIO(result.redacted.pdf)).pages[0]
    resources = cast(DictionaryObject, page["/Resources"])
    xobjects = cast(DictionaryObject, resources["/XObject"].get_object())
    filters = [
        cast(DictionaryObject, xobjects[n].get_object())["/Filter"] for n in xobjects
    ]
    assert filters == ["/DCTDecode"]


def test_report_never_holds_values(run: Run) -> None:
    report = verify(run.src, replace(run.redacted, pdf=run.src), run.values, run.cfg)

    text = repr(report)
    for value in (FAKE_CF, PATIENT, "Laura Bianchi"):
        assert value not in text


def test_strong_values_are_identifiers_and_full_names() -> None:
    strong = strong_values(
        {
            (FAKE_CF, "CODICE_FISCALE"),
            (PATIENT, "PERSONA"),
            ("Rossi", "PERSONA"),
            ("Roma", "LUOGO"),
            ("01/08/1985", "DATA_NASCITA"),
            ("12345", "NUMERO_CARTELLA"),
        }
    )

    assert strong == {"rssmra85m01h501q": "CODICE_FISCALE", "mariorossi": "PERSONA"}


def test_boxes_are_shaped_by_source_and_merged() -> None:
    boxes = [
        Box(0, (10, 10, 50, 20), "PERSONA", "native"),
        Box(0, (12, 11, 52, 21), "COGNOME", "native"),
        Box(0, (100, 100, 120, 110), "PERSONA", "ocr"),
        Box(0, (-50, 10, -20, 20), "PERSONA", "native"),  # outside the page: kept
    ]

    out = prepare(boxes, {0: (595, 842)})

    assert len(out) == 3
    assert out[0].rect == pytest.approx((9.5, 9.5, 52.5, 21.5))
    assert out[1].rect == (98.5, 98.5, 121.5, 111.5)
    assert out[2].rect[2] < 0


def _scan_pdf(tmp_path: Path, hidden_text: str | None = None, rotate: int = 0) -> bytes:
    """The letter rendered at 150 dpi and stored as a one-image PDF, optionally with a hidden
    OCR layer (render mode 3) and a rotation."""
    pdf = pdfium.PdfDocument(letter_pdf(tmp_path / "in.pdf"))
    try:
        image = pdf[0].render(scale=150 / 72).to_pil().convert("RGB")
    finally:
        pdf.close()
    path = tmp_path / "scan.pdf"
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=90)
    cv.drawImage(ImageReader(buf), 0, 0, width=WIDTH, height=HEIGHT)
    if hidden_text:
        text = cv.beginText(113, HEIGHT - 188)
        text.setTextRenderMode(3)
        text.setFont("Helvetica", 10)
        text.textOut(hidden_text)
        cv.drawText(text)
    cv.save()
    if rotate:
        writer = PdfWriter(clone_from=path)
        writer.pages[0].rotate(rotate)
        writer.write(path)
    return path.read_bytes()


def _cf_box() -> Box:
    return Box(0, (110.0, 178.0, 216.0, 192.0), "CODICE_FISCALE", "ocr")


def test_scan_is_redacted_in_its_own_image(tmp_path: Path) -> None:
    src = _scan_pdf(tmp_path, hidden_text=FAKE_CF)

    redacted = redact(src, [_cf_box()], _cfg().redaction, scanned={0})

    assert redacted.strategies == {0: "scan"}
    assert FAKE_CF not in _layers(redacted.pdf)[0].text  # hidden OCR layer dropped
    before, after = _render(src, dpi=72), _render(redacted.pdf, dpi=72)
    x0, y0, x1, y1 = (int(v) for v in redacted.boxes[0].rect)
    assert after[y0 + 1 : y1 - 1, x0 + 1 : x1 - 1].max() < 30
    outside = np.ones(before.shape[:2], bool)
    outside[y0 - 4 : y1 + 4, x0 - 4 : x1 + 4] = False
    assert (np.abs(before - after).max(axis=2)[outside] > 40).mean() < 2e-4
    report = verify(src, redacted, {(FAKE_CF, "CODICE_FISCALE")}, _cfg())
    assert report.ok, report.failures


def test_rotated_scan_falls_back_to_flatten(tmp_path: Path) -> None:
    src = _scan_pdf(tmp_path, rotate=90)

    redacted = redact(src, [_cf_box()], _cfg().redaction, scanned={0})

    assert redacted.strategies == {0: "flatten"}


def test_scan_with_visible_text_falls_back_to_flatten(tmp_path: Path) -> None:
    path = tmp_path / "mixed.pdf"
    path.write_bytes(_scan_pdf(tmp_path))
    overlay(path, lambda cv: cv.drawString(40, 40, "Visibile"))

    redacted = redact(path.read_bytes(), [_cf_box()], _cfg().redaction, scanned={0})

    assert redacted.strategies == {0: "flatten"}
