"""Native PDF rendering of a resolved spec (reportlab), in the style of Italian health documents.

The output is deterministic (``invariant``): the same spec gives the same bytes. Every
string is drawn as real text, never as an image, so every page is a native PDF page.
"""

import io
import math
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from anonval import spec as s

PAGE_W, PAGE_H = A4
DARK = colors.HexColor("#222222")
GREY = colors.HexColor("#555555")
LIGHT_GREY = colors.HexColor("#F2F2F2")
GRID = colors.HexColor("#9A9A9A")
FLAG = colors.HexColor("#B00020")
INK = colors.HexColor("#1B2A6B")
STAMP_INK = colors.HexColor("#4B3B8F")
DIGITAL_NOTE = "Documento firmato digitalmente ai sensi del D.Lgs. 82/2005 e s.m.i."

_DEJAVU = "DejaVuSans"
pdfmetrics.registerFont(
    TTFont(
        _DEJAVU,
        str(Path(__file__).parent / "fonts" / "DejaVuSans.ttf"),
    )
)
pdfmetrics.registerFontFamily(
    _DEJAVU,
    normal=_DEJAVU,
    bold="Helvetica-Bold",
    italic="Helvetica-Oblique",
    boldItalic="Helvetica-BoldOblique",
)
FONTS = {
    "helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"),
    "times": ("Times-Roman", "Times-Bold", "Times-Italic"),
    "courier": ("Courier", "Courier-Bold", "Courier-Oblique"),
    "dejavu": (_DEJAVU, "Helvetica-Bold", "Helvetica-Oblique"),
}
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def markup(text: str) -> str:
    """Paragraph markup of plain text: XML escaped, ``**bold**``, line breaks."""
    return _BOLD.sub(r"<b>\1</b>", escape(text)).replace("\n", "<br/>")


def _tint(color: colors.Color, amount: float) -> colors.Color:
    return colors.Color(
        color.red + (1 - color.red) * amount,
        color.green + (1 - color.green) * amount,
        color.blue + (1 - color.blue) * amount,
    )


@dataclass
class Theme:
    regular: str
    bold: str
    italic: str
    size: float
    color: colors.Color
    tint: colors.Color
    margin: float

    @classmethod
    def of(cls, style: s.Style) -> "Theme":
        regular, bold, italic = FONTS[style.font]
        color = colors.HexColor(style.color)
        return cls(
            regular,
            bold,
            italic,
            style.font_size,
            color,
            _tint(color, 0.88),
            style.margins_mm * mm,
        )

    def para(self, size: float | None = None, **kwargs: Any) -> ParagraphStyle:
        size = size or self.size
        options: dict[str, Any] = {
            "fontName": self.regular,
            "fontSize": size,
            "leading": size * 1.25,
            "textColor": DARK,
        }
        options.update(kwargs)
        return ParagraphStyle("p", **options)


# --- flowables ---------------------------------------------------------------------------------


class CharBoxesFlowable(Flowable):
    """Label followed by one box per character (forms: codice fiscale, health card)."""

    def __init__(self, label: str, value: str, theme: Theme) -> None:
        super().__init__()
        self.label, self.value, self.theme = label, value, theme

    def wrap(
        self,
        availWidth: float,  # noqa: N803 (reportlab API)
        availHeight: float,  # noqa: ARG002, N803
    ) -> tuple[float, float]:
        self.width = availWidth
        return availWidth, 8 * mm

    def draw(self) -> None:
        c, t = self.canv, self.theme
        label_w = min(
            pdfmetrics.stringWidth(self.label, t.bold, t.size) + 3 * mm,
            self.width * 0.4,
        )
        c.setFont(t.bold, t.size)
        c.setFillColor(DARK)
        c.drawString(0, 2.2 * mm, self.label)
        box = min(5.2 * mm, (self.width - label_w) / max(len(self.value), 1))
        c.setStrokeColor(GRID)
        c.setLineWidth(0.6)
        c.setFont(t.regular, min(t.size + 1, box * 0.75))
        for i, char in enumerate(self.value):
            x = label_w + i * box
            c.rect(x, 0.5 * mm, box, 6.5 * mm, stroke=1, fill=0)
            c.drawCentredString(x + box / 2, 2.2 * mm, char)


