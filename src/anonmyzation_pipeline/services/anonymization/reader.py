"""Text of a PDF behind one interface: per page, its geometry and text layers.

``LiteparseReader``: native text with exact character boxes from pypdfium2;
LiteParse (PDFium) tells scanned pages apart and renders them for the OCR
server (``ocr-server/``, PP-OCRv5 behind the LiteParse OCR API). A scan that
already has a text layer is read by OCR too, so a wrong scanner layer never
hides the visible text. On an upright scan, codes printed one character per box
are read again box by box, cleaned (``cells.py``).

Boxes of the layers are in page space (unrotated), where redaction works.
OCR words of scanned pages are also returned in display space, for the
visual detectors that work on the rendering.
"""

import bisect
import dataclasses
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import httpx
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from liteparse import AnnotationRect, LiteParse, ParsedPage

from anonmyzation_pipeline.services.anonymization.cells import read_cells
from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.geometry import (
    direction_from_display,
    from_display,
    intersects,
    to_display,
    union,
)
from anonmyzation_pipeline.services.anonymization.layers import (
    Rect,
    TextLayer,
    Token,
    Word,
    char_boxes,
    merge_cells,
    ocr_layer,
    split_glued,
)
from anonmyzation_pipeline.services.anonymization.pdf_pages import (
    PageView,
    pdfium_locked,
)
from anonmyzation_pipeline.services.anonymization.pdf_text import chars

logger = logging.getLogger(__name__)

Fraction = Callable[[float], None]

# a gap wider than this many word heights starts a new line (another cell or column):
# measured on the native gold set against the lines of the PDF text layer: it cuts
# 0.2% of the gaps inside a line and keeps 1.6% of the gaps between lines
LINE_GAP = 0.8


@dataclass
class Region:
    """A table or a figure found by LiteParse.

    Attributes:
        kind: ``table`` or ``figure``.
        rect: Area of the region.
        cells: Table rows, header first, one area per cell; None for a cell
            LiteParse added to square off the grid. Empty for a figure, and for
            a table LiteParse could not split into cells.
    """

    kind: str
    rect: Rect
    cells: list[list[Rect | None]] = dataclasses.field(default_factory=list)

    def map(self, fn: Callable[[Rect], Rect]) -> "Region":
        """The same region with ``fn`` applied to every area (space conversions)."""
        return Region(
            self.kind,
            fn(self.rect),
            [[None if c is None else fn(c) for c in row] for row in self.cells],
        )


@dataclass
class PageText:
    """What a reader returns for one page.

    Attributes:
        info: Geometry, as in ``Analysis.pages``: display ``width``/``height``,
            ``scanned``, ``rotation``, page-space ``page_width``/``page_height``.
        layers: Text layers in page space.
        ocr_words: OCR words in display space (scanned pages only), for the
            visual detectors; None on native pages.
        regions: Tables and figures LiteParse found on the page, in page space.
    """

    info: dict
    layers: list[TextLayer]
    ocr_words: list[Word] | None = None
    regions: list[Region] = dataclasses.field(default_factory=list)


