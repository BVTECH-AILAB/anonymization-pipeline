"""PDF redaction: the boxes applied to the pages, then the document-level clean-up.

Every page with at least one box is redacted by a ``Redactor`` strategy; the other pages are kept
as they are:

- ``ScanRedactor`` (scanned pages): the boxes are painted into the scan image itself, at its own
  resolution, and the page keeps only that image (a hidden OCR text layer is dropped). Pixels
  outside the boxes stay exactly the same: re-rendering a raster would resample it.
- ``FlattenRedactor`` (native pages, and scans that are not a single upright image): the page is
  replaced by its rendering with the boxes painted in. No text, image or vector object of the
  original page survives on it.

Then ``scrub`` rewrites the whole file. The result is checked by ``verify.pdf`` before anyone can
download it.
"""

import io
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageDraw, ImageFont

from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import Geometry
from anonymization_pipeline.pipeline_config.schema import Redaction
from anonymization_pipeline.render.boxes import Box, PageFallback, Strategy, prepare
from anonymization_pipeline.render.scrub import scrub
from anonymization_pipeline.render.surgical import SurgicalRedactor

Style = Literal["black", "label"]
PixelRect = tuple[int, int, int, int]
# a scan is one image covering at least this fraction of the page
SCAN_COVERAGE = 0.9

LABEL_FILL = (222, 235, 252)
LABEL_BORDER = (140, 173, 230)
LABEL_TEXT = (28, 64, 140)


@dataclass(frozen=True)
class Redacted:
    """The redacted PDF and what was applied to it."""

    pdf: bytes
    boxes: list[Box]  # as applied, after shaping and merging
    strategies: dict[int, str]  # page -> strategy, only pages with boxes


class Redactor(Protocol):
    """Applies the boxes of one page of an open document (the caller holds ``PDFIUM_LOCK``)."""

    name: Strategy

    def redact_page(
        self, pdf: Any, index: int, boxes: list[Box], scanned: bool
    ) -> None: ...


def _paint(
    image: Image.Image, boxes: list[tuple[PixelRect, str]], style: Style
) -> None:
    """Paint the boxes (pixel rectangles with their category) into the image."""
    draw = ImageDraw.Draw(image)
    black = 0 if image.mode == "L" else (0, 0, 0)
    for (x0, y0, x1, y1), category in boxes:
        rect = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        if style == "black" or image.mode == "L":
            draw.rectangle(rect, fill=black)
            continue
        draw.rectangle(rect, fill=LABEL_FILL, outline=LABEL_BORDER)
        tag = category.replace("_", " ")
        size = max(6, int((y1 - y0) * 0.7))
        font = ImageFont.load_default(size=size)
        width = draw.textlength(tag, font=font)
        if width <= x1 - x0 - 2:
            draw.text(
                ((x0 + x1) / 2, (y0 + y1) / 2),
                tag,
                fill=LABEL_TEXT,
                font=font,
                anchor="mm",
            )


class FlattenRedactor:
    """The page replaced by its rendering with the boxes painted in.

    Annotations and form fields are not rendered (they are removed anyway). Scanned pages are
    stored as JPEG, native pages losslessly.
    """

    name: Strategy = "flatten"

    def __init__(self, cfg: Redaction) -> None:
        self.cfg = cfg

    def redact_page(
        self, pdf: Any, index: int, boxes: list[Box], scanned: bool
    ) -> None:
        """Replace page ``index`` of ``pdf`` with its rendering and the boxes."""
        scale = self.cfg.flatten_dpi / 72
        page = pdf[index]
        try:
            width, height = page.get_size()
            bitmap = page.render(scale=scale, draw_annots=False, may_draw_forms=False)
            image = bitmap.to_pil().convert("RGB")
            bitmap.close()
        finally:
            page.close()
        pixels: list[tuple[PixelRect, str]] = []
        for box in boxes:
            x0, y0, x1, y1 = (round(v * scale) for v in box.rect)
            pixels.append(((x0, y0, x1, y1), box.category))
        _paint(image, pixels, self.cfg.style)
        new = pdf.new_page(width, height, index=index)
        obj = pdfium.PdfImage.new(pdf)
        if scanned:
            jpeg = io.BytesIO()
            image.save(jpeg, "JPEG", quality=self.cfg.jpeg_quality)
            jpeg.seek(0)
            obj.load_jpeg(jpeg, inline=True)
        else:
            raster = pdfium.PdfBitmap.from_pil(image)
            obj.set_bitmap(raster)
            raster.close()
        obj.set_matrix(pdfium.PdfMatrix().scale(width, height))
        new.insert_obj(obj)
        new.gen_content()
        new.close()
        pdf.del_page(index + 1)


