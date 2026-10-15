"""Surgical redaction with PDFium: only what lies under the boxes leaves the page.

On a native page the rest stays as it was: text stays text (selectable, searchable), vectors
stay vectors. Under each box:

- text: the characters under a box are dropped. Every other character of the page is written
  again one glyph per object at its original position, with the same font, size, matrix, colours
  and render mode: regenerating the page content rewrites every text object, and PDFium's
  serializer would lose word and character spacing (justified text would move);
- images: an image mostly under a box is removed; otherwise it is replaced by its rendering
  (masks included) with the box blanked;
- vector paths and forms entirely under a box are removed.

Then a cover (black, or the light box with the category) is drawn on each box, marked
``AnonCover`` so that the verification can render the page without it and check that nothing is
left underneath. Text inside a form, a form only partly under a box, characters PDFium cannot
write again or a page whose text changes outside the boxes raise ``PageFallback``: the page is
then flattened. The caller holds ``PDFIUM_LOCK``.
"""

import ctypes
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from anonymization_pipeline.pdf.geometry import Geometry, area, intersect
from anonymization_pipeline.pipeline_config.schema import Redaction
from anonymization_pipeline.render.boxes import Box, PageFallback, Strategy

COVER_MARK = "AnonCover"
IMAGE_REMOVED = 0.9  # an image this much under a box is removed whole
INSIDE_TOLERANCE = 0.5  # points: an object this close to a box edge is inside it
LABEL_FILL = (222, 235, 252)
LABEL_BORDER = (140, 173, 230)
LABEL_TEXT = (28, 64, 140)

PageRect = tuple[float, float, float, float]  # left, bottom, right, top (page space)


@dataclass(frozen=True)
class _Char:
    obj: int  # address of the text object
    text: str
    rect: PageRect
    origin: tuple[float, float]


def _address(raw: Any) -> int:
    return ctypes.cast(raw, ctypes.c_void_p).value or 0


def _center_inside(rect: PageRect, areas: list[PageRect]) -> bool:
    x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    return any(a[0] <= x <= a[2] and a[1] <= y <= a[3] for a in areas)


def _inside(rect: PageRect, areas: list[PageRect]) -> bool:
    t = INSIDE_TOLERANCE
    return any(
        a[0] - t <= rect[0]
        and a[1] - t <= rect[1]
        and rect[2] <= a[2] + t
        and rect[3] <= a[3] + t
        for a in areas
    )


def _bounds(raw: Any) -> PageRect:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    pdfium_c.FPDFPageObj_GetBounds(raw, left, bottom, right, top)
    return (left.value, bottom.value, right.value, top.value)


