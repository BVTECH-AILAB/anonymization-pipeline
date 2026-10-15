"""PDF redaction: the boxes applied to the pages, then the document-level clean-up.

Every page with at least one box is redacted by a ``Redactor`` strategy; the other pages are kept
as they are. ``FlattenRedactor`` replaces the page with its rendering, the boxes painted in: no
text, image or vector object of the original page survives on it. Then ``scrub`` rewrites the
whole file. The result is checked by ``verify.pdf`` before anyone can download it.
"""

import io
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageDraw, ImageFont

from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pipeline_config.schema import Redaction
from anonymization_pipeline.render.boxes import Box, prepare
from anonymization_pipeline.render.scrub import scrub

Style = Literal["black", "label"]
Strategy = Literal["flatten"]

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


def _paint(image: Image.Image, boxes: list[Box], scale: float, style: Style) -> None:
    draw = ImageDraw.Draw(image)
    for box in boxes:
        x0, y0, x1, y1 = (round(v * scale) for v in box.rect)
        rect = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        if style == "black":
            draw.rectangle(rect, fill=(0, 0, 0))
            continue
        draw.rectangle(rect, fill=LABEL_FILL, outline=LABEL_BORDER)
        tag = box.category.replace("_", " ")
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
        _paint(image, boxes, scale, self.cfg.style)
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


def redact(
    src: bytes, boxes: list[Box], cfg: Redaction, scanned: set[int] | None = None
) -> Redacted:
    """Apply ``boxes`` to the PDF ``src`` and clean the document.

    Args:
        src: Original PDF.
        boxes: Boxes of the whole document (display space), before shaping.
        cfg: ``redaction`` section of the configuration.
        scanned: Pages that are scans (stored as JPEG once redacted).
    """
    scanned = scanned or set()
    redactor: Redactor = FlattenRedactor(cfg)
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(src)
        try:
            sizes = {i: pdf.get_page_size(i) for i in range(len(pdf))}
            applied = prepare(boxes, sizes)
            pages = sorted({b.page for b in applied})
            for index in pages:
                page_boxes = [b for b in applied if b.page == index]
                redactor.redact_page(pdf, index, page_boxes, index in scanned)
            out = io.BytesIO()
            pdf.save(out, flags=pdfium_c.FPDF_NO_INCREMENTAL)
        finally:
            pdf.close()
    return Redacted(scrub(out.getvalue()), applied, dict.fromkeys(pages, redactor.name))