class StampFlowable(Flowable):
    """Rotated rubber stamp: double border and centred lines, in stamp ink."""

    def __init__(
        self, lines: list[str], angle: float, align: str, theme: Theme
    ) -> None:
        super().__init__()
        self.lines, self.angle, self.align, self.theme = lines, angle, align, theme
        self.size = theme.size - 0.5
        self.box_w = (
            max(pdfmetrics.stringWidth(line, theme.bold, self.size) for line in lines)
            + 8 * mm
        )
        self.box_h = len(lines) * self.size * 1.3 + 5 * mm
        rad = math.radians(abs(angle))
        self.out_w = self.box_w * math.cos(rad) + self.box_h * math.sin(rad)
        self.out_h = self.box_w * math.sin(rad) + self.box_h * math.cos(rad)

    def wrap(
        self,
        availWidth: float,  # noqa: N803 (reportlab API)
        availHeight: float,  # noqa: ARG002, N803
    ) -> tuple[float, float]:
        self.width = availWidth
        return availWidth, self.out_h + 2 * mm

    def draw(self) -> None:
        c = self.canv
        if self.align == "right":
            cx = self.width - self.out_w / 2
        elif self.align == "center":
            cx = self.width / 2
        else:
            cx = self.out_w / 2
        c.saveState()
        c.translate(cx, self.out_h / 2 + 1 * mm)
        c.rotate(self.angle)
        c.setStrokeColor(STAMP_INK)
        c.setFillColor(STAMP_INK)
        c.setLineWidth(1.2)
        c.roundRect(
            -self.box_w / 2,
            -self.box_h / 2,
            self.box_w,
            self.box_h,
            3,
            stroke=1,
            fill=0,
        )
        c.setLineWidth(0.5)
        c.roundRect(
            -self.box_w / 2 + 1.2 * mm,
            -self.box_h / 2 + 1.2 * mm,
            self.box_w - 2.4 * mm,
            self.box_h - 2.4 * mm,
            2,
            stroke=1,
            fill=0,
        )
        leading = self.size * 1.3
        top = (len(self.lines) - 1) * leading / 2
        for i, line in enumerate(self.lines):
            font = self.theme.bold if i == 0 else self.theme.regular
            c.setFont(font, self.size)
            c.drawCentredString(0, top - i * leading - self.size * 0.35, line)
        c.restoreState()


class ScribbleFlowable(Flowable):
    """Handwritten signature: a seeded pen stroke, no text."""

    def __init__(self, seed: str, width: float) -> None:
        super().__init__()
        self.rng = random.Random(seed)
        self.span = width

    def wrap(
        self,
        availWidth: float,  # noqa: N803 (reportlab API)
        availHeight: float,  # noqa: ARG002, N803
    ) -> tuple[float, float]:
        self.width = availWidth
        return availWidth, 11 * mm

    def draw(self) -> None:
        c, rng = self.canv, self.rng
        x0 = (self.width - self.span) / 2
        c.setStrokeColor(INK)
        c.setLineWidth(0.9)
        path = c.beginPath()
        path.moveTo(x0, 4 * mm)
        x = x0
        while x < x0 + self.span:
            step = rng.uniform(4, 9) * mm
            path.curveTo(
                x + step * 0.3,
                rng.uniform(6, 10) * mm,
                x + step * 0.7,
                rng.uniform(0, 3) * mm,
                x + step,
                rng.uniform(2, 7) * mm,
            )
            x += step
        c.drawPath(path, stroke=1, fill=0)


def _checkbox(checked: bool, size: float) -> Drawing:
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, strokeColor=DARK, fillColor=None, strokeWidth=0.7))
    if checked:
        d.add(Line(1.5, 1.5, size - 1.5, size - 1.5, strokeColor=DARK, strokeWidth=1))
        d.add(Line(1.5, size - 1.5, size - 1.5, 1.5, strokeColor=DARK, strokeWidth=1))
    return d


# --- blocks ------------------------------------------------------------------------------------


