import io
from collections import Counter

import pytest
from reportlab.pdfgen.canvas import Canvas

from anonymization_pipeline.layout import pdf
from anonymization_pipeline.layout.model import Graphic
from anonymization_pipeline.layout.pdf import Extraction, extract, keep_graphics
from tests.layout import LINES, X, styled_pdf
from tests.unit.test_extraction import pdf_with_text


def _extraction() -> Extraction:
    return extract(styled_pdf(), {1}, graphics=True)


def _line_glyphs(e: Extraction, y: float) -> str:
    return "".join(g.char for g in e.pages[1].glyphs if abs(g.baseline - y) < 0.5)


def test_glyphs_keep_text_position_and_style() -> None:
    e = _extraction()

    for line in LINES:
        assert _line_glyphs(e, line.y) == line.text.replace(" ", "")
    first = e.pages[1].glyphs[0]
    assert (first.char, first.x0, first.baseline) == ("R", X, 800)
    styles = {e.styles[g.style] for g in e.pages[1].glyphs}
    summary = {(s.font, s.bold, s.italic, s.size, s.color) for s in styles}
    assert summary == {
        ("liberation-serif", True, False, 16.0, (0, 0, 0)),
        ("liberation-sans", False, False, 12.0, (0, 0, 0)),
        ("liberation-mono", False, False, 10.0, (0, 0, 0)),
        ("liberation-sans", False, True, 12.0, (255, 0, 0)),
    }
    assert e.counts["font_alias"] == 4


def test_scaled_text_has_its_drawn_size() -> None:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(595, 842))
    canvas.translate(100, 400)
    canvas.scale(2, 2)
    canvas.setFont("Helvetica", 10)
    canvas.drawString(0, 0, "Grande")
    canvas.save()

    e = extract(buffer.getvalue(), {1}, graphics=False)

    assert {s.size for s in e.styles} == {20.0}
    assert e.pages[1].graphics == []


def test_invisible_text_and_missing_pages_are_skipped() -> None:
    e = extract(
        pdf_with_text([("Nascosto", "invisible"), ("Visibile", "")]), {1, 5}, False
    )

    assert "".join(g.char for g in e.pages[1].glyphs) == "Visibile"
    assert set(e.pages) == {1}


def test_lines_and_rectangles_are_kept_curves_and_barcodes_dropped() -> None:
    e = _extraction()

    kinds = sorted((g.kind, g.x0, g.y0, g.x1, g.y1) for g in e.pages[1].graphics)
    assert kinds == [("line", X, 720, 545, 720), ("rect", X, 600, 250, 680)]
    assert e.counts["graphics_dropped_curve"] == 1
    assert e.counts["graphics_dropped_barcode"] == 20
    assert e.counts["graphics_kept"] == 2


def _bar(x: float, y: float = 0, w: float = 1.5, h: float = 30) -> Graphic:
    return Graphic(
        kind="rect",
        x0=x,
        y0=y,
        x1=x + w,
        y1=y + h,
        stroke=None,
        fill=(0, 0, 0),
        width=0,
    )


def test_graphics_filter() -> None:
    counts: Counter[str] = Counter()
    table = [_bar(x * 40, w=0.5, h=15) for x in range(10)]  # separators far apart
    horizontal_code = [
        Graphic(
            kind="rect",
            x0=0,
            y0=300 + i * 3,
            x1=30,
            y1=301.5 + i * 3,
            stroke=None,
            fill=(0, 0, 0),
            width=0,
        )
        for i in range(9)
    ]
    qr = [_bar(500 + i * 2, 500, 1, 1) for i in range(30)]
    inside_picture = [_bar(700, 700, 50, 50)]
    kept = keep_graphics(
        table + horizontal_code + qr + inside_picture,
        [(690, 690, 760, 760)],
        counts,
    )

    assert kept == table
    assert counts == Counter(
        graphics_kept=10,
        graphics_dropped_barcode=9,
        graphics_dropped_small=30,
        graphics_dropped_picture=1,
    )


def test_too_many_graphics_are_all_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf, "MAX_GRAPHICS", 3)
    counts: Counter[str] = Counter()

    assert keep_graphics([_bar(x * 100) for x in range(4)], [], counts) == []
    assert counts["graphics_dropped_cap"] == 4


def test_frames_inside_a_joined_row_are_dropped_long_lines_kept() -> None:
    counts: Counter[str] = Counter()
    frames = [
        Graphic(
            kind="rect",
            x0=100 + i * 12,
            y0=696,
            x1=112 + i * 12,
            y1=712,
            stroke=(0, 0, 0),
            fill=None,
            width=0.5,
        )
        for i in range(16)
    ]
    rule = Graphic(
        kind="line",
        x0=50,
        y0=696,
        x1=545,
        y1=696,
        stroke=(0, 0, 0),
        fill=None,
        width=0.5,
    )

    kept = keep_graphics([*frames, rule], [], counts, [(94, 690, 298, 718)])

    assert kept == [rule]
    assert counts["graphics_dropped_merged"] == 16
