"""Surgical redaction: only what lies under the boxes leaves a native page."""

import io
from dataclasses import replace
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from anonymization_pipeline.detectors.ner import Engine
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import Geometry
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.render.boxes import Box
from anonymization_pipeline.render.pdf import Redacted, redact
from anonymization_pipeline.render.surgical import COVER_MARK
from anonymization_pipeline.services import anonymization as service
from anonymization_pipeline.services.anonymization import anonymize
from anonymization_pipeline.verify.pdf import VerificationReport, verify
from tests.conftest import DEFAULTS_FILE, FAKE_CF, make_settings
from tests.documents import HEIGHT, PATIENT, WIDTH, letter_pdf


def _cfg(**redaction: object) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    return cfg.model_copy(
        update={"redaction": cfg.redaction.model_copy(update=redaction)}
    )


def _layers(data: bytes) -> list[TextLayer]:
    pdf = pdfium.PdfDocument(data)
    try:
        return [native_layer(pdf[i], i) for i in range(len(pdf))]
    finally:
        pdf.close()


def _box(data: bytes, word: str, category: str, page: int = 0) -> Box:
    layer = _layers(data)[page]
    start = layer.text.index(word)
    ((rect, horizontal),) = layer.line_rects(start, start + len(word))
    return Box(page, rect, category, "native", horizontal)


