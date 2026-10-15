"""Synthetic native PDFs (styles and graphics, values in character boxes) and stand-in docling
conversions of them."""

import io
from collections.abc import Callable
from dataclasses import dataclass

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

from anonymization_pipeline.extraction.native_pdf import Conversion

WIDTH, HEIGHT = 595.0, 842.0
X = 50.0


@dataclass(frozen=True)
class Line:
    text: str
    y: float
    font: str = "Helvetica"
    size: float = 12.0
    color: tuple[float, float, float] = (0, 0, 0)


LINES = [
    Line("Referto cardiologico", 800, "Times-Bold", 16),
    Line("Paziente: Mario Rossi", 770),
    Line("Codice fiscale RSSMRA85M01H501U", 750, "Courier", 10),
    Line("Diagnosi: diabete mellito tipo 2", 730, "Helvetica-Oblique", 12, (1, 0, 0)),
]


def styled_pdf(lines: list[Line] = LINES, barcode: bool = True) -> bytes:
    """One A4 page: the lines, a rule, a box, a curve and (with ``barcode``) a vector barcode."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), invariant=1)
    for line in lines:
        canvas.setFillColorRGB(*line.color)
        canvas.setFont(line.font, line.size)
        canvas.drawString(X, line.y, line.text)
    canvas.setLineWidth(0.5)
    canvas.line(X, 720, 545, 720)
    canvas.rect(X, 600, 200, 80, stroke=1, fill=0)
    canvas.bezier(300, 600, 320, 650, 360, 650, 380, 600)
    if barcode:
        for i in range(20):
            canvas.rect(300 + i * 3, 500, 1.5, 30, stroke=0, fill=1)
    canvas.save()
    return buffer.getvalue()


def _box(line: Line) -> BoundingBox:
    width = pdfmetrics.stringWidth(line.text, line.font, line.size)
    return BoundingBox(
        l=X,
        t=line.y + line.size,
        r=X + width,
        b=line.y - line.size * 0.25,
        coord_origin=CoordOrigin.BOTTOMLEFT,
    )


def structure_of(lines: list[Line] = LINES, page_no: int = 1) -> DoclingDocument:
    """docling-like document of ``styled_pdf``: one text item per line, in its box."""
    doc = DoclingDocument(name="fake")
    doc.add_page(page_no, Size(width=WIDTH, height=HEIGHT))
    for line in lines:
        doc.add_text(
            DocItemLabel.TEXT,
            line.text,
            prov=ProvenanceItem(
                page_no=page_no, bbox=_box(line), charspan=(0, len(line.text))
            ),
        )
    return doc


def convert_lines(lines: list[Line] = LINES) -> Callable[..., Conversion]:
    """Stand-in for ``native_pdf.convert`` of ``styled_pdf(lines)`` (one page)."""

    def convert(data: bytes, pages: list[int], c: object) -> Conversion:
        markdown = {0: "\n\n".join(line.text for line in lines)}
        return Conversion(markdown, structure_of(lines))

    return convert


@dataclass(frozen=True)
class Boxes:
    """A value written one character per box, after a label, on the baseline ``y``."""

    label: str
    value: str
    x: float
    y: float
    pitch: float = 12.0


CF = "RSSMRA80A01H501U"  # with a valid check character: the rules recognize it
TEAM = "80380000000000000001"
BOX_ROWS = [
    Boxes("Codice fiscale", CF, 130, 700),
    Boxes("Scala del dolore", "0123456789", 130, 650),
]


def _label_x(row: Boxes) -> float:
    """The label ends half a box before the first box: its last letter is about one pitch
    from the first character."""
    return row.x - row.pitch / 2 - pdfmetrics.stringWidth(row.label, "Helvetica", 10)


def _frame(row: Boxes, i: int) -> tuple[float, float, float, float]:
    left = row.x + i * row.pitch
    return left, row.y - 4, left + row.pitch, row.y + 12


def boxed_pdf(rows: list[Boxes] = BOX_ROWS) -> bytes:
    """One A4 page: every row with its label, a frame per box and a character centered in it."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), invariant=1)
    canvas.setLineWidth(0.5)
    for row in rows:
        canvas.setFont("Helvetica", 10)
        canvas.drawString(_label_x(row), row.y, row.label)
        for i, char in enumerate(row.value):
            left, bottom, right, top = _frame(row, i)
            canvas.rect(left, bottom, right - left, top - bottom, stroke=1, fill=0)
            canvas.drawCentredString((left + right) / 2, row.y, char)
    canvas.save()
    return buffer.getvalue()


