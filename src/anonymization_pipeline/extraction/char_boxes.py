"""Values written one character per box (comb fields), joined into one text of the docling document.

docling turns a tax code in boxes into one paragraph or one table cell per character, and the
Markdown order may scatter them: no rule recognizes it and it stays in clear text. A row of at
least ``MIN_BOXES`` single characters on the same line is read again from the text layer of the
PDF (complete, in order, also where docling lost boxes) and, only if the string is an identifier
the rules recognize (tax code, health card, IBAN, card, VAT number), the boxes become one text
in the area of the row. Other rows (a pain scale 0-10) are left as they are.
"""

import statistics
from collections import Counter
from dataclasses import dataclass

from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    NodeItem,
    PictureItem,
    ProvenanceItem,
    TableItem,
    TextItem,
)

from anonymization_pipeline import metrics
from anonymization_pipeline.detectors.rules import RulesDetector
from anonymization_pipeline.layout.pdf import Box, Glyph, extract

MIN_BOXES = 8
MAX_GAP = 1.5  # between two boxes of a row, in box widths
PITCH_TOLERANCE = 0.35  # a glyph continues the row if its distance is the pitch +-35%
MIN_GAP = 0.5  # points between two characters in boxes (letters of a word touch)
CONTEXT_CHARS = 60  # of the label before the row, for the rules that need it ("P.IVA")
IDENTIFIERS = {
    "CODICE_FISCALE",
    "TESSERA_SANITARIA",
    "IBAN",
    "CARTA_CREDITO",
    "PARTITA_IVA",
}


@dataclass
class _Char:
    """A docling element holding one character: a text item or a table cell."""

    page: int
    box: Box
    item: TextItem | None = None
    table: TableItem | None = None
    cell: int | None = None

    @property
    def key(self) -> str:
        return self.table.self_ref if self.table is not None else "texts"


def _bottom_left(bbox: BoundingBox, height: float) -> Box:
    b = bbox.to_bottom_left_origin(height)
    return b.l, min(b.b, b.t), b.r, max(b.b, b.t)


def _single(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) == 1 and stripped.isalnum()


def _chars(doc: DoclingDocument) -> list[_Char]:
    out: list[_Char] = []
    for item in doc.texts:
        if _single(item.text) and len(item.prov) == 1:
            page = item.prov[0].page_no
            height = doc.pages[page].size.height
            out.append(_Char(page, _bottom_left(item.prov[0].bbox, height), item=item))
    for table in doc.tables:
        if not table.prov:
            continue
        page = table.prov[0].page_no
        height = doc.pages[page].size.height
        for i, cell in enumerate(table.data.table_cells):
            if cell.bbox is not None and _single(cell.text):
                out.append(
                    _Char(page, _bottom_left(cell.bbox, height), table=table, cell=i)
                )
    return out


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _rows(chars: list[_Char]) -> list[list[_Char]]:
    """Runs of at least ``MIN_BOXES`` elements of the same kind side by side on one line."""
    out: list[list[_Char]] = []
    groups: dict[tuple[int, str], list[_Char]] = {}
    for c in chars:
        groups.setdefault((c.page, c.key), []).append(c)
    for members in groups.values():
        members.sort(key=lambda c: _center(c.box)[1])
        bands: list[list[_Char]] = []
        for c in members:
            height = c.box[3] - c.box[1]
            if (
                bands
                and _center(c.box)[1] - _center(bands[-1][-1].box)[1] <= height / 2
            ):
                bands[-1].append(c)
            else:
                bands.append([c])
        for band in bands:
            band.sort(key=lambda c: c.box[0])
            width = statistics.median(c.box[2] - c.box[0] for c in band)
            pitch = statistics.median(
                [b.box[0] - a.box[0] for a, b in zip(band, band[1:], strict=False)]
                or [width]
            )
            run = [band[0]]
            for c in band[1:]:
                if c.box[0] - run[-1].box[2] > MAX_GAP * max(width, pitch):
                    out.append(run)
                    run = []
                run.append(c)
            out.append(run)
    return [run for run in out if len(run) >= MIN_BOXES]


def _read(run: list[_Char], glyphs: list[Glyph]) -> tuple[str, Box, float] | None:
    """The characters of the row in the text layer, their box and the pitch of the row."""
    left = min(c.box[0] for c in run)
    right = max(c.box[2] for c in run)
    bottom = min(c.box[1] for c in run)
    top = max(c.box[3] for c in run)
    band = sorted(
        (g for g in glyphs if bottom - 1 <= (g.bottom + g.top) / 2 <= top + 1),
        key=lambda g: g.x0,
    )
    middle = [g for g in band if left <= (g.x0 + g.x1) / 2 <= right]
    if len(middle) < MIN_BOXES:
        return None
    inner = [(g.x0 + g.x1) / 2 for g in middle]
    pitch = statistics.median(b - a for a, b in zip(inner, inner[1:], strict=False))
    if pitch <= 0:
        return None
    centers = [(g.x0 + g.x1) / 2 for g in band]
    first = band.index(middle[0])
    last = band.index(middle[-1])

    def gap(a: Glyph, b: Glyph) -> float:
        return max(b.x0 - a.x1, a.x0 - b.x1)

    def boxed(i: int, j: int, k: int) -> bool:
        """Glyph ``j`` continues the row after ``i``: one pitch from it, and alone in its box like
        the others (letters of a word, as in a label, touch their neighbours)."""
        if not 0 <= j < len(band):
            return False
        step = abs(centers[j] - centers[i])
        alone = gap(band[i], band[j]) > MIN_GAP and (
            not 0 <= k < len(band) or gap(band[j], band[k]) > MIN_GAP
        )
        return abs(step - pitch) <= PITCH_TOLERANCE * pitch and alone

    while boxed(last, last + 1, last + 2):
        last += 1
    while boxed(first, first - 1, first - 2):
        first -= 1
    row = band[first : last + 1]
    box = (
        min(g.x0 for g in row),
        min(g.bottom for g in row),
        max(g.x1 for g in row),
        max(g.top for g in row),
    )
    return "".join(g.char for g in row), box, pitch