class _Blocks:
    def __init__(self, theme: Theme, seed: str) -> None:
        self.t = theme
        self.seed = seed
        self.count = 0
        self.handlers: dict[str, Callable[[Any, float], list[Flowable]]] = {
            "title": self.title,
            "patient_box": self.patient_box,
            "fields": self.fields,
            "section": self.section,
            "heading": self.heading,
            "paragraph": self.paragraph,
            "table": self.table,
            "barcode": self.barcode,
            "char_boxes": self.char_boxes,
            "checkboxes": self.checkboxes,
            "signature": self.signature,
            "stamp": self.stamp,
            "note": self.note,
            "footnotes": self.footnotes,
            "address": self.address,
            "page_break": lambda _b, _w: [PageBreak()],
            "spacer": lambda b, _w: [Spacer(1, b.mm * mm)],
            "columns": self.columns,
        }

    def render(self, blocks: list[Any], width: float) -> list[Flowable]:
        out: list[Flowable] = []
        for block in blocks:
            self.count += 1
            out.extend(self.handlers[block.type](block, width))
        return out

    def p(self, text: str, **kwargs: Any) -> Paragraph:
        return Paragraph(markup(text), self.t.para(**kwargs))

    def title(self, b: s.Title, _width: float) -> list[Flowable]:
        t = self.t
        out: list[Flowable] = [
            Spacer(1, 2 * mm),
            self.p(
                b.text,
                size=t.size + 5,
                fontName=t.bold,
                textColor=t.color,
                alignment=TA_CENTER,
                leading=(t.size + 5) * 1.2,
            ),
        ]
        if b.subtitle:
            out.append(self.p(b.subtitle, textColor=GREY, alignment=TA_CENTER))
        out.append(Spacer(1, 3 * mm))
        return out

    def _pairs(
        self, fields: list[tuple[str, str]], columns: int, width: float, boxed: bool
    ) -> Table:
        t = self.t
        label_style = {"size": t.size - 1, "textColor": GREY}
        value_style = {"fontName": t.bold} if boxed else {}
        rows: list[list[Any]] = []
        for i in range(0, len(fields), columns):
            row: list[Any] = []
            for label, value in fields[i : i + columns]:
                row += [
                    self.p(f"{label}:", **label_style),
                    self.p(value, **value_style),
                ]
            row += [""] * (2 * columns - len(row))
            rows.append(row)
        cell = width / columns
        longest = max(
            pdfmetrics.stringWidth(f"{label}:", t.regular, t.size - 1)
            for label, _ in fields
        )
        label_w = min(longest + 12, cell * 0.5)  # text + cell padding
        widths = [label_w, cell - label_w] * columns
        table = Table(rows, colWidths=widths)
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    def patient_box(self, b: s.PatientBox, width: float) -> list[Flowable]:
        t = self.t
        box_w = width if b.align == "full" else width * 0.58
        inner = self._pairs(b.fields, b.columns, box_w - 6, boxed=True)
        head = self.p(b.title, fontName=t.bold, size=t.size - 1, textColor=colors.white)
        table = Table([[head], [inner]], colWidths=[box_w], hAlign="RIGHT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), t.color),
                    ("BACKGROUND", (0, 1), (0, 1), t.tint),
                    ("BOX", (0, 0), (-1, -1), 0.8, t.color),
                    ("TOPPADDING", (0, 0), (0, 0), 2),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 2),
                ]
            )
        )
        return [table, Spacer(1, 3 * mm)]

    def fields(self, b: s.Fields, width: float) -> list[Flowable]:
        return [self._pairs(b.fields, b.columns, width, boxed=False), Spacer(1, 2 * mm)]

    def section(self, b: s.Section, width: float) -> list[Flowable]:
        t = self.t
        table = Table(
            [[self.p(b.text.upper(), fontName=t.bold, textColor=colors.white)]],
            colWidths=[width],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), t.color),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return [Spacer(1, 2 * mm), table, Spacer(1, 1.5 * mm)]

    def heading(self, b: s.Heading, _width: float) -> list[Flowable]:
        t = self.t
        return [
            Spacer(1, 2 * mm),
            self.p(b.text, fontName=t.bold, size=t.size + 1, textColor=t.color),
            HRFlowable(
                width="100%", thickness=0.5, color=t.color, spaceBefore=1, spaceAfter=2
            ),
        ]

    def paragraph(self, b: s.Paragraph, _width: float) -> list[Flowable]:
        align = {
            "justify": TA_JUSTIFY,
            "left": TA_LEFT,
            "center": TA_CENTER,
            "right": TA_RIGHT,
        }
        return [self.p(b.text, size=b.size, alignment=align[b.align], spaceAfter=4)]

    def table(self, b: s.Table, width: float) -> list[Flowable]:
        t = self.t
        size = b.font_size or t.size - 0.5
        header = [
            self.p(h, size=size, fontName=t.bold, textColor=colors.white)
            for h in b.header
        ]
        rows: list[list[Any]] = [header]
        for row in b.rows:
            cells: list[Any] = []
            for i, value in enumerate(row):
                if i == b.flag_column and value.strip():
                    cells.append(
                        self.p(value, size=size, fontName=t.bold, textColor=FLAG)
                    )
                else:
                    cells.append(self.p(value, size=size))
            rows.append(cells)
        weights = b.widths or [1.0] * len(b.header)
        widths = [width * w / sum(weights) for w in weights]
        commands: list[tuple[Any, ...]] = [
            ("BACKGROUND", (0, 0), (-1, 0), t.color),
            ("GRID", (0, 0), (-1, -1), 0.3, GRID),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]
        if b.zebra:
            commands += [
                ("BACKGROUND", (0, i), (-1, i), t.tint) for i in range(2, len(rows), 2)
            ]
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle(commands))
        out: list[Flowable] = []
        if b.caption:
            out.append(self.p(b.caption, size=size, fontName=t.italic, textColor=GREY))
        return [*out, table, Spacer(1, 3 * mm)]

    def barcode(self, b: s.Barcode, width: float) -> list[Flowable]:
        t = self.t
        text = f"{b.label}: {b.value}" if b.label else b.value
        caption = self.p(text, size=t.size - 1)
        if b.kind == "qr":
            widget = QrCodeWidget(b.value)
            x0, y0, x1, y1 = widget.getBounds()
            side = 20 * mm
            drawing = Drawing(
                side, side, transform=[side / (x1 - x0), 0, 0, side / (y1 - y0), 0, 0]
            )
            drawing.add(widget)
            table = Table([[drawing, caption]], colWidths=[side + 3 * mm, width * 0.45])
        else:
            code = b.value.encode("ascii", "replace").decode()
            drawing = createBarcodeDrawing(
                "Code128",
                value=code,
                barHeight=9 * mm,
                barWidth=0.8,
                humanReadable=False,
            )
            table = Table(
                [[drawing], [caption]], colWidths=[max(drawing.width, 50 * mm)]
            )
        table.hAlign = b.align.upper()
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        return [table, Spacer(1, 2 * mm)]

    def char_boxes(self, b: s.CharBoxes, _width: float) -> list[Flowable]:
        return [CharBoxesFlowable(b.label, b.value, self.t), Spacer(1, 2 * mm)]

    def checkboxes(self, b: s.Checkboxes, width: float) -> list[Flowable]:
        t = self.t
        cells: list[Any] = []
        for label, checked in b.items:
            cells += [_checkbox(checked, 3 * mm), self.p(label)]
        n = 2 * b.columns
        rows = [cells[i : i + n] for i in range(0, len(cells), n)]
        rows[-1] += [""] * (n - len(rows[-1]))
        cell = width / b.columns
        table = Table(rows, colWidths=[5 * mm, cell - 5 * mm] * b.columns)
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        out: list[Flowable] = []
        if b.label:
            out.append(self.p(b.label, fontName=t.bold))
        return [*out, table, Spacer(1, 2 * mm)]

    def signature(self, b: s.Signature, width: float) -> list[Flowable]:
        t = self.t
        right: list[Flowable] = []
        if b.role:
            right.append(self.p(b.role, fontName=t.italic, alignment=TA_CENTER))
        if not b.digital:
            right.append(ScribbleFlowable(f"{self.seed}:{self.count}", width * 0.25))
        if b.name:
            right.append(self.p(b.name, fontName=t.bold, alignment=TA_CENTER))
        right += [
            self.p(line, size=t.size - 1, alignment=TA_CENTER) for line in b.extra
        ]
        if b.stamp:
            right.append(StampFlowable(b.stamp, -7, "center", t))
        if b.digital:
            right.append(
                self.p(
                    DIGITAL_NOTE,
                    size=t.size - 2,
                    fontName=t.italic,
                    textColor=GREY,
                    alignment=TA_CENTER,
                    borderColor=GRID,
                    borderWidth=0.5,
                    borderPadding=3,
                    spaceBefore=6,
                )
            )
        left: Any = [self.p(b.place_date)] if b.place_date else ""
        table = Table([[left, right]], colWidths=[width * 0.45, width * 0.55])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
        return [Spacer(1, 4 * mm), table, Spacer(1, 3 * mm)]

    def stamp(self, b: s.Stamp, _width: float) -> list[Flowable]:
        return [StampFlowable(b.lines, b.angle, b.align, self.t)]

    def note(self, b: s.Note, width: float) -> list[Flowable]:
        t = self.t
        table = Table(
            [[self.p(b.text, size=t.size - 1, fontName=t.italic)]], colWidths=[width]
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
                    ("LINEBEFORE", (0, 0), (0, -1), 2, t.color),
                ]
            )
        )
        return [table, Spacer(1, 3 * mm)]

    def footnotes(self, b: s.Footnotes, _width: float) -> list[Flowable]:
        t = self.t
        out: list[Flowable] = [
            HRFlowable(
                width="35%", thickness=0.4, color=GREY, hAlign="LEFT", spaceBefore=4
            )
        ]
        out += [
            self.p(f"({i}) {item}", size=t.size - 2, textColor=GREY)
            for i, item in enumerate(b.items, start=1)
        ]
        return out

    def address(self, b: s.Address, width: float) -> list[Flowable]:
        text = self.p("\n".join(b.lines))
        cells = [["", text]] if b.align == "right" else [[text, ""]]
        widths = (
            [width * 0.55, width * 0.45]
            if b.align == "right"
            else [width * 0.45, width * 0.55]
        )
        return [Table(cells, colWidths=widths), Spacer(1, 4 * mm)]

    def columns(self, b: s.Columns, width: float) -> list[Flowable]:
        half = width * 0.485 - 4
        left = self.render(b.left, half)
        right = self.render(b.right, half)
        table = Table(
            [[left, "", right]], colWidths=[width * 0.485, width * 0.03, width * 0.485]
        )
        commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]
        if b.divider:
            commands.append(("LINEBEFORE", (2, 0), (2, 0), 0.4, GRID))
            commands.append(("LEFTPADDING", (2, 0), (2, 0), 4))
        table.setStyle(TableStyle(commands))
        return [table, Spacer(1, 2 * mm)]


