"""Output safety: rotated pages, text outside the page, the Word export."""

from pathlib import Path

import numpy as np
import pymupdf
from docx import Document

from anonmyzation_pipeline.services.anonymization.layers import Word, char_boxes
from anonmyzation_pipeline.services.anonymization.pipeline import (
    Analysis,
    Box,
    Pipeline,
    from_display,
    to_display,
)
from anonmyzation_pipeline.services.anonymization.redact import redact
from anonmyzation_pipeline.services.anonymization.release import (
    FLATTEN_DPI,
    flatten_pages,
)
from anonmyzation_pipeline.services.anonymization.verify import verify, verify_docx
from tests.conftest import FAKE_CF
from tests.documents import FakeNER, FakeOCR


def _rotated_scan(tmp_path: Path) -> tuple[Path, pymupdf.Rect]:
    """A scanned page stored with /Rotate 90 and the page-space box of the CF on it."""
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    page.insert_text((100, 700), f"C.F. {FAKE_CF}", fontsize=14)
    cf = page.search_for(FAKE_CF)[0]
    pix = page.get_pixmap(dpi=100)
    scan = pymupdf.open()
    sp = scan.new_page(width=595, height=842)
    sp.insert_image(sp.rect, stream=pix.tobytes("png"))
    sp.set_rotation(90)
    path = tmp_path / "rotated_scan.pdf"
    scan.save(path)
    return path, cf


def test_ocr_boxes_of_a_rotated_scan_land_on_the_text(tmp_path: Path) -> None:
    path, cf = _rotated_scan(tmp_path)
    with pymupdf.open(path) as doc:
        shown = (
            cf * doc[0].rotation_matrix
        )  # where the OCR sees the code on the rendering
    word = Word(FAKE_CF, tuple(shown), 0.99, "ocr", 0)  # type: ignore[arg-type]
    analysis = Pipeline(FakeOCR({0: [word]}), FakeNER(())).analyze(path)

    boxes = [b for b in analysis.boxes if b.label == "FISCAL_CODE"]
    assert boxes
    assert pymupdf.Rect(boxes[0].rect).contains(cf + (1, 1, -1, -1))

    out = tmp_path / "out.pdf"
    redact(path, analysis, out)
    assert verify(path, out, analysis).ok
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=72, clip=cf * doc[0].rotation_matrix)
        dark = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        assert dark.mean() < 60  # the code is under a black box


def test_character_boxes_follow_the_reading_direction() -> None:
    down = char_boxes((0, 0, 10, 30), 3, "ttb")
    up = char_boxes((0, 0, 10, 30), 3, "btt")

    assert down[0][1] == 0 and down[-1][3] == 30
    assert up[0][3] == 30 and up[-1][1] == 0


def test_text_outside_the_visible_page_blocks_the_document(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 100), "Referto", fontsize=12)
    page.insert_text((100, 900), f"C.F. {FAKE_CF}", fontsize=12)  # below the page
    src = tmp_path / "offpage.pdf"
    doc.save(src)
    analysis = Pipeline(FakeOCR(), FakeNER(())).analyze(src)
    out = tmp_path / "out.pdf"
    redact(src, analysis, out)

    rep = verify(src, out, analysis)

    assert not rep.ok
    assert any("outside the visible page" in issue for issue in rep.issues)


def test_a_value_left_in_the_word_export_is_reported(tmp_path: Path) -> None:
    analysis = Analysis(tmp_path / "x.pdf", [{"width": 595, "height": 842}])
    analysis.boxes.append(
        Box(0, (10, 10, 90, 20), "PERSON", "Mario Rossi", 0.9, ["ner"], "native", True)
    )
    leaky, clean = tmp_path / "leaky.docx", tmp_path / "clean.docx"
    for path, text in ((leaky, "Paziente Mario Rossi"), (clean, "Paziente ███")):
        d = Document()
        d.add_paragraph(text)
        d.save(str(path))

    assert verify_docx(leaky, analysis) == [
        "value still in the Word export: 'Mario Rossi'"
    ]
    assert verify_docx(clean, analysis) == []


def test_display_and_page_space_round_trip_for_every_rotation() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    rect = pymupdf.Rect(300, 600, 400, 650)
    for rotation in (0, 90, 180, 270):
        page.set_rotation(rotation)
        info = {
            "width": page.rect.width,
            "height": page.rect.height,
            "rotation": rotation,
            "page_width": 595,
            "page_height": 842,
        }
        shown = to_display((rect.x0, rect.y0, rect.x1, rect.y1), info)
        assert pymupdf.Rect(shown) == rect * page.rotation_matrix
        assert pymupdf.Rect(from_display(shown, info)) == rect


def test_bookmarks_and_structure_tree_do_not_survive(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Referto", fontsize=12)
    doc.set_toc([[1, "Lettera dimissione - ROSSI MARIO", 1]])
    src = tmp_path / "bookmarked.pdf"
    doc.save(src)
    analysis = Pipeline(FakeOCR(), FakeNER(())).analyze(src)
    out = tmp_path / "out.pdf"

    redact(src, analysis, out)

    with pymupdf.open(out) as result:
        assert result.get_toc() == []
        assert b"ROSSI" not in b"".join(
            result.xref_object(i).encode() for i in range(1, result.xref_length())
        )
    assert verify(src, out, analysis).ok


def test_a_flattened_page_of_fractional_size_passes_the_pixel_check(
    tmp_path: Path,
) -> None:
    # A4 as scanners write it: 595.5 x 842.1 pt is not a whole number of pixels at 200 dpi
    src = tmp_path / "a4.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595.5, height=842.1)
    for i in range(30):
        page.insert_text(
            (60, 80 + 22 * i), f"Riga {i} del referto, {FAKE_CF}", fontsize=10
        )
    doc.save(src)
    analysis = Pipeline(FakeOCR(), FakeNER(())).analyze(src)
    out = tmp_path / "out.pdf"
    redact(src, analysis, out)

    flatten_pages(src, analysis, out, {0})
    rep = verify(src, out, analysis, raster_pages={0: FLATTEN_DPI})

    assert rep.ok, rep.issues
