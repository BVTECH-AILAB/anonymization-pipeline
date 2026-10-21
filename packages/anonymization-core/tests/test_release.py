"""Redaction and release: flattened pages, pypdf rewrite, checks."""

import dataclasses
import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader

from anonymization_core.layers import Word
from anonymization_core.pdf_pages import to_pdf
from anonymization_core.pipeline import (
    Analysis,
    Pipeline,
    to_display,
)
from anonymization_core.release import (
    PRODUCER,
    cover_found,
    rebuild,
    redact_and_verify,
)
from anonymization_core.testing import (
    DOCTOR,
    FAKE_CF,
    PATIENT,
    FakeNER,
    FakeReader,
    Page,
    draw_pdf,
    edit_pdf,
    find_text,
    letter_pdf,
    scanned_pdf,
)
from anonymization_core.verify import verify


def _analyze(path: Path, words: dict[int, list[Word]] | None = None) -> Analysis:
    return Pipeline(FakeReader(words), FakeNER()).analyze(path)


def _text(path: Path) -> list[str]:
    with pdfium.PdfDocument(path) as doc:
        return [doc[i].get_textpage().get_text_bounded() for i in range(len(doc))]


def _dark(path: Path, pno: int, rect: tuple[float, float, float, float]) -> float:
    """Mean brightness of a rectangle of a page as displayed."""
    with pdfium.PdfDocument(path) as doc:
        img = doc[pno].render(scale=2).to_numpy()[:, :, :3]
    x0, y0, x1, y1 = (int(v * 2) for v in rect)
    return float(img[y0:y1, x0:x1].mean())


