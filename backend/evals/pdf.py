"""Reading PDFs for the developer tools, on pypdfium2 and pypdf.

Page space as in the pipeline: points, top-left origin of the CropBox, page
not rotated (``geometry.to_display`` turns it into the rendered page).
"""

import logging
import re
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    PdfObject,
    StreamObject,
)

from src.services.anonymizer.geometry import Rect, union
from src.services.anonymizer.pdf_pages import (
    PageView,
    pdfium_locked,
)
from src.services.anonymizer.pdf_text import Char, chars, to_page

logger = logging.getLogger(__name__)

MAX_FORM_DEPTH = 8
_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)


def _breaks(c: Char) -> bool:
    return c.generated or not c.text or c.text.isspace()


def _runs(items: list[Char]) -> Iterator[list[Char]]:
    """Consecutive characters split at spaces and line breaks."""
    run: list[Char] = []
    for c in items:
        if _breaks(c):
            if run:
                yield run
            run = []
        else:
            run.append(c)
    if run:
        yield run


def _box(run: list[Char]) -> Rect:
    return union(c.rect for c in run) or run[0].rect


def words(page: pdfium.PdfPage) -> list[tuple[Rect, str]]:
    """Words of the page (split at whitespace and line breaks) with their boxes."""
    return [(_box(run), "".join(c.text for c in run)) for run in _runs(chars(page))]


@pdfium_locked
def document_words(path: Path) -> list[tuple[dict, list[tuple[Rect, str]]]]:
    """Words of every page of ``path`` as it is displayed, with the page geometry.

    Annotation and form-field appearances are merged into the page first, as a
    viewer shows them; hidden annotations stay out.

    Returns:
        Per page: its geometry (``PageView.info``) and its words in page space.
    """
    pdf = pdfium.PdfDocument(path)
    out = []
    try:
        pdf.init_forms()
        for i in range(len(pdf)):
            page = pdf[i]
            flat = pdfium_c.FPDFPage_Flatten(page.raw, pdfium_c.FLAT_NORMALDISPLAY)
            if flat == pdfium_c.FLATTEN_SUCCESS:
                page.close()
                page = pdf[i]
            out.append((PageView(page, i).info, words(page)))
            page.close()
    finally:
        pdf.close()
    return out


def _gap(a: Char, b: Char) -> bool:
    """True when ``b`` starts well after ``a`` ends on the line (a justified space)."""
    return b.rect[0] - a.rect[2] > 0.5 * (b.rect[3] - b.rect[1])


def color_spans(page: pdfium.PdfPage) -> list[tuple[Rect, str, int]]:
    """Runs of characters of one fill colour on one line: ``(box, text, rgb)``.

    Spaces drawn in the file stay inside the run of their colour; a line break,
    a colour change or a wide gap (a stretched space) ends it.
    """
    out: list[tuple[Rect, str, int]] = []
    run: list[Char] = []

    def flush() -> None:
        if run:
            out.append((_box(run), "".join(c.text for c in run), run[0].rgb))
        run.clear()

    for c in chars(page):
        if c.generated or not c.text or c.text in "\r\n":
            flush()
            continue
        if run and (c.rgb != run[0].rgb or _gap(run[-1], c)):
            flush()
        run.append(c)
    flush()
    return out


def search(page: pdfium.PdfPage, term: str) -> list[Rect]:
    """Boxes of every case-insensitive occurrence of ``term``, one per line it spans."""
    items = chars(page)
    text = "".join(c.text[:1] or "\0" for c in items)
    found = []
    for m in re.finditer(re.escape(term), text, re.IGNORECASE):
        line: list[Char] = []
        for c in items[m.start() : m.end()]:
            if c.generated and c.text in "\r\n":
                if line:
                    found.append(_box(line))
                line = []
            elif not c.generated:
                line.append(c)
        if line:
            found.append(_box(line))
    return found