def test_text_outside_the_boxes_stays_text(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()
    cfg = _cfg()

    result = anonymize(src, cfg)

    assert result.status == "done" and result.strategies == {0: "surgical"}
    text = _layers(result.pdf or b"")[0].text
    assert "morbo di Crohn" in text and "LETTERA DI DIMISSIONE" in text
    for value in (FAKE_CF, PATIENT, "Laura Bianchi"):
        assert value not in text
        assert value.encode() not in (result.pdf or b"")


def test_text_inside_a_form_falls_back_to_flatten(tmp_path: Path) -> None:
    path = tmp_path / "form.pdf"
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    cv.beginForm("head")
    cv.setFont("Helvetica", 12)
    cv.drawString(40, HEIGHT - 40, "Dott. Luca Verdi - Studio medico")
    cv.endForm()
    cv.doForm("head")
    cv.save()
    src = path.read_bytes()

    redacted = redact(src, [_box(src, "Luca Verdi", "PERSONA")], _cfg().redaction)

    assert redacted.strategies == {0: "flatten"}
    assert "Luca" not in _layers(redacted.pdf)[0].text


def test_an_image_partly_under_a_box_is_blanked_there_only(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf", signature=True).read_bytes()
    left_half = Box(0, (300, 700, 360, 740), "FIRMA", "visual")

    redacted = redact(src, [left_half], _cfg().redaction)

    assert redacted.strategies == {0: "surgical"}
    report = verify(src, redacted, set(), _cfg())
    assert report.ok, report.failures


def test_a_box_over_the_bottom_of_a_line_keeps_that_line(tmp_path: Path) -> None:
    # a visual box (a barcode) reaching into the lower part of a text line: the letters are
    # centred outside it, stay in the page, and their ink under the cover is not a leftover
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()
    line = _box(src, "morbo di Crohn", "PERSONA").rect
    x0, y0, x1, y1 = line
    code = Box(0, (x0, y0 + 0.8 * (y1 - y0), x1, y1 + 30), "CODICE_A_BARRE", "visual")

    redacted = redact(src, [code], _cfg().redaction)

    assert redacted.strategies == {0: "surgical"}
    assert "morbo di Crohn" in _layers(redacted.pdf)[0].text
    report = verify(src, redacted, set(), _cfg())
    assert report.ok, report.failures


def _cover_only(src: bytes, box: Box) -> bytes:
    """A black rectangle drawn on the box, marked as a cover, without removing anything."""
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(src)
        try:
            page = pdf[0]
            left, bottom, right, top = Geometry(page).to_page(box.rect)
            rect = pdfium_c.FPDFPageObj_CreateNewRect(
                left, bottom, right - left, top - bottom
            )
            pdfium_c.FPDFPageObj_SetFillColor(rect, 0, 0, 0, 255)
            pdfium_c.FPDFPath_SetDrawMode(rect, pdfium_c.FPDF_FILLMODE_ALTERNATE, 0)
            pdfium_c.FPDFPageObj_AddMark(rect, COVER_MARK.encode())
            pdfium_c.FPDFPage_InsertObject(page.raw, rect)
            pdfium_c.FPDFPage_GenerateContent(page.raw)
            out = io.BytesIO()
            pdf.save(out, flags=pdfium_c.FPDF_NO_INCREMENTAL)
            page.close()
            return out.getvalue()
        finally:
            pdf.close()


def test_xray_sees_content_left_under_a_cover(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()
    box = _box(src, FAKE_CF, "CODICE_FISCALE")
    covered = Redacted(_cover_only(src, box), [box], {0: "surgical"})

    report = verify(src, covered, set(), _cfg())

    assert ("content_under_box", 0, "CODICE_FISCALE") in {
        (f.check, f.page, f.category) for f in report.failures
    }


def test_a_surgical_page_refused_by_the_verification_is_flattened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[int, str]] = []
    real = service.verify

    def first_fails(
        original: bytes,
        redacted: Redacted,
        values: set[tuple[str, str]],
        cfg: PipelineConfig,
        ner: Engine | None = None,
    ) -> VerificationReport:
        calls.append(dict(redacted.strategies))
        report = real(original, redacted, values, cfg, ner)
        if len(calls) == 1:
            report.fail("pixels_outside_boxes", 0)
        return report

    monkeypatch.setattr(service, "verify", first_fails)

    result = anonymize(letter_pdf(tmp_path / "in.pdf").read_bytes(), _cfg())

    assert calls == [{0: "surgical"}, {0: "flatten"}]
    assert result.status == "done" and result.strategies == {0: "flatten"}


def test_rotated_page_loses_whole_values(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=letter_pdf(tmp_path / "in.pdf"))
    writer.pages[0].rotate(90)
    buf = io.BytesIO()
    writer.write(buf)

    result = anonymize(buf.getvalue(), _cfg())

    assert result.status == "done" and result.strategies == {0: "surgical"}
    # PDFium may read the rebuilt glyphs of a rotated page in another order: check the words
    text = _layers(result.pdf or b"")[0].text
    assert "aziente:" in text and "morbo di Crohn" in text
    for fragment in ("Mario", "Rossi", "ario", "ossi", FAKE_CF[:4], FAKE_CF[-4:]):
        assert fragment not in text


def test_label_style_writes_the_category_as_a_cover(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()
    cfg = _cfg(style="label")

    result = anonymize(src, cfg)

    assert result.status == "done" and result.strategies == {0: "surgical"}
    assert "CODICE FISCALE" in _layers(result.pdf or b"")[0].text


def test_flatten_strategy_rasterises_every_page_with_boxes(tmp_path: Path) -> None:
    result = anonymize(
        letter_pdf(tmp_path / "in.pdf").read_bytes(), _cfg(strategy="flatten")
    )

    assert result.strategies == {0: "flatten"}
    assert _layers(result.pdf or b"")[0].text.strip() == ""


def test_surgical_output_is_not_much_larger(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf", pages=3).read_bytes()

    surgical = anonymize(src, _cfg())
    flat = anonymize(src, _cfg(strategy="flatten"))

    assert surgical.pdf and flat.pdf
    assert len(surgical.pdf) < 1.5 * len(src) + 20_000
    assert len(surgical.pdf) < len(flat.pdf)


def test_refused_page_is_rebuilt_from_the_original(tmp_path: Path) -> None:
    # a strategy that gives up after changing the page must not leak its changes to flatten
    src = letter_pdf(tmp_path / "in.pdf").read_bytes()
    box = _box(src, FAKE_CF, "CODICE_FISCALE")

    flattened = redact(src, [box], _cfg().redaction, flatten_pages={0})
    direct = redact(src, [box], _cfg(strategy="flatten").redaction)

    assert flattened.strategies == direct.strategies == {0: "flatten"}
    assert replace(flattened, pdf=b"") == replace(direct, pdf=b"")
