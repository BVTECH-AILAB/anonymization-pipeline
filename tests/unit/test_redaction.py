from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter

from anonmyzation_pipeline.services.anonymization.layers import Word, ocr_layer
from anonmyzation_pipeline.services.anonymization.pipeline import Box, Pipeline
from anonmyzation_pipeline.services.anonymization.release import (
    rebuild,
    redact_and_verify,
)
from anonmyzation_pipeline.services.anonymization.verify import verify
from tests.conftest import FAKE_CF
from tests.documents import (
    PATIENT,
    FakeNER,
    FakeReader,
    Page,
    draw_pdf,
    edit_pdf,
    letter_pdf,
)


def _traps(page: Page) -> None:
    page.text(300, 830, f"REF {FAKE_CF}", size=3, colour=(1, 1, 1))  # white, tiny


@pytest.fixture
def analyzed(tmp_path: Path):
    src = letter_pdf(
        tmp_path / "in.pdf",
        qr_text="https://x.example.it/r?id=2026123456",
        signature=True,
        extra=_traps,
    )
    edit_pdf(src, notes=[(0, (500, 700, 520, 720), f"Richiamare {PATIENT}")])
    analysis = Pipeline(FakeReader(), FakeNER()).analyze(src)
    return src, analysis


def render(path: Path, dpi: int = 50) -> np.ndarray:
    with pdfium.PdfDocument(path) as doc:
        return (
            doc[0]
            .render(scale=dpi / 72, rev_byteorder=True)
            .to_numpy()[:, :, :3]
            .astype(int)
        )


def page_text(path: Path) -> str:
    with pdfium.PdfDocument(path) as doc:
        return doc[0].get_textpage().get_text_bounded()


def test_native_layer_has_a_box_per_character(analyzed) -> None:
    _, analysis = analyzed
    layer = next(L for L in analysis.layers if L.source == "native")

    start = layer.text.index(FAKE_CF)
    rects = layer.rects(start, start + len(FAKE_CF))
    assert len(rects) == 1
    assert all(len(t.chars) == len(t.text) for t in layer.tokens)


def test_span_over_two_lines_gives_two_rectangles(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf", lines=["Paziente Mario", "Rossi ricoverato"])
    analysis = Pipeline(FakeReader(), FakeNER(())).analyze(src)
    layer = next(L for L in analysis.layers if L.source == "native")

    start = layer.text.index("Mario")
    assert len(layer.rects(start, layer.text.index("Rossi") + 5)) == 2


def test_ocr_layer_interpolates_vertical_words() -> None:
    layer = ocr_layer(
        0, [Word("ROSSI", (10, 100, 20, 150), 0.9, "ocr-vertical", 1, "btt")]
    )

    chars = layer.tokens[0].chars
    assert chars[0][3] == 150  # first character at the bottom
    assert chars[-1][1] == 100


def test_redaction_removes_pii_and_keeps_the_rest(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    text = page_text(out)
    assert FAKE_CF not in text
    assert PATIENT not in text
    assert "morbo di Crohn" in text
    assert "Azienda Sanitaria Test" in text
    reader = PdfReader(out)
    assert "/Annots" not in reader.pages[0]
    assert FAKE_CF not in str(reader.metadata)
    assert "/Author" not in (reader.metadata or {})
    assert max(release.report.pixel_changed_outside) <= 2e-4


def test_header_colours_are_preserved(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    redact_and_verify(src, analysis, out)

    before, after = render(src), render(out)

    # the band beside the title: no glyph edge, whose antialiasing changes with resolution
    band = (slice(3, 38), slice(220, 400))
    assert np.abs(before[band] - after[band]).max() <= 2
    assert tuple(after[20, 300]) == pytest.approx((13, 92, 115), abs=2)


def test_old_versions_and_notes_are_dropped(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    redact_and_verify(src, analysis, out)

    assert b"Richiamare" in src.read_bytes()
    assert b"Richiamare" not in out.read_bytes()


def test_verification_fails_when_a_value_is_left(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    for box in analysis.boxes:
        if box.label == "FISCAL_CODE" and box.rect[1] > 100:
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
    pages = {b.page for b in analysis.boxes if b.redact}

    rebuild(src, analysis, out, pages, "black")
    report = verify(src, out, analysis, pages)

    assert not report.ok
    assert any("still in text" in issue for issue in report.issues)


def test_verification_fails_when_pixels_change_outside_the_boxes(
    analyzed, tmp_path: Path
) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"
    release = redact_and_verify(src, analysis, out)
    stamped = draw_pdf(
        tmp_path / "stamp.pdf",
        lambda p: p.rect((100, 500, 300, 600), fill=(1, 0, 0), stroke=None),
    )
    writer = PdfWriter(clone_from=PdfReader(out))
    writer.pages[0].merge_page(PdfReader(stamped).pages[0])
    with (tmp_path / "tampered.pdf").open("wb") as fh:
        writer.write(fh)

    assert not verify(
        src, tmp_path / "tampered.pdf", analysis, set(release.flattened)
    ).ok


def test_label_style_paints_the_type(analyzed, tmp_path: Path) -> None:
    src, analysis = analyzed
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out, style="label")

    assert release.report.ok, release.report.issues
    cf = next(b for b in analysis.boxes if b.label == "FISCAL_CODE")
    x0, y0, x1, y1 = (int(v * 50 / 72) for v in cf.rect)
    box = render(out)[y0 : y1 + 1, x0 : x1 + 1]
    assert 120 < box.mean() < 250  # a light box with darker letters, not black


def test_policy_keeps_quasi_identifiers(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf")

    default = Pipeline(FakeReader(), FakeNER()).analyze(src)
    strict = Pipeline(FakeReader(), FakeNER(), policy={"DATE": True}).analyze(src)

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
