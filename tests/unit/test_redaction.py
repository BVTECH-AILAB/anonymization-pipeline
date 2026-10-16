from pathlib import Path

import numpy as np
import pymupdf
import pytest

from anonmyzation_pipeline.services.anonymization import redact as redact_module
from anonmyzation_pipeline.services.anonymization.layers import native_layer, ocr_layer
from anonmyzation_pipeline.services.anonymization.ocr import Word
from anonmyzation_pipeline.services.anonymization.pipeline import Box, Pipeline
from anonmyzation_pipeline.services.anonymization.redact import redact
from anonmyzation_pipeline.services.anonymization.verify import verify
from tests.conftest import FAKE_CF
from tests.documents import PATIENT, FakeNER, FakeOCR, letter_pdf


@pytest.fixture
def analyzed(tmp_path: Path):
    src = letter_pdf(
        tmp_path / "in.pdf",
        qr_text="https://x.example.it/r?id=2026123456",
        signature=True,
    )
    with pymupdf.open(src) as doc:  # traps: white invisible text and a note annotation
        page = doc[0]
        page.insert_text((300, 830), f"REF {FAKE_CF}", fontsize=3, color=(1, 1, 1))
        page.add_text_annot((500, 120), f"Richiamare {PATIENT}")
        doc.saveIncr()
    analysis = Pipeline(FakeOCR(), FakeNER()).analyze(src)
    return src, analysis


def render(path: Path, dpi: int = 50) -> np.ndarray:
    with pymupdf.open(path) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        return (
            np.frombuffer(pix.samples, np.uint8)
            .reshape(pix.height, pix.width, pix.n)
            .astype(int)
        )


def test_native_layer_has_a_box_per_character(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf")
    with pymupdf.open(src) as doc:
        layer = native_layer(doc[0])

    start = layer.text.index(FAKE_CF)
    rects = layer.rects(start, start + len(FAKE_CF))
    assert len(rects) == 1
    assert all(len(t.chars) == len(t.text) for t in layer.tokens)


def test_span_over_two_lines_gives_two_rectangles(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf", lines=["Paziente Mario", "Rossi ricoverato"])
    with pymupdf.open(src) as doc:
        layer = native_layer(doc[0])

    start = layer.text.index("Mario")
    assert len(layer.rects(start, layer.text.index("Rossi") + 5)) == 2


def test_glued_words_are_split_on_gaps(tmp_path: Path) -> None:
    path = tmp_path / "glued.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 100), "BOTTA", fontsize=10)
        page.insert_text(
            (50 + pymupdf.get_text_length("BOTTA", fontsize=10) + 4, 100),
            "DANILO",
            fontsize=10,
        )
        doc.save(path)
        with pymupdf.open(path) as again:
            layer = native_layer(again[0])

    assert "BOTTA DANILO" in layer.text


def test_ocr_layer_interpolates_vertical_words() -> None:
    layer = ocr_layer(0, [Word("ROSSI", (10, 100, 20, 150), 0.9, "ocr-vertical", 1)])

    chars = layer.tokens[0].chars
    assert chars[0][3] == 150  # first character at the bottom
    assert chars[-1][1] == 100


def test_redaction_removes_pii_and_keeps_the_rest(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"

    redact(src, analysis, out)
    report = verify(src, out, analysis)

    assert report.ok, report.issues
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
        assert FAKE_CF not in text
        assert PATIENT not in text
        assert "morbo di Crohn" in text
        assert "Azienda Sanitaria Test" in text
        assert list(doc[0].annots()) == []
        assert FAKE_CF not in str(doc.metadata)
        assert doc.metadata["author"] == ""
    assert max(report.pixel_changed_outside) <= 2e-4


def test_header_colours_are_preserved(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    redact(src, analysis, out)

    before, after = render(src), render(out)

    assert np.array_equal(before[5:30, 5:200], after[5:30, 5:200])


def test_incremental_history_is_dropped(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    redact(src, analysis, out)

    assert PATIENT.encode() in src.read_bytes() or b"Richiamare" in src.read_bytes()
    assert b"Richiamare" not in out.read_bytes()


def test_verification_fails_when_a_value_is_left(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    for box in analysis.boxes:
        if box.label == "FISCAL_CODE" and box.layer == "native" and box.rect[1] > 100:
            box.redact = False
    # keep one redacted copy of the value so the global search still looks for it
    analysis.boxes.append(
        Box(
            0,
            (300, 826, 340, 832),
            "FISCAL_CODE",
            FAKE_CF,
            1.0,
            ["test"],
            "native",
            True,
        )
    )

    redact(src, analysis, out)
    report = verify(src, out, analysis)

    assert not report.ok
    assert any("still in text" in issue for issue in report.issues)


def test_verification_fails_when_pixels_change_outside_the_boxes(
    analyzed, tmp_path: Path
) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    redact(src, analysis, out)
    with pymupdf.open(out) as doc:
        doc[0].draw_rect(pymupdf.Rect(100, 500, 300, 600), color=None, fill=(1, 0, 0))
        doc.saveIncr()

    assert not verify(src, out, analysis).ok


def test_label_style_writes_the_type(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"

    redact(src, analysis, out, style="label")

    with pymupdf.open(out) as doc:
        assert "CODICE FISCALE" in doc[0].get_text() or "CF" in doc[0].get_text()


def test_policy_keeps_quasi_identifiers(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf")

    default = Pipeline(FakeOCR(), FakeNER()).analyze(src)
    strict = Pipeline(FakeOCR(), FakeNER(), policy={"DATE": True}).analyze(src)

    assert not any(b.redact for b in default.boxes if b.label == "DATE")
    assert any(b.redact for b in strict.boxes if b.label == "DATE")


def test_search_and_snap(analyzed) -> None:
    _, analysis = analyzed

    hits = analysis.search("medicina interna")
    snapped = analysis.snap(0, (40, 100, 41, 101))

    assert len(hits) == 1
    assert snapped[2] > 41
    assert analysis.search("x") == []


def test_counts_group_distinct_values(analyzed) -> None:
    _, analysis = analyzed

    counts = analysis.counts()

    assert counts["FISCAL_CODE"] == 1
    assert counts["QRCODE"] == 1


def test_redaction_constants_match_pymupdf() -> None:
    runtime = vars(pymupdf)

    assert redact_module.REDACT_IMAGE_PIXELS == runtime["PDF_REDACT_IMAGE_PIXELS"]
    assert (
        redact_module.REDACT_LINE_ART_IF_COVERED
        == runtime["PDF_REDACT_LINE_ART_REMOVE_IF_COVERED"]
    )
    assert redact_module.REDACT_TEXT_REMOVE == runtime["PDF_REDACT_TEXT_REMOVE"]