def is_cover(raw: Any) -> bool:
    """True for the covers (and their tags) drawn by this redactor."""
    for i in range(pdfium_c.FPDFPageObj_CountMarks(raw)):
        mark = pdfium_c.FPDFPageObj_GetMark(raw, i)
        length = ctypes.c_ulong()
        pdfium_c.FPDFPageObjMark_GetName(mark, None, 0, length)
        buffer = (ctypes.c_ushort * (length.value // 2 + 1))()
        pdfium_c.FPDFPageObjMark_GetName(mark, buffer, length.value, length)
        name = bytes(buffer)[: max(0, length.value - 2)].decode("utf-16-le", "ignore")
        if name == COVER_MARK:
            return True
    return False


def _chars(textpage: Any) -> list[_Char]:
    raw = textpage.raw
    out = []
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    x, y = ctypes.c_double(), ctypes.c_double()
    for i in range(pdfium_c.FPDFText_CountChars(raw)):
        if pdfium_c.FPDFText_IsGenerated(raw, i) == 1:
            continue
        code = pdfium_c.FPDFText_GetUnicode(raw, i)
        if not code:
            continue
        pdfium_c.FPDFText_GetCharBox(raw, i, left, right, bottom, top)
        pdfium_c.FPDFText_GetCharOrigin(raw, i, x, y)
        obj = _address(pdfium_c.FPDFText_GetTextObject(raw, i))
        out.append(
            _Char(
                obj,
                chr(code),
                (left.value, bottom.value, right.value, top.value),
                (x.value, y.value),
            )
        )
    return out


def _object_text(raw: Any, textpage: Any) -> str:
    n = pdfium_c.FPDFTextObj_GetText(raw, textpage.raw, None, 0)
    if n <= 2:
        return ""
    buffer = (ctypes.c_ushort * (n // 2))()
    pdfium_c.FPDFTextObj_GetText(raw, textpage.raw, buffer, n)
    return bytes(buffer).decode("utf-16-le", errors="ignore").rstrip("\x00")


def _colour(getter: Any, raw: Any) -> tuple[int, int, int, int] | None:
    rgba = [ctypes.c_uint() for _ in range(4)]
    if not getter(raw, *rgba):
        return None
    return (rgba[0].value, rgba[1].value, rgba[2].value, rgba[3].value)


def _glyphs(pdf: Any, raw: Any, kept: list[_Char]) -> list[Any]:
    """The kept characters of a text object, one new text object per glyph."""
    font = pdfium_c.FPDFTextObj_GetFont(raw)
    size = ctypes.c_float()
    matrix = pdfium_c.FS_MATRIX()
    if not font or not pdfium_c.FPDFTextObj_GetFontSize(raw, size):
        raise PageFallback("text object without a font")
    pdfium_c.FPDFPageObj_GetMatrix(raw, matrix)
    fill = _colour(pdfium_c.FPDFPageObj_GetFillColor, raw)
    stroke = _colour(pdfium_c.FPDFPageObj_GetStrokeColor, raw)
    width = ctypes.c_float()
    has_width = pdfium_c.FPDFPageObj_GetStrokeWidth(raw, width)
    mode = pdfium_c.FPDFTextObj_GetTextRenderMode(raw)
    out = []
    for char in kept:
        if char.text.isspace():
            continue
        if ord(char.text) > 0xFFFF:
            raise PageFallback("character outside the basic plane")
        glyph = pdfium_c.FPDFPageObj_CreateTextObj(pdf.raw, font, size.value)
        if not glyph or not pdfium_c.FPDFText_SetText(
            glyph, (ctypes.c_ushort * 2)(ord(char.text), 0)
        ):
            raise PageFallback("character that cannot be written again")
        pdfium_c.FPDFPageObj_SetMatrix(
            glyph,
            pdfium_c.FS_MATRIX(matrix.a, matrix.b, matrix.c, matrix.d, *char.origin),
        )
        if fill is not None:
            pdfium_c.FPDFPageObj_SetFillColor(glyph, *fill)
        if stroke is not None:
            pdfium_c.FPDFPageObj_SetStrokeColor(glyph, *stroke)
        if has_width:
            pdfium_c.FPDFPageObj_SetStrokeWidth(glyph, width.value)
        pdfium_c.FPDFTextObj_SetTextRenderMode(glyph, mode)
        out.append(glyph)
    return out


def _image_without(
    pdf: Any, image: Any, bounds: PageRect, areas: list[PageRect]
) -> Any:
    """The rendering of an image object (masks included) with the boxes blanked."""
    bitmap = image.get_bitmap(render=True, scale_to_original=True)
    try:
        pixels = np.asarray(bitmap.to_pil().convert("RGBA")).copy()
    finally:
        bitmap.close()
    h, w = pixels.shape[:2]
    x0, y0, x1, y1 = bounds
    for a in areas:
        common = intersect(bounds, a)
        if area(common) == 0:
            continue
        px0 = int((common[0] - x0) / (x1 - x0) * w)
        px1 = int(np.ceil((common[2] - x0) / (x1 - x0) * w))
        py0 = int((y1 - common[3]) / (y1 - y0) * h)
        py1 = int(np.ceil((y1 - common[1]) / (y1 - y0) * h))
        pixels[max(0, py0 - 1) : py1 + 1, max(0, px0 - 1) : px1 + 1] = (
            255,
            255,
            255,
            0,
        )
    new = pdfium.PdfImage.new(pdf)
    raster = pdfium.PdfBitmap.from_pil(Image.fromarray(pixels, "RGBA"))
    try:
        new.set_bitmap(raster)
    finally:
        raster.close()
    new.set_matrix(pdfium.PdfMatrix().scale(x1 - x0, y1 - y0).translate(x0, y0))
    return new


def _insert_at(page: Any, wrapper: Any, index: int) -> None:
    """``PdfPage.insert_obj`` at a given position (the stacking order of the page)."""
    if not pdfium_c.FPDFPage_InsertObjectAtIndex(page.raw, wrapper.raw, index):
        raise PageFallback("object not inserted")
    wrapper._detach_finalizer()  # owned by the page from now on
    wrapper.page, wrapper.pdf = page, page.pdf


class SurgicalRedactor:
    """Removes only what lies under the boxes; see the module docstring."""

    name: Strategy = "surgical"

    def __init__(self, cfg: Redaction) -> None:
        self.cfg = cfg

    def redact_page(
        self, pdf: Any, index: int, boxes: list[Box], scanned: bool
    ) -> None:
        """Redact page ``index`` of ``pdf`` in place.

        Raises:
            PageFallback: The page cannot be redacted this way (it is left untouched only when the
                fallback happens before any change: the caller starts from the original file).
        """
        del scanned  # a scan is handled by ScanRedactor
        page = pdf[index]
        try:
            geo = Geometry(page)
            areas = [geo.to_page(b.rect) for b in boxes]
            self._remove_annotations(page)
            expected = self._clear(pdf, page, areas)
            self._cover(pdf, page, boxes, areas)
            if not pdfium_c.FPDFPage_GenerateContent(page.raw):
                raise PageFallback("content not regenerated")
            self._check(page, areas, expected)
        finally:
            page.close()

    @staticmethod
    def _remove_annotations(page: Any) -> None:
        for i in reversed(range(pdfium_c.FPDFPage_GetAnnotCount(page.raw))):
            pdfium_c.FPDFPage_RemoveAnnot(page.raw, i)

    def _clear(self, pdf: Any, page: Any, areas: list[PageRect]) -> Counter[str]:
        """Remove what lies under the areas; return the characters that must stay."""
        objects = list(page.get_objects(max_depth=1))
        position = {_address(o.raw): i for i, o in enumerate(objects)}
        textpage = page.get_textpage()
        try:
            chars = _chars(textpage)
            by_object: dict[int, list[_Char]] = {}
            for char in chars:
                by_object.setdefault(char.obj, []).append(char)
            lengths = {
                addr: len(_object_text(objects[i].raw, textpage).replace(" ", ""))
                for addr, i in position.items()
                if addr in by_object
            }
        finally:
            textpage.close()
        expected = Counter(
            c.text
            for c in chars
            if not c.text.isspace() and not _center_inside(c.rect, areas)
        )
        operations: dict[
            int, tuple[list[Any], bool]
        ] = {}  # index -> (new objects, remove)
        # Every text object of the page is written again glyph by glyph, not only those under a
        # box: regenerating the content stream rewrites all of them, and PDFium's serializer
        # loses word and character spacing (justified text would move). Text inside forms is
        # kept: forms are not regenerated.
        for addr, group in by_object.items():
            hits = [_center_inside(c.rect, areas) for c in group]
            if addr not in position:
                if any(hits):
                    raise PageFallback("text inside a form under a box")
                continue
            if lengths.get(addr, 0) != sum(1 for c in group if not c.text.isspace()):
                raise PageFallback("text object not fully read by PDFium")
            raw = objects[position[addr]].raw
            kept = [c for c, hit in zip(group, hits, strict=True) if not hit]
            operations[position[addr]] = (_glyphs(pdf, raw, kept), True)
        for i, obj in enumerate(objects):
            if i in operations or obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                continue
            bounds = _bounds(obj.raw)
            touched = [a for a in areas if area(intersect(bounds, a)) > 0]
            if not touched:
                continue
            if _inside(bounds, areas):
                operations[i] = ([], True)
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_FORM:
                raise PageFallback("form partly under a box")
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                common = sum(area(intersect(bounds, a)) for a in touched)
                if common >= IMAGE_REMOVED * area(bounds):
                    operations[i] = ([], True)
                else:
                    operations[i] = ([_image_without(pdf, obj, bounds, touched)], True)
            # paths and shadings partly under a box stay: the cover hides only rules and bands
        for i in sorted(operations, reverse=True):
            new, remove = operations[i]
            for offset, item in enumerate(new):
                if isinstance(item, pdfium.PdfObject):
                    _insert_at(page, item, i + 1 + offset)
                elif not pdfium_c.FPDFPage_InsertObjectAtIndex(
                    page.raw, item, i + 1 + offset
                ):
                    raise PageFallback("glyph not inserted")
            if remove:
                page.remove_obj(objects[i])
                objects[i].close()
        return expected

    def _cover(
        self, pdf: Any, page: Any, boxes: list[Box], areas: list[PageRect]
    ) -> None:
        label = self.cfg.style == "label"
        font = (
            pdfium_c.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica") if label else None
        )
        for box, (left, bottom, right, top) in zip(boxes, areas, strict=True):
            rect = pdfium_c.FPDFPageObj_CreateNewRect(
                left, bottom, right - left, top - bottom
            )
            fill = (*LABEL_FILL, 255) if label else (0, 0, 0, 255)
            pdfium_c.FPDFPageObj_SetFillColor(rect, *fill)
            if label:
                pdfium_c.FPDFPageObj_SetStrokeColor(rect, *LABEL_BORDER, 255)
                pdfium_c.FPDFPageObj_SetStrokeWidth(rect, 0.4)
            pdfium_c.FPDFPath_SetDrawMode(
                rect, pdfium_c.FPDF_FILLMODE_ALTERNATE, int(label)
            )
            pdfium_c.FPDFPageObj_AddMark(rect, COVER_MARK.encode())
            pdfium_c.FPDFPage_InsertObject(page.raw, rect)
            if label and font and not page.get_rotation():
                self._tag(pdf, page, font, box, (left, bottom, right, top))

    @staticmethod
    def _tag(pdf: Any, page: Any, font: Any, box: Box, rect: PageRect) -> None:
        tag = box.category.replace("_", " ")
        left, bottom, right, top = rect
        size = min((top - bottom) * 0.7, 9.0)
        if size < 4:
            return
        text = pdfium_c.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
        encoded = (ctypes.c_ushort * (len(tag) + 1))(*[ord(c) for c in tag], 0)
        pdfium_c.FPDFText_SetText(text, encoded)
        width = _bounds(text)[2] - _bounds(text)[0]
        if width > right - left - 2:
            pdfium_c.FPDFPageObj_Destroy(text)
            return
        x = left + (right - left - width) / 2
        y = bottom + (top - bottom - size * 0.7) / 2
        pdfium_c.FPDFPageObj_SetMatrix(text, pdfium_c.FS_MATRIX(1, 0, 0, 1, x, y))
        pdfium_c.FPDFPageObj_SetFillColor(text, *LABEL_TEXT, 255)
        pdfium_c.FPDFPageObj_AddMark(text, COVER_MARK.encode())
        pdfium_c.FPDFPage_InsertObject(page.raw, text)

    @staticmethod
    def _check(page: Any, areas: list[PageRect], expected: Counter[str]) -> None:
        """The regenerated page: the same text outside the boxes, none under them (tags apart)."""
        covers = {
            _address(o.raw) for o in page.get_objects(max_depth=1) if is_cover(o.raw)
        }
        textpage = page.get_textpage()
        try:
            chars = [c for c in _chars(textpage) if c.obj not in covers]
        finally:
            textpage.close()
        if any(_center_inside(c.rect, areas) for c in chars if not c.text.isspace()):
            raise PageFallback("text left under a box")
        if Counter(c.text for c in chars if not c.text.isspace()) != expected:
            raise PageFallback("text changed outside the boxes")
