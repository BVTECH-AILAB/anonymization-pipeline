import io
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.pdfgen import canvas

from anonymization_pipeline.layers import TextLayer, Token
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf.geometry import Geometry, union_all
from tests.conftest import FAKE_CF
from tests.documents import letter_pdf, string_width, text_pdf


def _layer(path: Path | bytes, page: int = 0) -> TextLayer:
    pdf = pdfium.PdfDocument(path)
    try:
        return native_layer(pdf[page], page)
    finally:
        pdf.close()


def test_native_layer_has_a_box_per_character(tmp_path: Path) -> None:
    layer = _layer(letter_pdf(tmp_path / "in.pdf"))

    start = layer.text.index(FAKE_CF)
    rects = layer.rects(start, start + len(FAKE_CF))
    assert len(rects) == 1
    assert all(len(t.chars) == len(t.text) for t in layer.tokens)
    x0, y0, x1, y1 = rects[0]
    # "Codice fiscale: " is drawn at x=40 on the baseline y=188 (display space)
    assert x0 == pytest.approx(40 + string_width("Codice fiscale: "), abs=0.5)
    assert y0 < 188 < y1


def test_span_over_two_lines_gives_two_rectangles(tmp_path: Path) -> None:
    layer = _layer(
        letter_pdf(tmp_path / "in.pdf", lines=["Paziente Mario", "Rossi ricoverato"])
    )

    start = layer.text.index("Mario")
    assert len(layer.rects(start, layer.text.index("Rossi") + 5)) == 2
    assert layer.text.splitlines()[-2:] == ["Paziente Mario", "Rossi ricoverato"]


def test_glued_words_are_split_on_gaps(tmp_path: Path) -> None:
    path = text_pdf(
        tmp_path / "glued.pdf",
        [(50, 100, "BOTTA"), (50 + string_width("BOTTA") + 4, 100, "DANILO")],
    )

    assert "BOTTA DANILO" in _layer(path).text


def test_words_without_spaces_are_split_by_the_gap_rule() -> None:
    # an OCR layer written character by character, with no space characters at all
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(595, 842))
    text = cv.beginText()
    text.setFont("Helvetica", 10)
    x = 50.0
    for word in ("BOTTA", "DANILO"):
        for ch in word:
            text.setTextOrigin(x, 700)
            text.textOut(ch)
            x += string_width(ch)
        x += 4
    cv.drawText(text)
    cv.save()

    assert "BOTTA DANILO" in _layer(buf.getvalue()).text


def test_invisible_white_and_off_page_text_are_in_the_layer() -> None:
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(595, 842))
    invisible = cv.beginText(40, 700)
    invisible.setTextRenderMode(3)
    invisible.setFont("Helvetica", 10)
    invisible.textOut(f"OCR {FAKE_CF}")
    cv.drawText(invisible)
    cv.setFillColorRGB(1, 1, 1)
    cv.setFont("Helvetica", 3)
    cv.drawString(300, 12, "BIANCO")
    cv.setFillColorRGB(0, 0, 0)
    cv.setFont("Helvetica", 10)
    cv.drawString(700, 500, "FUORI")
    cv.save()

    text = _layer(buf.getvalue()).text

    assert FAKE_CF in text
    assert "BIANCO" in text
    assert "FUORI" in text


def _transformed(rotate: int, crop: tuple[float, float, float, float] | None) -> bytes:
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(600, 800))
    cv.setFont("Helvetica", 20)
    cv.drawString(100, 700, "ABC")
    cv.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    page = reader.pages[0]
    if rotate:
        page.rotate(rotate)
    if crop:
        page.cropbox = RectangleObject(crop)
        page.mediabox = RectangleObject(crop)
    writer = PdfWriter()
    writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _ink(data: bytes) -> tuple[float, float, float, float]:
    pdf = pdfium.PdfDocument(data)
    try:
        grey = np.asarray(pdf[0].render(scale=2).to_pil().convert("L"))
    finally:
        pdf.close()
    ys, xs = np.nonzero(grey < 128)
    return xs.min() / 2, ys.min() / 2, (xs.max() + 1) / 2, (ys.max() + 1) / 2


@pytest.mark.parametrize(
    ("rotate", "crop"),
    [
        (0, None),
        (90, None),
        (180, None),
        (270, None),
        (0, (50, 100, 600, 800)),
        (90, (50, 100, 600, 800)),
    ],
)
def test_character_boxes_match_the_rendered_page(
    rotate: int, crop: tuple[float, float, float, float] | None
) -> None:
    data = _transformed(rotate, crop)

    layer = _layer(data)

    assert layer.text == "ABC"
    box = union_all(layer.tokens[0].chars)
    ink = _ink(data)
    # the glyphs are inside the loose boxes, which add the line gap above and below
    assert box[0] - 1 <= ink[0] and ink[2] <= box[2] + 1
    assert box[1] - 1 <= ink[1] and ink[3] <= box[3] + 1
    # and they fill them along the writing direction
    if rotate in (0, 180):
        assert ink[2] - ink[0] == pytest.approx(box[2] - box[0], abs=3)
    else:
        assert ink[3] - ink[1] == pytest.approx(box[3] - box[1], abs=3)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_display_and_page_space_round_trip(rotate: int) -> None:
    pdf = pdfium.PdfDocument(_transformed(rotate, (50, 100, 600, 800)))
    try:
        geo = Geometry(pdf[0])
        r = (10.0, 20.0, 110.0, 70.0)

        assert geo.to_display(*geo.to_page(r)) == pytest.approx(r, abs=0.01)
    finally:
        pdf.close()


def test_rects_merge_the_characters_of_a_line_and_pad() -> None:
    tokens = [
        Token("ab", (0, 0, 2, 1), [(0, 0, 1, 1), (1, 0, 2, 1)], 0, "native"),
        Token("cd", (3, 0, 5, 1), [(3, 0, 4, 1), (4, 0, 5, 1)], 0, "native"),
    ]
    layer = TextLayer.build(0, "native", tokens)

    assert layer.text == "ab cd"
    assert layer.rects(1, 4) == [(1, 0, 4, 1)]
    assert layer.rects(0, 2, pad=1) == [(-1, -1, 3, 2)]