def _char_box(row: Boxes, i: int) -> BoundingBox:
    left, _, right, _ = _frame(row, i)
    center = (left + right) / 2
    return BoundingBox(
        l=center - 3,
        t=row.y + 7.5,
        r=center + 3,
        b=row.y - 0.2,
        coord_origin=CoordOrigin.BOTTOMLEFT,
    )


def boxed_structure(
    rows: list[Boxes] = BOX_ROWS, kind: str = "text", missing: int = 0
) -> DoclingDocument:
    """docling-like document of ``boxed_pdf``: the label, then one item per box.

    Args:
        kind: ``text`` one paragraph per box, ``cells`` one table cell per box, ``merged``
            table cells holding two boxes ("8  0"), ``picture`` the paragraphs inside a
            picture docling found over the row.
        missing: Boxes at the end of every row docling lost.
    """
    doc = DoclingDocument(name="fake")
    doc.add_page(1, Size(width=WIDTH, height=HEIGHT))
    for row in rows:
        label = BoundingBox(
            l=_label_x(row),
            t=row.y + 7.5,
            r=row.x - row.pitch / 2,
            b=row.y - 0.2,
            coord_origin=CoordOrigin.BOTTOMLEFT,
        )
        doc.add_text(
            DocItemLabel.TEXT,
            row.label,
            prov=ProvenanceItem(page_no=1, bbox=label, charspan=(0, len(row.label))),
        )
        chars = list(enumerate(row.value))[: len(row.value) - missing]
        if kind in ("cells", "merged"):
            step = 2 if kind == "merged" else 1
            groups = [chars[i : i + step] for i in range(0, len(chars), step)]
            cells = [
                TableCell(
                    text="  ".join(char for _, char in group),
                    bbox=BoundingBox(
                        l=_char_box(row, group[0][0]).l,
                        t=row.y + 7.5,
                        r=_char_box(row, group[-1][0]).r,
                        b=row.y - 0.2,
                        coord_origin=CoordOrigin.BOTTOMLEFT,
                    ),
                    start_row_offset_idx=0,
                    end_row_offset_idx=1,
                    start_col_offset_idx=n,
                    end_col_offset_idx=n + 1,
                )
                for n, group in enumerate(groups)
            ]
            frame = BoundingBox(
                l=row.x,
                t=row.y + 12,
                r=row.x + len(row.value) * row.pitch,
                b=row.y - 4,
                coord_origin=CoordOrigin.BOTTOMLEFT,
            )
            doc.add_table(
                TableData(table_cells=cells, num_rows=1, num_cols=len(cells)),
                prov=ProvenanceItem(page_no=1, bbox=frame, charspan=(0, 0)),
            )
            continue
        parent = None
        if kind == "picture":
            frame = BoundingBox(
                l=row.x - 2,
                t=row.y + 14,
                r=row.x + len(row.value) * row.pitch + 2,
                b=row.y - 6,
                coord_origin=CoordOrigin.BOTTOMLEFT,
            )
            parent = doc.add_picture(
                prov=ProvenanceItem(page_no=1, bbox=frame, charspan=(0, 0))
            )
        for i, char in chars:
            doc.add_text(
                DocItemLabel.TEXT,
                char,
                parent=parent,
                prov=ProvenanceItem(page_no=1, bbox=_char_box(row, i), charspan=(0, 1)),
            )
    return doc