class ScanRedactor:
    """The boxes painted into the scan image itself; the page keeps only that image.

    Only for an upright page (no rotation, MediaBox at the origin, CropBox equal to it) whose
    visible content is one straight image covering the page: anything else raises
    ``PageFallback``. JPEG scans are stored again as JPEG (4:4:4), the others losslessly.
    """

    name: Strategy = "scan"

    def __init__(self, cfg: Redaction) -> None:
        self.cfg = cfg

    def _scan_image(self, page: Any) -> Any:
        if page.get_rotation() or tuple(page.get_mediabox()[:2]) != (0, 0):
            raise PageFallback("rotated or offset page")
        if tuple(page.get_cropbox()) != tuple(page.get_mediabox()):
            raise PageFallback("cropped page")
        width, height = page.get_size()
        images = []
        for obj in page.get_objects(max_depth=1):
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                images.append(obj)
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT and (
                pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw)
                == pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE
            ):
                continue  # hidden OCR layer: dropped with the page
            else:
                raise PageFallback("visible content besides the scan")
        if len(images) != 1:
            raise PageFallback("not a single image")
        (scan,) = images
        a, b, c, d, _, _ = scan.get_matrix().get()
        x0, y0, x1, y1 = scan.get_bounds()
        if b or c or (x1 - x0) * (y1 - y0) < SCAN_COVERAGE * width * height:
            raise PageFallback("skewed or small image")
        return scan

    def redact_page(
        self, pdf: Any, index: int, boxes: list[Box], scanned: bool
    ) -> None:
        """Paint the boxes into the scan image of page ``index`` and keep only the image."""
        del scanned  # the image keeps its own format, JPEG or lossless
        page = pdf[index]
        try:
            geo = Geometry(page)
            width, height = page.get_size()
            scan = self._scan_image(page)
            a, _, _, d, e, f = scan.get_matrix().get()
            bitmap = scan.get_bitmap(render=False)
            image = bitmap.to_pil()
            bitmap.close()
            jpeg = "DCTDecode" in scan.get_filters()
            # page space while the page is open: Geometry reads the page handle
            spans = [(geo.to_page(box.rect), box.category) for box in boxes]
        finally:
            page.close()
        image = image.convert("L" if image.mode in ("1", "L") else "RGB")
        pixels: list[tuple[PixelRect, str]] = []
        for (left, bottom, right, top), category in spans:
            us = [(left - e) / a, (right - e) / a]
            vs = [(bottom - f) / d, (top - f) / d]
            rect = (
                int(min(us) * image.width),
                int((1 - max(vs)) * image.height),
                int(max(us) * image.width) + 1,
                int((1 - min(vs)) * image.height) + 1,
            )
            pixels.append((rect, category))
        _paint(image, pixels, self.cfg.style)
        new = pdf.new_page(width, height, index=index)
        obj = pdfium.PdfImage.new(pdf)
        if jpeg:
            data = io.BytesIO()
            image.save(data, "JPEG", quality=self.cfg.jpeg_quality, subsampling=0)
            data.seek(0)
            obj.load_jpeg(data, inline=True)
        else:
            raster = pdfium.PdfBitmap.from_pil(image)
            obj.set_bitmap(raster)
            raster.close()
        obj.set_matrix(pdfium.PdfMatrix(a, 0, 0, d, e, f))
        new.insert_obj(obj)
        new.gen_content()
        new.close()
        pdf.del_page(index + 1)


def _restore_page(pdf: Any, original: Any, index: int) -> None:
    """Put page ``index`` back as it is in the original, after a strategy gave up on it."""
    pdf.del_page(index)
    pdf.import_pages(original, [index], index)


def redact(
    src: bytes,
    boxes: list[Box],
    cfg: Redaction,
    scanned: set[int] | None = None,
    flatten_pages: set[int] | None = None,
) -> Redacted:
    """Apply ``boxes`` to the PDF ``src`` and clean the document.

    Each page with boxes tries its strategies in order: scans ``scan`` then ``flatten``, native
    pages ``surgical`` (when ``cfg.strategy`` is surgical) then ``flatten``. A strategy that gives
    up leaves the page as in the original for the next one.

    Args:
        src: Original PDF.
        boxes: Boxes of the whole document (display space), before shaping.
        cfg: ``redaction`` section of the configuration.
        scanned: Pages that are scans.
        flatten_pages: Pages flattened whatever the strategy (a surgical result the verification
            refused).
    """
    scanned = scanned or set()
    flatten_pages = flatten_pages or set()
    flatten: Redactor = FlattenRedactor(cfg)
    scan: Redactor = ScanRedactor(cfg)
    surgical: Redactor = SurgicalRedactor(cfg)
    strategies: dict[int, str] = {}
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(src)
        original = pdfium.PdfDocument(src)
        try:
            sizes = {i: pdf.get_page_size(i) for i in range(len(pdf))}
            applied = prepare(boxes, sizes)
            for index in sorted({b.page for b in applied}):
                page_boxes = [b for b in applied if b.page == index]
                if index in flatten_pages:
                    order = [flatten]
                elif index in scanned:
                    order = [scan, flatten]
                elif cfg.strategy == "surgical":
                    order = [surgical, flatten]
                else:
                    order = [flatten]
                for redactor in order:
                    try:
                        redactor.redact_page(pdf, index, page_boxes, index in scanned)
                    except PageFallback:
                        _restore_page(pdf, original, index)
                        continue
                    strategies[index] = redactor.name
                    break
            out = io.BytesIO()
            pdf.save(out, flags=pdfium_c.FPDF_NO_INCREMENTAL)
        finally:
            original.close()
            pdf.close()
    return Redacted(scrub(out.getvalue()), applied, strategies)