def images(page: pdfium.PdfPage) -> list[tuple[Rect, int, int]]:
    """Raster images placed on the page, nested forms included: ``(box, px width, px height)``."""
    out = []

    def walk(form=None, chain: tuple = (), depth: int = 0) -> None:
        for obj in page.get_objects(max_depth=0, form=form):
            if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM and depth < MAX_FORM_DEPTH:
                walk(obj, (obj.get_matrix(), *chain), depth + 1)
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                left, bottom, right, top = obj.get_bounds()
                points = [(left, bottom), (right, bottom), (left, top), (right, top)]
                for m in chain:
                    points = [m.on_point(x, y) for x, y in points]
                xs, ys = [x for x, _ in points], [y for _, y in points]
                w, h = obj.get_px_size()
                out.append((to_page(page, (min(xs), min(ys), max(xs), max(ys))), w, h))

    walk()
    return out


def _strings(obj: PdfObject, out: list[str], depth: int = 0) -> None:
    """Every string and name inside a direct object (dictionaries and arrays walked)."""
    if depth > 64:
        return
    if isinstance(obj, DictionaryObject):
        for k, v in obj.items():
            out.append(str(k))
            if not isinstance(v, IndirectObject):
                _strings(v, out, depth + 1)
    elif isinstance(obj, ArrayObject):
        for v in obj:
            if not isinstance(v, IndirectObject):
                _strings(v, out, depth + 1)
    elif not isinstance(obj, IndirectObject):
        out.append(str(obj))


def _metadata(reader: PdfReader) -> str:
    """Document information values and the raw XMP packet (not parsed: it may be invalid XML)."""
    parts = []
    try:
        parts += [str(v) for v in (reader.metadata or {}).values() if v]
    except Exception:  # noqa: BLE001 - a broken info dictionary is still searched as an object
        logger.debug("unreadable document information")
    try:
        root = reader.trailer["/Root"].get_object()
        xmp = root.get("/Metadata") if isinstance(root, DictionaryObject) else None
        if xmp is not None:
            parts.append(str(xmp.get_object().get_data(), "utf-8", "ignore"))
    except Exception:  # noqa: BLE001 - the stream is still searched with the others
        logger.debug("unreadable XMP")
    return " ".join(parts)


@dataclass
class FileContent:
    """Everything a PDF holds that could carry a value, for the leak check.

    Attributes:
        page_text: Text of every page as pdfium extracts it.
        strings: Every string and name of every object (dictionaries, arrays,
            metadata, attachments' names), decoded.
        raw: Decoded stream data of every object, every stream found in the file
            bytes (older revisions included) inflated when possible, and the file
            bytes themselves.
        metadata: Document information dictionary and XMP packet.
    """

    page_text: str
    strings: str
    raw: bytes
    metadata: str


@pdfium_locked
def file_content(path: Path) -> FileContent:
    """Read every place of ``path`` where text can survive."""
    data = path.read_bytes()
    pdf = pdfium.PdfDocument(data)
    try:
        texts = []
        for i in range(len(pdf)):
            textpage = pdf[i].get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
    finally:
        pdf.close()
    pypdf_logger = logging.getLogger("pypdf")
    level = pypdf_logger.level
    pypdf_logger.setLevel(logging.ERROR)  # damaged or incremental files are expected
    try:
        reader = PdfReader(path)
        strings: list[str] = []
        chunks: list[bytes] = [data]
        ids = [(idnum, gen) for gen, nums in reader.xref.items() for idnum in nums]
        ids += [(idnum, 0) for idnum in reader.xref_objStm]
        for idnum, gen in ids:
            try:
                obj = reader.get_object(IndirectObject(idnum, gen, reader))
            except Exception:  # noqa: S112 - a broken object cannot hide a value from the others
                continue
            if obj is None:
                continue
            _strings(obj, strings)
            if isinstance(obj, StreamObject):
                try:
                    chunks.append(obj.get_data())
                except Exception:  # noqa: BLE001 - undecodable: keep the raw bytes
                    chunks.append(obj._data)
        meta = _metadata(reader)
    finally:
        pypdf_logger.setLevel(level)
    for m in _STREAM.finditer(data):
        try:
            chunks.append(zlib.decompressobj().decompress(m.group(1)))
        except zlib.error:
            continue
    return FileContent(" ".join(texts), " ".join(strings), b"".join(chunks), meta)