# --- page furniture ----------------------------------------------------------------------------


def _logo(
    c: Canvas, shape: str, initials: str, x: float, y: float, size: float, t: Theme
) -> None:
    c.saveState()
    c.setFillColor(t.color)
    c.setStrokeColor(t.color)
    cx, cy = x + size / 2, y + size / 2
    if shape == "circle":
        c.circle(cx, cy, size / 2, stroke=0, fill=1)
    elif shape == "square":
        c.roundRect(x, y, size, size, size * 0.18, stroke=0, fill=1)
    elif shape == "shield":
        path = c.beginPath()
        path.moveTo(x, y + size)
        path.lineTo(x + size, y + size)
        path.lineTo(x + size, y + size * 0.45)
        path.curveTo(x + size, y + size * 0.15, cx, y, cx, y)
        path.curveTo(cx, y, x, y + size * 0.15, x, y + size * 0.45)
        path.close()
        c.drawPath(path, stroke=0, fill=1)
    elif shape == "cross":
        arm = size / 3
        c.rect(x + arm, y, arm, size, stroke=0, fill=1)
        c.rect(x, y + arm, size, arm, stroke=0, fill=1)
    else:
        c.ellipse(x, y + size * 0.15, x + size, y + size * 0.85, stroke=0, fill=1)
    c.setFillColor(colors.white)
    font_size = size * (
        0.42 if len(initials) <= 2 else 0.3 if len(initials) <= 4 else 0.22
    )
    c.setFont("Helvetica-Bold", font_size)
    c.drawCentredString(cx, cy - font_size * 0.35, initials)
    c.restoreState()