class TextReader(Protocol):
    """Anything that reads the text layers of a PDF."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def read(
        self, path: Path, views: list[PageView], progress: Fraction
    ) -> list[PageText]: ...


def derotate_rect(info: dict, rect: Rect) -> Rect:
    """A rectangle of the rendered (rotated) page in page space, where text and redaction live.

    Args:
        info: The page, as an ``Analysis.pages`` item.
        rect: Rectangle in display space.
    """
    return from_display(rect, info) if info.get("rotation") else rect


def derotate_word(info: dict, w: Word) -> Word:
    """An OCR word in page space; its reading direction turns with the page."""
    rotation = info.get("rotation", 0)
    if not rotation:
        return w
    return dataclasses.replace(
        w,
        rect=from_display(w.rect, info),
        direction=direction_from_display(w.direction, rotation),
    )


def polygon_direction(poly: list[list[float]], text: str) -> str:
    """Reading direction of an OCR quadrilateral: its longer side, TL -> TR first.

    The same rule LiteParse applies to the polygons of the OCR API, except for
    one or two characters: a glyph is taller than wide whatever the direction
    (a digit in a form cell), so short text reads left to right.
    """
    if len(text.strip()) < 3:
        return "ltr"
    (x0, y0), (x1, y1), (x2, y2) = poly[0], poly[1], poly[2]
    e0, e1 = (x1 - x0, y1 - y0), (x2 - x1, y2 - y1)
    len0, len1 = abs(e0[0]) + abs(e0[1]), abs(e1[0]) + abs(e1[1])
    longer, shorter = max(len0, len1), min(len0, len1)
    if longer < 1 or (shorter > 0 and longer / shorter < 1.3):
        return "ltr"
    dx, dy = e0 if len0 >= len1 else e1
    if abs(dx) >= abs(dy):
        return "ltr" if dx > 0 else "rtl"
    return "ttb" if dy > 0 else "btt"


def split_words(w: Word) -> list[Word]:
    """Split an OCR result holding several words (a whole vertical line) along its direction."""
    parts = w.text.split()
    if len(parts) <= 1:
        return [dataclasses.replace(w, text=w.text.strip())]
    chars = char_boxes(w.rect, len(w.text), w.direction)
    out = []
    pos = 0
    for part in parts:
        a = w.text.index(part, pos)
        b = a + len(part)
        pos = b
        out.append(dataclasses.replace(w, text=part, rect=union(chars[a:b]) or w.rect))
    return out


def _next_gap(a: Word | Token, b: Word | Token) -> float | None:
    """Gap from ``a`` to ``b`` when ``b`` can follow ``a`` on its line, else None.

    ``b`` follows when both read the same way, overlap across the reading axis by
    at least half of the smaller one, and start after ``a`` ends (a slight overlap
    of OCR boxes is tolerated) at most ``LINE_GAP`` sizes away.
    """
    if a.direction != b.direction:
        return None
    vertical = a.direction in ("ttb", "btt")
    axis, cross = (1, 0) if vertical else (0, 1)
    size_a = a.rect[cross + 2] - a.rect[cross]
    size_b = b.rect[cross + 2] - b.rect[cross]
    overlap = min(a.rect[cross + 2], b.rect[cross + 2]) - max(
        a.rect[cross], b.rect[cross]
    )
    if overlap < 0.5 * min(size_a, size_b):
        return None
    if a.direction in ("btt", "rtl"):
        gap = a.rect[axis] - b.rect[axis + 2]
    else:
        gap = b.rect[axis] - a.rect[axis + 2]
    size = max(size_a, 1.0)
    if gap < -0.3 * size or gap > LINE_GAP * size:
        return None
    return gap


def group_lines[W: (Word, Token)](words: list[W]) -> list[W]:
    """Words with line ids from their geometry, in reading order.

    Every word is chained to its nearest follower on the same line (see
    ``_next_gap``); each chain is a line, as in a PDF text layer. Table
    cells and columns stay separate lines (detectors never read "Nessuna" and
    "Vivente" of two cells as one name), and rows of a form that overlap by a
    few points do not steal each other's words.
    """
    pairs: list[tuple[float, int, int]] = []
    for direction in {w.direction for w in words}:
        cross = 0 if direction in ("ttb", "btt") else 1
        ids = sorted(
            (i for i, w in enumerate(words) if w.direction == direction),
            key=lambda i: words[i].rect[cross] + words[i].rect[cross + 2],
        )
        centres = [words[i].rect[cross] + words[i].rect[cross + 2] for i in ids]
        for n, i in enumerate(ids):
            a = words[i]
            # followers overlap ``a`` by half its size: their centres are close
            reach = 2 * (a.rect[cross + 2] - a.rect[cross])
            lo = bisect.bisect_left(centres, centres[n] - reach)
            hi = bisect.bisect_right(centres, centres[n] + reach)
            for j in ids[lo:hi]:
                if j != i and (gap := _next_gap(a, words[j])) is not None:
                    pairs.append((gap, i, j))
    pairs.sort()
    succ: dict[int, int] = {}
    pred: dict[int, int] = {}
    for _, i, j in pairs:
        if i in succ or j in pred:
            continue
        # never close a loop
        k = i
        while k in pred and k != j:
            k = pred[k]
        if k == j:
            continue
        succ[i], pred[j] = j, i
    chains = []
    for i in range(len(words)):
        if i in pred:
            continue
        chain = [i]
        while chain[-1] in succ:
            chain.append(succ[chain[-1]])
        chains.append(chain)

    horizontal = [c for c in chains if words[c[0]].direction not in ("ttb", "btt")]
    vertical = [c for c in chains if words[c[0]].direction in ("ttb", "btt")]

    def box(chain: list[int]) -> Rect:
        rs = [words[i].rect for i in chain]
        return (
            min(r[0] for r in rs),
            min(r[1] for r in rs),
            max(r[2] for r in rs),
            max(r[3] for r in rs),
        )

    # reading order: lines whose centre falls inside the first line of a row
    # share the row, then left to right
    rows: list[list[tuple[Rect, list[int]]]] = []
    for b, chain in sorted(
        ((box(c), c) for c in horizontal), key=lambda bc: bc[0][1] + bc[0][3]
    ):
        centre = (b[1] + b[3]) / 2
        if rows and rows[-1][0][0][1] <= centre <= rows[-1][0][0][3]:
            rows[-1].append((b, chain))
        else:
            rows.append([(b, chain)])
    chains = [c for row in rows for _, c in sorted(row, key=lambda bc: bc[0][0])]
    chains += sorted(vertical, key=lambda c: (box(c)[0], box(c)[1]))
    return [
        dataclasses.replace(words[i], line=line)
        for line, chain in enumerate(chains)
        for i in chain
    ]


def _char_direction(angle: float) -> str:
    """Reading direction (page space, y down) of a character rotated by ``angle`` radians."""
    turns = round(math.degrees(angle) / 90) % 4
    return ("ltr", "btt", "rtl", "ttb")[turns]


def pdfium_tokens(page: pdfium.PdfPage, info: dict | None = None) -> list[Token]:
    """Tokens of the native text layer of a page, with exact character boxes (page space).

    Words end at spaces and line breaks, at a change of direction, and at a
    gap wider than 0.12 of the font size along the reading direction (words
    written without space characters, as scanner OCR layers do). Words and
    lines are built on the page as displayed (``info``: its geometry), as a
    reader sees it, so a table on a ``/Rotate`` page reads row by row; the
    boxes are then turned back into page space.
    """
    info = info or {"rotation": 0, "width": 0.0, "height": 0.0}
    rotation = info.get("rotation", 0) % 360
    back = (360 - rotation) % 360  # page space -> display
    words: list[list] = []
    prev = None
    for c in chars(page):
        if c.generated or not c.text or c.text.isspace():
            prev = None
            continue
        direction = direction_from_display(_char_direction(c.angle), back)
        rect = to_display(c.rect, info) if rotation else c.rect
        if prev is not None and prev.direction == direction:
            a, b = prev.rect, rect
            gap = {
                "ltr": b[0] - a[2],
                "rtl": a[0] - b[2],
                "ttb": b[1] - a[3],
                "btt": a[1] - b[3],
            }[direction]
            if gap <= 0.12 * max(c.size, 1.0) and gap > -max(c.size, 1.0):
                words[-1].append((c, rect))
                prev = _Placed(rect, direction)
                continue
        words.append([(c, rect)])
        prev = _Placed(rect, direction)
    tokens = [
        Token(
            "".join(c.text for c, _ in w),
            union(r for _, r in w) or w[0][1],
            [r for _, r in w],
            0,
            "native",
            direction=direction_from_display(_char_direction(w[0][0].angle), back),
        )
        for w in words
    ]
    shown = merge_cells(split_glued(group_lines(tokens)))
    return _to_page_space(info, shown) if rotation else shown


@pdfium_locked
def flattened_tokens(path: Path, views: list[PageView]) -> dict[int, list[Token]]:
    """Exact native tokens of every page, form-field values and annotations included.

    A separate copy of the document is flattened page by page, so what a viewer
    shows in a form field or a note becomes page text; hidden annotations stay
    out. The pages of ``views`` are left untouched.
    """
    pdf = pdfium.PdfDocument(path)
    out: dict[int, list[Token]] = {}
    try:
        pdf.init_forms()
        for view in views:
            page = pdf[view.number]
            flat = pdfium_c.FPDFPage_Flatten(page.raw, pdfium_c.FLAT_NORMALDISPLAY)
            if flat == pdfium_c.FLATTEN_SUCCESS:  # reload: the content changed
                page.close()
                page = pdf[view.number]
            out[view.number] = pdfium_tokens(page, view.info)
            page.close()
    finally:
        pdf.close()
    return out


@dataclass
class _Placed:
    rect: Rect
    direction: str


def server_words(results: list[dict], scale: float) -> list[Word]:
    """Words of an OCR API response (pixels) in PDF points, display space."""
    out: list[Word] = []
    for r in results:
        text = str(r.get("text", "")).strip()
        bbox = r.get("bbox")
        if not text or not bbox or len(bbox) != 4:
            continue
        poly = r.get("polygon")
        direction = polygon_direction(poly, text) if poly and len(poly) == 4 else "ltr"
        rect = (bbox[0] * scale, bbox[1] * scale, bbox[2] * scale, bbox[3] * scale)
        w = Word(text, rect, float(r.get("confidence", 0.0)), "ocr", 0, direction)
        out += [x for x in split_words(w) if x.text]
    return group_lines(out)


def _to_page_space(info: dict, tokens: list[Token]) -> list[Token]:
    if not info.get("rotation"):
        return tokens
    return [
        dataclasses.replace(
            t,
            rect=derotate_rect(info, t.rect),
            chars=[derotate_rect(info, c) for c in t.chars],
        )
        for t in tokens
    ]


def read_upside_down(words: list[Word]) -> bool:
    """True when most of the text was read right to left: the page was upside down."""
    turned = sum(len(w.text) for w in words if w.direction == "rtl")
    return turned > sum(len(w.text) for w in words) / 2


def is_scanned_page(parsed: ParsedPage) -> bool:
    """A scan for LiteParse: a full-page raster, or images over half of the page."""
    c = parsed.complexity
    return c is not None and (c.full_page_image or c.image_coverage > 0.5)


# LiteParse block kinds the Markdown export lays out on their own
REGION_KINDS = {"table": "table", "grid_fallback": "table", "figure": "figure"}


def _rect(a: AnnotationRect) -> Rect:
    return (a.x, a.y, a.x + a.width, a.y + a.height)


def page_regions(parsed: ParsedPage) -> list[Region]:
    """Tables and figures of a LiteParse page, display space, in reading order."""
    out = []
    for b in parsed.blocks or []:
        if b.kind not in REGION_KINDS or b.bbox is None:
            continue
        rows = ([b.header] if b.header else []) + (b.rows or [])
        cells = [
            [None if c.bbox is None else _rect(c.bbox) for c in row] for row in rows
        ]
        out.append(Region(REGION_KINDS[b.kind], _rect(b.bbox), cells))
    return out


class LiteparseReader:
    """Exact native text (pypdfium2) and OCR through the LiteParse OCR API.

    LiteParse parses without OCR, to tell scanned pages apart and render them:
    with OCR on, it replaces a hidden text layer with what OCR sees on the scan,
    and the hidden values would never reach the detectors (a leak). Scanned
    pages are read whole by the OCR server; on native pages only the grayscale
    clinical pictures are.

    Args:
        server_url: Base URL of the OCR server (``http://paddle-ocr:8829``); empty:
            not ready, so ``/readyz`` stays 503 and no document is read without OCR.
        dpi: Rendering resolution of the pages sent to OCR.
        language: Language sent to the OCR server.
        timeout: Seconds for one OCR request made here (health, scanned pages).
        parser: Injected parser without OCR (tests).
        client: Injected HTTP client (tests).
    """

    def __init__(
        self,
        server_url: str,
        dpi: int = 300,
        language: str = "it",
        timeout: float = 120.0,
        parser: Any = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.dpi = dpi
        self.language = language
        self._client = client or httpx.Client(timeout=timeout)
        self._parser = parser
        self._ready = False

    @property
    def ready(self) -> bool:
        """True once the OCR server has answered its health check."""
        return self._ready

    def load(self) -> None:
        """Check the OCR server and build the parser.

        Raises:
            RuntimeError: No OCR server is configured, or it is not healthy.
        """
        if not self.server_url:
            raise RuntimeError("no OCR server configured")
        r = self._client.get(f"{self.server_url}/health")
        if r.status_code != 200:
            raise RuntimeError(f"OCR server not ready: status={r.status_code}")
        if self._parser is None:
            # page complexity (scan detection), screenshots, table and figure
            # regions (Markdown export): its text is not used
            self._parser = LiteParse(
                ocr_enabled=False,
                dpi=float(self.dpi),
                include_complexity=True,
                extract_blocks=True,
                max_pages=1000,
                quiet=True,
            )
        self._ready = True
        logger.info("LiteParse reader ready: dpi=%d", self.dpi)

    def _screenshot(self, path: Path, pno: int) -> bytes:
        """PNG of a page as displayed, at the reader's resolution."""
        return self._parser.screenshot(str(path), page_numbers=[pno + 1])[0].image_bytes

    def _server_words(self, png: bytes) -> list[Word]:
        """Every word of a page image, read by the OCR server."""
        r = self._client.post(
            f"{self.server_url}/ocr",
            files={"file": ("page.png", png, "image/png")},
            data={"language": self.language},
            headers={"X-Render-DPI": str(self.dpi)},
        )
        r.raise_for_status()
        return server_words(r.json().get("results", []), 72 / self.dpi)

    def _ocr_page(self, path: Path, pno: int) -> list[Word]:
        """Every word of a page, read by the OCR server."""
        return self._server_words(self._screenshot(path, pno))

    def _scan_page(self, path: Path, pno: int) -> list[Word]:
        """Every word of a scanned page; on an upright page, rows of character
        boxes are read again cleaned (``read_cells``)."""
        png = self._screenshot(path, pno)
        words = self._server_words(png)
        if read_upside_down(words):
            return words
        bgr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            return words
        img = cv2.cvtColor(np.asarray(bgr), cv2.COLOR_BGR2RGB)
        return read_cells(img, self.dpi / 72, words, self._recognize)

    def _recognize(self, img: np.ndarray) -> tuple[str, float]:
        """Text of an image holding one word, recognition only (OCR server)."""
        ok, png = cv2.imencode(".png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        if not ok:
            return "", 0.0
        r = self._client.post(
            f"{self.server_url}/recognize",
            files={"file": ("word.png", png.tobytes(), "image/png")},
        )
        r.raise_for_status()
        body = r.json()
        return str(body.get("text", "")), float(body.get("confidence", 0.0))

    def _grayscale_picture_words(
        self, path: Path, views: list[PageView], scanned: list[bool]
    ) -> dict[int, list[Word]]:
        """Text burned into the grayscale clinical pictures of native pages (X-ray, CT).

        Only those pictures are read: codes, signatures, stamps and colour photos
        are found and redacted whole by the visual detectors. The page is read
        by the OCR server and only the words inside the pictures are kept.
        """
        out: dict[int, list[Word]] = {}
        for view in views:
            if scanned[view.number]:
                continue
            inside = [r for r, colour in visual.pictures(view) if not colour]
            if not inside:
                continue
            info = view.info
            words = self._ocr_page(path, view.number)
            out[view.number] = [
                w
                for w in words
                if any(intersects(derotate_rect(info, w.rect), r) for r in inside)
            ]
        return out

    def read(
        self, path: Path, views: list[PageView], progress: Fraction
    ) -> list[PageText]:
        """Text layers of every page of the PDF at ``path`` (``views``: its pages).

        Raises:
            RuntimeError: The reader is not loaded.
        """
        if not self._ready:
            raise RuntimeError("LiteParse reader not loaded")
        progress(0.0)
        result = self._parser.parse(str(path))
        scanned = [is_scanned_page(p) for p in result.pages]
        pictures = self._grayscale_picture_words(path, views, scanned)
        exact = flattened_tokens(path, views)
        out: list[PageText] = []
        for view in views:
            pno = view.number
            info = view.info | {"scanned": scanned[pno]}
            layers: list[TextLayer] = []
            tokens = exact[pno]
            if tokens:
                layers.append(TextLayer.build(pno, "native", tokens))
            if scanned[pno]:
                words = self._scan_page(path, pno)
            else:
                words = pictures.get(pno, [])
            if words:
                layers.append(ocr_layer(pno, [derotate_word(info, w) for w in words]))
            # a scan is one picture: its content is the OCR text, not a figure
            regions = (
                []
                if scanned[pno]
                else [
                    region.map(lambda r, info=info: derotate_rect(info, r))
                    for region in page_regions(result.pages[pno])
                ]
            )
            out.append(PageText(info, layers, words if scanned[pno] else None, regions))
            progress((pno + 1) / len(views))
        return out
