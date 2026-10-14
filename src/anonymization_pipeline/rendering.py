"""PDF with the layout of the original, drawn from a layout structure (a DoclingDocument).

With a layout (``layout.model``), the fields it covers are drawn word by word at their original
position, with a font of the catalog that replaces the original one, its size and color, and the
lines and rectangles of the page; a tag fills the box of the value it replaces. The other fields
are drawn inside their box, shrinking a single font until they fit; tables without the lines of
the page become a grid of cells. Pictures are a grey box; images and logos are not drawn: the
result reproduces positions, text, styles and rules, not the original file. Tags can be
highlighted.
"""

import functools
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files

import pypdfium2 as pdfium
from docling_core.types.doc import (
    BoundingBox,
    DocItem,
    DoclingDocument,
    TableItem,
    TextItem,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from anonymization_pipeline.extraction.native_pdf import IMAGE_PLACEHOLDER
from anonymization_pipeline.ingest import PDFIUM_LOCK
from anonymization_pipeline.layout import fonts
from anonymization_pipeline.layout.model import FieldLayout, Graphic, Layout, Run, Style
from anonymization_pipeline.redaction.structure import Field

FONT = "DejaVuSans"
MIN_SIZE = 4.0
MAX_SIZE = 20.0
PICTURE_SIZE = 9.0  # largest font of the picture placeholder
LEADING = 1.2  # distance between baselines, in font sizes
ASCENT, DESCENT = 0.76, 0.24  # of the font, in font sizes
MIN_SQUEEZE = 0.8  # narrowest horizontal scale before the font shrinks
PADDING = 1.5  # points between a cell border and its text
MIN_SCALE, MAX_SCALE = 0.5, 1.5  # horizontal scale of a word to its original width
HIGHLIGHT = 0.82
_SPACE = re.compile(r"(\s+)")
_DESCENDERS = re.compile(r"[gjpqyQ,;()\[\]_|]")

Marks = dict[Field, list[tuple[int, int]]]
# a word to draw: text and whether it is a tag; None is a forced line break
Word = tuple[str, bool] | None


@functools.cache
def _font() -> str:
    path = files("anonymization_pipeline.resources").joinpath("fonts/DejaVuSans.ttf")
    pdfmetrics.registerFont(TTFont(FONT, str(path)))
    return FONT


def _words(text: str, marks: list[tuple[int, int]]) -> list[Word]:
    """Words of ``text``: a tag is one word, newlines force a break."""
    out: list[Word] = []
    cursor = 0
    for start, end in [*sorted(marks), (len(text), len(text))]:
        for part in _SPACE.split(text[cursor:start]):
            if not part:
                continue
            if part.isspace():
                out += [None] * part.count("\n")
            else:
                out.append((part, False))
        if end > start:
            out.append((text[start:end], True))
        cursor = end
    return out


def _lines(
    words: list[Word], size: float, width: float
) -> list[list[tuple[str, bool]]]:
    space = pdfmetrics.stringWidth(" ", FONT, size)
    lines: list[list[tuple[str, bool]]] = [[]]
    used = 0.0
    for word in words:
        if word is None:
            lines.append([])
            used = 0.0
            continue
        w = pdfmetrics.stringWidth(word[0], FONT, size)
        if lines[-1] and used + space + w > width:
            lines.append([])
            used = 0.0
        used += (space if lines[-1] else 0.0) + w
        lines[-1].append(word)
    return lines


def _fit(
    words: list[Word], width: float, height: float, largest: float = MAX_SIZE
) -> tuple[float, list[list[tuple[str, bool]]]]:
    """The largest font size whose wrapped lines fit the box (at least ``MIN_SIZE``).

    docling boxes are tight around the glyphs: one line is as tall as the ascent, plus the descent
    when a letter goes below the baseline. Lines may be squeezed horizontally down to
    ``MIN_SQUEEZE`` before the font shrinks (the font is wider than most document fonts).
    """
    text = "".join(w[0] for w in words if w)
    extent = ASCENT + (DESCENT if _DESCENDERS.search(text) else 0.0)
    size = max(MIN_SIZE, min(largest, height / extent))
    while True:
        lines = _lines(words, size, width / MIN_SQUEEZE)
        needed = size * extent + (len(lines) - 1) * size * LEADING
        if needed <= height * 1.05 or size <= MIN_SIZE:
            return size, lines
        size = max(MIN_SIZE, size - 0.25)


def _draw_text(
    canvas: Canvas,
    text: str,
    marks: list[tuple[int, int]],
    box: tuple[float, float, float, float],
    highlight: bool,
    largest: float = MAX_SIZE,
) -> None:
    left, bottom, right, top = box
    width, height = max(right - left, 1.0), max(top - bottom, 1.0)
    words = _words(text, marks)
    if not any(words):
        return
    size, lines = _fit(words, width, height, largest)
    space = pdfmetrics.stringWidth(" ", FONT, size)
    natural = max(
        sum(pdfmetrics.stringWidth(w, FONT, size) for w, _ in line)
        + space * (len(line) - 1)
        for line in lines
    )
    squeeze = min(1.0, width / natural) if natural else 1.0  # the same for every line
    for i, line in enumerate(lines):
        x, y = left, top - ASCENT * size - i * size * LEADING
        for word, tag in line:
            w = pdfmetrics.stringWidth(word, FONT, size) * squeeze
            if tag and highlight:
                canvas.setFillGray(0.82)
                canvas.rect(
                    x,
                    y - DESCENT * size,
                    w,
                    (ASCENT + DESCENT) * size,
                    stroke=0,
                    fill=1,
                )
            canvas.setFillGray(0)
            text_object = canvas.beginText(x, y)
            text_object.setFont(FONT, size)
            text_object.setHorizScale(squeeze * 100)
            text_object.textOut(word)
            canvas.drawText(text_object)
            x += w + space * squeeze


def _box(bbox: BoundingBox, page_height: float) -> tuple[float, float, float, float]:
    b = bbox.to_bottom_left_origin(page_height)
    return b.l, min(b.b, b.t), b.r, max(b.b, b.t)


def _split(text: str, parts: int) -> list[str]:
    """``text`` in ``parts`` consecutive chunks of words (an item spread over several boxes)."""
    words = text.split(" ")
    step = -(-len(words) // parts)
    return [" ".join(words[i * step : (i + 1) * step]) for i in range(parts)]


def _on_page(item: DocItem, page_no: int) -> Iterator[tuple[int, BoundingBox]]:
    for i, prov in enumerate(item.prov):
        if prov.page_no == page_no:
            yield i, prov.bbox


def _width(parts: list[tuple[str, str]], size: float) -> float:
    return sum(pdfmetrics.stringWidth(text, name, size) for name, text in parts)


def _draw_parts(
    canvas: Canvas,
    parts: list[tuple[str, str]],
    x: float,
    baseline: float,
    size: float,
    scale: float,
) -> None:
    text_object = canvas.beginText(x, baseline)
    text_object.setHorizScale(scale * 100)
    for name, text in parts:
        text_object.setFont(name, size)
        text_object.textOut(text)
    canvas.drawText(text_object)


def _draw_run(
    canvas: Canvas, text: str, run: Run, style: Style, highlight: bool
) -> None:
    """A word at its original position and width; a tag shrinks to fit the box of its value."""
    parts = fonts.segments(text, style.font, style.bold, style.italic)
    size = style.size
    natural = _width(parts, size)
    if natural <= 0:
        return
    if run.tag:
        if run.width < natural * MIN_SQUEEZE:
            size = max(MIN_SIZE, size * run.width / (natural * MIN_SQUEEZE))
            natural = _width(parts, size)
        scale = min(1.0, run.width / natural) if run.width > 0 else 1.0
        if highlight:
            canvas.setFillGray(HIGHLIGHT)
            canvas.rect(
                run.x,
                run.baseline - DESCENT * style.size,
                max(run.width, natural * scale),
                (ASCENT + DESCENT) * style.size,
                stroke=0,
                fill=1,
            )
    else:
        scale = min(MAX_SCALE, max(MIN_SCALE, run.width / natural))
    canvas.setFillColorRGB(*(c / 255 for c in style.color))
    _draw_parts(canvas, parts, run.x, run.baseline, size, scale)


def _draw_field(
    canvas: Canvas, text: str, field: FieldLayout, layout: Layout, highlight: bool
) -> None:
    for run in field.runs:
        _draw_run(
            canvas, text[run.start : run.end], run, layout.styles[run.style], highlight
        )


def _rgb(color: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = color
    return r / 255, g / 255, b / 255


def _draw_graphics(canvas: Canvas, graphics: list[Graphic]) -> None:
    canvas.saveState()
    for g in graphics:
        if g.stroke is not None:
            canvas.setStrokeColorRGB(*_rgb(g.stroke))
            canvas.setLineWidth(g.width)
        if g.kind == "line":
            canvas.line(g.x0, g.y0, g.x1, g.y1)
        else:
            if g.fill is not None:
                canvas.setFillColorRGB(*_rgb(g.fill))
            canvas.rect(
                g.x0,
                g.y0,
                g.x1 - g.x0,
                g.y1 - g.y0,
                stroke=int(g.stroke is not None),
                fill=int(g.fill is not None),
            )
    canvas.restoreState()


@dataclass
class _Page:
    """What a page draws from the layout: fields by reference, and whether it has its own lines."""

    fields: dict[Field, FieldLayout]
    layout: Layout | None
    ruled: bool


def _draw_table(
    canvas: Canvas,
    table: TableItem,
    bbox: BoundingBox,
    page_height: float,
    marks: Marks,
    highlight: bool,
    page: _Page,
) -> None:
    left, bottom, right, top = _box(bbox, page_height)
    rows, cols = max(table.data.num_rows, 1), max(table.data.num_cols, 1)
    canvas.setStrokeGray(0.6)
    canvas.setLineWidth(0.4)
    for i, cell in enumerate(table.data.table_cells):
        styled = page.fields.get((table.self_ref, i))
        if styled is not None and page.layout is not None:
            _draw_field(canvas, cell.text, styled, page.layout, highlight)
            continue
        if cell.bbox is not None:
            cl, cb, cr, ct = _box(cell.bbox, page_height)
        else:  # no cell boxes: an even grid over the table box
            cw, ch = (right - left) / cols, (top - bottom) / rows
            cl = left + cell.start_col_offset_idx * cw
            cr = left + cell.end_col_offset_idx * cw
            ct = top - cell.start_row_offset_idx * ch
            cb = top - cell.end_row_offset_idx * ch
        if not page.ruled:  # the page has no lines of its own: a grid
            canvas.rect(cl, cb, cr - cl, ct - cb, stroke=1, fill=0)
        _draw_text(
            canvas,
            cell.text,
            marks.get((table.self_ref, i), []),
            (cl + PADDING, cb + PADDING, cr - PADDING, ct - PADDING),
            highlight,
        )


def render_pdf(
    structure: DoclingDocument,
    marks: Marks | None = None,
    highlight: bool = True,
    layout: Layout | None = None,
) -> bytes:
    """PDF of a layout structure.

    Args:
        structure: Pages (size) and items (positions, text, tables).
        marks: Positions of the tags in each text field (censored structure).
        highlight: Grey background behind the tags.
        layout: Styles and positions of the fields, lines and rectangles of the pages (it must
            match ``structure``: ``layout.censor.check``).
    """
    marks = marks or {}
    by_page: dict[int, dict[Field, FieldLayout]] = {}
    graphics: dict[int, list[Graphic]] = {}
    if layout is not None:
        for f in layout.fields:
            by_page.setdefault(f.page_no, {})[(f.ref, f.cell)] = f
        graphics = {p.page_no: p.graphics for p in layout.pages}
    font = _font()
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pageCompression=1, invariant=1)
    canvas.setTitle("documento")
    canvas.setAuthor("")
    canvas.setSubject("")
    canvas.setCreator("anonymization-pipeline")
    for page_no in sorted(structure.pages):
        size = structure.pages[page_no].size
        canvas.setPageSize((size.width, size.height))
        page = _Page(by_page.get(page_no, {}), layout, bool(graphics.get(page_no)))
        _draw_graphics(canvas, graphics.get(page_no, []))
        for picture in structure.pictures:
            for _, bbox in _on_page(picture, page_no):
                left, bottom, right, top = _box(bbox, size.height)
                canvas.setFillGray(0.93)
                canvas.setStrokeGray(0.7)
                canvas.rect(left, bottom, right - left, top - bottom, stroke=1, fill=1)
                _draw_text(
                    canvas,
                    IMAGE_PLACEHOLDER,
                    [],
                    (left + PADDING, bottom, right, top - PADDING),
                    False,
                    PICTURE_SIZE,
                )
        for table in structure.tables:
            for _, bbox in _on_page(table, page_no):
                _draw_table(canvas, table, bbox, size.height, marks, highlight, page)
        for item in structure.texts:
            styled = page.fields.get((item.self_ref, None))
            if styled is not None and layout is not None:
                _draw_field(canvas, item.text, styled, layout, highlight)
            else:
                _draw_item(canvas, item, page_no, size.height, marks, highlight)
        canvas.setFont(font, MIN_SIZE)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _draw_item(
    canvas: Canvas,
    item: TextItem,
    page_no: int,
    page_height: float,
    marks: Marks,
    highlight: bool,
) -> None:
    item_marks = marks.get((item.self_ref, None), [])
    if len(item.prov) == 1:
        chunks = [(item.text, item_marks)]
    else:  # spread over several boxes: tags are not highlighted there
        chunks = [(chunk, []) for chunk in _split(item.text, len(item.prov))]
    for i, bbox in _on_page(item, page_no):
        text, chunk_marks = chunks[i]
        _draw_text(canvas, text, chunk_marks, _box(bbox, page_height), highlight)


def attach(pdf: bytes, name: str, data: bytes) -> bytes:
    """``pdf`` with ``data`` attached as the file ``name``."""
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf)
        try:
            document.new_attachment(name).set_data(data)
            out = io.BytesIO()
            document.save(out)
        finally:
            document.close()
    return out.getvalue()


def attachment(pdf: bytes, name: str) -> bytes | None:
    """The attached file ``name`` of ``pdf``; None if it is not there.

    Raises:
        ValueError: ``pdf`` is not a readable PDF.
    """
    with PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(pdf)
        except pdfium.PdfiumError as exc:
            raise ValueError("unreadable PDF") from exc
        try:
            for i in range(document.count_attachments()):
                found = document.get_attachment(i)
                if found.get_name() == name:
                    return bytes(found.get_data())
        finally:
            document.close()
    return None


def text_of(pdf: bytes) -> list[str]:
    """Text of every page of ``pdf`` and, as a last entry, its metadata values."""
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(pdf)
        try:
            pages = []
            for page in document:
                textpage = page.get_textpage()
                try:
                    pages.append(textpage.get_text_range())
                finally:
                    textpage.close()
                    page.close()
            metadata = " ".join(str(v) for v in document.get_metadata_dict().values())
        finally:
            document.close()
    return [*pages, metadata]