class _Furniture:
    """Letterhead (first page), running header (next pages), footer, fax line."""

    def __init__(self, spec: s.Spec, theme: Theme) -> None:
        self.spec, self.t = spec, theme
        self.fax_h = 5 * mm if spec.style.fax_line else 0
        self.letterhead_h = (24 + max(0, len(spec.letterhead.lines) - 4) * 4) * mm
        self.header_h = 9 * mm if spec.running_header else 4 * mm
        self.footer_h = (7 + 3 * len(spec.footer)) * mm

    def fax(self, c: Canvas) -> None:
        if self.spec.style.fax_line:
            c.setFont("Courier", 7)
            c.setFillColor(DARK)
            c.drawString(self.t.margin * 0.6, PAGE_H - 7 * mm, self.spec.style.fax_line)

    def first(self, c: Canvas, _doc: Any) -> None:
        t, head = self.t, self.spec.letterhead
        c.saveState()
        self.fax(c)
        top = PAGE_H - t.margin - self.fax_h
        x = t.margin
        if head.logo:
            _logo(c, head.logo_shape, head.logo, x, top - 17 * mm, 17 * mm, t)
            x += 21 * mm
        y = top - 4 * mm
        for i, line in enumerate(head.lines):
            font, size = (t.bold, 11.5) if i == 0 else (t.regular, 8.5)
            c.setFont(font, size)
            c.setFillColor(t.color if i == 0 else GREY)
            c.drawString(x, y, line)
            y -= 4.6 * mm if i == 0 else 3.9 * mm
        c.setFont(t.regular, 7.5)
        c.setFillColor(GREY)
        y = top - 3 * mm
        for line in head.right:
            c.drawRightString(PAGE_W - t.margin, y, line)
            y -= 3.4 * mm
        c.setStrokeColor(t.color)
        c.setLineWidth(1.2)
        rule = top - self.letterhead_h + 3 * mm
        c.line(t.margin, rule, PAGE_W - t.margin, rule)
        self.footer(c)
        c.restoreState()

    def later(self, c: Canvas, _doc: Any) -> None:
        t = self.t
        c.saveState()
        self.fax(c)
        top = PAGE_H - t.margin - self.fax_h
        if self.spec.running_header:
            c.setFont(t.regular, 7.5)
            c.setFillColor(GREY)
            c.drawString(t.margin, top - 4 * mm, self.spec.running_header)
            c.setStrokeColor(t.color)
            c.setLineWidth(0.6)
            c.line(t.margin, top - 6 * mm, PAGE_W - t.margin, top - 6 * mm)
        self.footer(c)
        c.restoreState()

    def footer(self, c: Canvas) -> None:
        t = self.t
        base = t.margin * 0.6
        c.setStrokeColor(GRID)
        c.setLineWidth(0.4)
        c.line(
            t.margin,
            base + self.footer_h - 3 * mm,
            PAGE_W - t.margin,
            base + self.footer_h - 3 * mm,
        )
        c.setFont(t.regular, 6.5)
        c.setFillColor(GREY)
        y = base + self.footer_h - 6.5 * mm
        for line in self.spec.footer:
            c.drawCentredString(PAGE_W / 2, y, line)
            y -= 3 * mm