def _context(doc: DoclingDocument, page: int, box: Box) -> str:
    """The text of the label nearest to the row: on its left on the same line, else above it."""
    best: tuple[float, str] | None = None
    height = doc.pages[page].size.height
    for item in doc.texts:
        if len(item.prov) != 1 or item.prov[0].page_no != page or _single(item.text):
            continue
        left, bottom, right, top = _bottom_left(item.prov[0].bbox, height)
        same_line = bottom < box[3] and top > box[1] and right <= box[0] + 1
        above = bottom >= box[1] and left < box[2] and right > box[0]
        if same_line or above:
            distance = box[0] - right if same_line else bottom - box[3] + 1000
            if best is None or distance < best[0]:
                best = (distance, item.text)
    return best[1][-CONTEXT_CHARS:] if best else ""


def _identifier(context: str, value: str) -> bool:
    proposals = RulesDetector().detect(f"{context} {value}")
    return any(p.value == value and p.category in IDENTIFIERS for p in proposals)


def _parent_picture(doc: DoclingDocument, item: TextItem) -> PictureItem | None:
    parent = item.parent.resolve(doc) if item.parent is not None else None
    return parent if isinstance(parent, PictureItem) else None


def _texts_inside(doc: DoclingDocument, node: NodeItem, removed: set[str]) -> bool:
    for ref in node.children:
        child = ref.resolve(doc)
        if child.self_ref in removed:
            continue
        if isinstance(child, TextItem) and child.text.strip():
            return True
        if _texts_inside(doc, child, removed):
            return True
    return False


def _area(box: Box) -> float:
    return max(box[2] - box[0], 0) * max(box[3] - box[1], 0)


def compact(doc: DoclingDocument, data: bytes) -> dict[int, list[Box]]:
    """Join the rows of boxes holding an identifier into one text (in place).

    Args:
        doc: docling conversion of ``data`` (IN CLEAR TEXT).
        data: The PDF, for its text layer.

    Returns:
        Per page (one-based), the area of every joined row with its frames.
    """
    runs = _rows(_chars(doc))
    if not runs:
        return {}
    pages = extract(data, {run[0].page for run in runs}, graphics=False).pages
    counts: Counter[str] = Counter()
    out: dict[int, list[Box]] = {}
    removed: list[NodeItem] = []
    pictures: dict[str, tuple[PictureItem, Box]] = {}
    for run in runs:
        page = run[0].page
        read = _read(run, pages[page].glyphs) if page in pages else None
        if read is None or not _identifier(_context(doc, page, read[1]), read[0]):
            counts["char_rows_skipped"] += 1
            continue
        value, box, pitch = read
        counts["char_rows_compacted"] += 1
        middle = (box[1] + box[3]) / 2
        reach = max(box[3] - box[1], pitch) * 0.9
        frames = (
            box[0] - pitch / 2,
            middle - reach,
            box[2] + pitch / 2,
            middle + reach,
        )
        out.setdefault(page, []).append(frames)
        height = doc.pages[page].size.height
        bbox = BoundingBox(
            l=box[0], b=box[1], r=box[2], t=box[3], coord_origin=CoordOrigin.BOTTOMLEFT
        )
        first = run[0]
        if first.table is not None:
            cells = first.table.data.table_cells
            cell = cells[first.cell or 0]
            cell.text = value
            if (
                cell.bbox is not None
                and cell.bbox.coord_origin != CoordOrigin.BOTTOMLEFT
            ):
                bbox = bbox.to_top_left_origin(height)
            cell.bbox = bbox
            for c in run[1:]:
                cells[c.cell or 0].text = ""
            continue
        items = [c.item for c in run if c.item is not None]
        picture = _parent_picture(doc, items[0])
        sibling: NodeItem = picture if picture is not None else items[0]
        origin = items[0].prov[0].bbox.coord_origin
        doc.insert_text(
            sibling=sibling,
            label=DocItemLabel.TEXT,
            text=value,
            prov=ProvenanceItem(
                page_no=page,
                bbox=bbox
                if origin == CoordOrigin.BOTTOMLEFT
                else bbox.to_top_left_origin(height),
                charspan=(0, len(value)),
            ),
            content_layer=items[0].content_layer,
            after=picture is not None,
        )
        removed += items
        if picture is not None:
            pictures[picture.self_ref] = (picture, frames)
    gone = {item.self_ref for item in removed}
    for picture, frames in pictures.values():
        prov = picture.prov[0]
        area = _area(_bottom_left(prov.bbox, doc.pages[prov.page_no].size.height))
        if not _texts_inside(doc, picture, gone) and area <= 2 * _area(frames):
            removed.append(picture)  # docling took the row of boxes for a picture
    if removed:
        doc.delete_items(node_items=removed)
    for name, n in counts.items():
        metrics.count(f"extraction.{name}", n)
    return out