def test_letter_is_flattened_searchable_and_clean(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    assert release.flattened == [0]
    (text,) = _text(out)
    assert FAKE_CF not in text and PATIENT not in text
    assert "metformina" in text  # the invisible layer keeps the page searchable
    cf = next(b for b in analysis.boxes if b.label == "FISCAL_CODE")
    assert _dark(out, 0, cf.rect) < 60
    reader = PdfReader(out)
    assert reader.metadata == {"/Producer": PRODUCER}
    assert DOCTOR not in str(reader.metadata)


def test_pages_without_boxes_stay_native(tmp_path: Path) -> None:
    src = draw_pdf(
        tmp_path / "two.pdf",
        lambda p: p.text(72, 100, f"Codice fiscale {FAKE_CF}", size=12),
        lambda p: p.text(72, 100, "Terapia con metformina", size=12),
    )
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    assert release.flattened == [0]
    with pdfium.PdfDocument(out) as result:
        images = [
            o
            for o in result[0].get_objects()
            if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE
        ]
        texts = [
            o for o in result[1].get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_TEXT
        ]
    assert images and texts


def test_rotated_scan_is_flattened_as_displayed(tmp_path: Path) -> None:
    native = draw_pdf(
        tmp_path / "native.pdf", lambda p: p.text(100, 700, f"C.F. {FAKE_CF}", size=14)
    )
    cf = find_text(native, FAKE_CF)
    src = edit_pdf(
        scanned_pdf(native, tmp_path / "scan.pdf", dpi=100),
        tmp_path / "rotated.pdf",
        rotate={0: 90},
    )
    info = {
        "width": 842,
        "height": 595,
        "rotation": 90,
        "page_width": 595,
        "page_height": 842,
    }
    shown = to_display(cf, info)
    analysis = _analyze(src, {0: [Word(FAKE_CF, shown, 0.99, "ocr", 0)]})
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    with pdfium.PdfDocument(out) as result:
        # an upright page as it was shown: no resampling of a rotated image
        assert result[0].get_rotation() == 0
        assert result[0].get_size() == pytest.approx((842, 595), abs=0.01)
    assert _dark(out, 0, shown) < 60


def test_rotated_native_page_passes_the_pixel_check_and_stays_searchable(
    tmp_path: Path,
) -> None:
    def draw(p: Page) -> None:
        p.text(100, 700, f"Codice fiscale {FAKE_CF}", size=12, rotate=90)
        p.text(140, 700, "Terapia con metformina", size=12, rotate=90)

    src = edit_pdf(draw_pdf(tmp_path / "rotated_native.pdf", draw), rotate={0: 90})
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    (text,) = _text(out)
    assert FAKE_CF not in text and "metformina" in text


def test_a_value_redacted_once_is_covered_everywhere(tmp_path: Path) -> None:
    lines = [
        f"Paziente: {PATIENT}",
        "Terapia con metformina 1000 mg.",
        f"Controllo per {PATIENT} tra un mese.",
    ]
    src = letter_pdf(tmp_path / "l.pdf", lines=lines)
    analysis = _analyze(src)
    persons = [b for b in analysis.boxes if b.text == PATIENT]
    assert len(persons) >= 2
    for missed in persons[1:]:  # as if detection had found only the first one
        analysis.boxes.remove(missed)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    (text,) = _text(out)
    assert PATIENT not in text and "metformina" in text
    covered = [b for b in analysis.boxes if "propagated" in b.sources]
    assert covered and all(b.redact and b.label == "PERSON" for b in covered)
    assert _dark(out, 0, covered[0].rect) < 60


def test_text_outside_the_page_does_not_survive(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        p.text(100, 100, "Referto", size=12)
        p.text(100, 900, f"C.F. {FAKE_CF}", size=12)  # below the page

    src = draw_pdf(tmp_path / "offpage.pdf", draw)
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    assert all(FAKE_CF not in t for t in _text(out))
    assert FAKE_CF.encode() not in out.read_bytes()


def test_document_extras_are_removed(tmp_path: Path) -> None:
    src = draw_pdf(
        tmp_path / "extras.pdf", lambda p: p.text(72, 72, "Referto senza dati", size=12)
    )
    edit_pdf(
        src,
        notes=[(0, (100, 700, 120, 720), f"Nota su {PATIENT}")],
        attachments={"allegato.txt": f"CF {FAKE_CF}".encode()},
        outline=[f"Lettera - {PATIENT}"],
        metadata={"/Author": DOCTOR, "/Subject": FAKE_CF},
        xmp=f"<x:xmpmeta xmlns:x='adobe:ns:meta/'>{PATIENT}</x:xmpmeta>",
    )
    analysis = _analyze(src)  # no PII on the page: nothing to flatten
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues
    data = out.read_bytes()
    for value in (PATIENT.encode(), FAKE_CF.encode(), DOCTOR.encode()):
        assert value not in data
    reader = PdfReader(out)
    assert not reader.outline and reader.xmp_metadata is None
    assert "/Annots" not in reader.pages[0]
    assert "/Names" not in reader.root_object


def test_label_style_paints_the_tag(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out, style="label")

    assert release.report.ok, release.report.issues
    cf = next(b for b in analysis.boxes if b.label == "FISCAL_CODE")
    assert 150 < _dark(out, 0, cf.rect) < 250  # light box, darker tag letters


def test_fractional_page_size_passes_the_pixel_check(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        for i in range(30):
            p.text(60, 80 + 22 * i, f"Riga {i} del referto, {FAKE_CF}", size=10)

    src = draw_pdf(tmp_path / "a4.pdf", draw, size=(595.5, 842.1))
    analysis = _analyze(src)
    out = tmp_path / "out.pdf"

    release = redact_and_verify(src, analysis, out)

    assert release.report.ok, release.report.issues


def test_a_value_left_in_the_file_fails_the_check(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    analysis = _analyze(src)

    rep = verify(
        src, src, analysis, flat=set()
    )  # the original itself: nothing redacted

    assert not rep.ok
    assert any(FAKE_CF in issue for issue in rep.issues)


def test_page_space_to_pdf_space_follows_the_cropbox(tmp_path: Path) -> None:
    src = draw_pdf(tmp_path / "crop.pdf", size=(600, 800))
    edit_pdf(src, cropbox={0: (50, 100, 550, 700)})
    with pdfium.PdfDocument(src) as pdf:
        # CropBox in PDF space: x 50-550, y 100-700 (bottom-left origin)
        x0, _, _, y1 = pdf[0].get_cropbox()
        assert (x0, y1) == (50, 700)
        assert to_pdf(pdf[0], (0, 0, 10, 20)) == (50, 680, 60, 700)


def test_flattening_never_logs_values(tmp_path: Path, log_stream: io.StringIO) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    analysis = _analyze(src)

    redact_and_verify(src, analysis, tmp_path / "out.pdf")

    assert FAKE_CF not in log_stream.getvalue()
    assert PATIENT not in log_stream.getvalue()


def test_values_left_in_the_output_text_are_located_and_covered(tmp_path: Path) -> None:
    # a URL wrapped next to a QR code: the analysis reads the two pieces apart,
    # the output text puts them together; they are found where the check finds them
    def draw(p: Page) -> None:
        p.text(60, 100, "Referto n. RD/26/0093317 - Radiologia", size=10)
        p.text(300, 500, "fse.example.it/referti/RD/", size=9)
        p.text(300, 512, "26/0093317", size=9)
        p.text(60, 700, "Terapia con metformina", size=10)

    src = draw_pdf(tmp_path / "wrapped.pdf", draw)
    analysis = _analyze(src)
    code = next(b for b in analysis.boxes if "0093317" in b.text)
    analysis.boxes = [
        dataclasses.replace(
            code, text="RD/26/0093317", label="MEDICAL_RECORD", redact=True
        )
    ]
    out = tmp_path / "out.pdf"
    rebuild(src, analysis, out, {0}, "black")
    assert any("0093317" in t for t in _text(out))  # the wrapped piece is still there

    added = cover_found(out, analysis, {0})

    assert added >= 1
    rebuild(src, analysis, out, {0}, "black")
    assert all("0093317" not in t for t in _text(out))
    assert any("metformina" in t for t in _text(out))