def _numbered_canvas(theme: Theme) -> type[Canvas]:
    class NumberedCanvas(Canvas):
        """Defers the pages to write "Pag. X di Y" once the total is known."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._pages: list[dict[str, Any]] = []

        def showPage(self) -> None:  # noqa: N802 (reportlab API)
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(theme.regular, 7)
                self.setFillColor(GREY)
                self.drawRightString(
                    PAGE_W - theme.margin,
                    theme.margin * 0.6,
                    f"Pag. {self._pageNumber} di {total}",
                )
                super().showPage()
            super().save()

    return NumberedCanvas


def render_pdf(spec: s.Spec) -> bytes:
    """PDF bytes of a resolved spec (no markers left)."""
    theme = Theme.of(spec.style)
    furniture = _Furniture(spec, theme)
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=theme.margin,
        rightMargin=theme.margin,
        topMargin=theme.margin,
        bottomMargin=theme.margin,
        title=spec.type.replace("_", " ").capitalize(),
        author="Dataset sintetico di validazione",
        creator="anonval",
        invariant=1,
    )
    width = PAGE_W - 2 * theme.margin
    bottom = theme.margin * 0.6 + furniture.footer_h
    first_top = PAGE_H - theme.margin - furniture.fax_h - furniture.letterhead_h
    later_top = PAGE_H - theme.margin - furniture.fax_h - furniture.header_h
    doc.addPageTemplates(
        [
            PageTemplate(
                id="first",
                frames=[
                    Frame(theme.margin, bottom, width, first_top - bottom, id="f1")
                ],
                onPage=furniture.first,
                autoNextPageTemplate="later",
            ),
            PageTemplate(
                id="later",
                frames=[
                    Frame(theme.margin, bottom, width, later_top - bottom, id="f2")
                ],
                onPage=furniture.later,
            ),
        ]
    )
    story = _Blocks(theme, spec.id).render(spec.blocks, width - 12)
    doc.build(story, canvasmaker=_numbered_canvas(theme))
    return buffer.getvalue()
