"""Ingest: file bytes -> Document with the pages as images (PDF, images) or text.

Every PDF page is rendered (the VLM and the verification need it). With
``phases.extraction.native_pdf.engine = docling`` every page is also analysed: it is "native"
(extractable with docling) if it has enough readable native text, few images and no hidden text;
otherwise ``Page.reason`` says why not.

CPU-bound and synchronous: the pipeline runs it in a thread.
"""

import io
import logging
import threading
from pathlib import Path
from typing import Any, cast

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps, UnidentifiedImageError

from anonymization_pipeline.domain import Document, DocumentKind, Page
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.pipeline_config.schema import NativePdf

logger = logging.getLogger(__name__)

TEXT_EXT = {".txt", ".text", ".md", ".csv", ".log", ".json", ".xml", ".html", ".eml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# PDFium is not thread-safe: documents are loaded in worker threads
PDFIUM_LOCK = threading.Lock()


class DocumentError(ValueError):
    """Corrupted, encrypted, too large or unsupported file: an error of the caller.

    The message is in Italian (it reaches the API user) and never contains document content.
    """


def file_kind(name: str, data: bytes) -> DocumentKind:
    """Kind of the file, from its magic bytes or extension."""
    ext = Path(name).suffix.lower()
    if data[:5] == b"%PDF-" or ext == ".pdf":
        return "pdf"
    if (
        ext in IMAGE_EXT
        or data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:3] == b"\xff\xd8\xff"
        or data[:4] in (b"II*\x00", b"MM\x00*")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    ):
        return "image"
    if ext in TEXT_EXT or not ext:
        return "text"
    raise DocumentError(f"Formato non supportato: {ext}")


def _rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        # transparency on a white background: on black, dark text would disappear
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _check_pixels(image: Image.Image, limit: int) -> None:
    if image.width * image.height > limit:
        raise DocumentError(
            f"Immagine troppo grande: {image.width}x{image.height} pixel (limite images.max_pixels = {limit})"
        )


def _object_text(obj: Any, textpage: Any) -> str:
    n = pdfium_c.FPDFTextObj_GetText(obj.raw, textpage.raw, None, 0)
    if n <= 2:
        return ""
    buffer = (pdfium_c.FPDF_WCHAR * (n // 2))()
    pdfium_c.FPDFTextObj_GetText(obj.raw, textpage.raw, buffer, n)
    return bytes(buffer).decode("utf-16-le", errors="ignore").rstrip("\x00")


def hidden_text(
    page: Any, textpage: Any, image: Image.Image, scale: float, min_contrast: int
) -> bool:
    """Text the PDF holds but that cannot be seen.

    Invisible render mode, outside the page, or without contrast in its box of the rendered page
    (white on white, under an image, microscopic). The check is visual: white text on a coloured
    bar stays visible.
    """
    grey = image.convert("L")
    _, height = page.get_size()
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_TEXT], max_depth=8):
        if not _object_text(obj, textpage).strip():
            continue
        if pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw) in (
            pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE,
            pdfium_c.FPDF_TEXTRENDERMODE_CLIP,
        ):
            return True
        x0, y0, x1, y1 = obj.get_bounds()
        box = (
            max(0, int(x0 * scale)),
            max(0, int((height - y1) * scale)),
            min(image.width, int(x1 * scale) + 1),
            min(image.height, int((height - y0) * scale) + 1),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return True  # outside the page
        # single band ("L"): two numbers, not a tuple per band
        low, high = cast(tuple[float, float], grey.crop(box).getextrema())
        if high - low < min_contrast:
            return True
    return False


def analyze_page(page: Any, image: Image.Image, scale: float, c: NativePdf) -> str:
    """Empty string if the page is native, otherwise the reason it goes to the VLM."""
    if page.get_rotation():
        return "rotated"
    textpage = page.get_textpage()
    try:
        text = textpage.get_text_range()
        useful = [ch for ch in text if not ch.isspace()]
        if len(useful) < c.min_chars:
            return "little_text"
        unreadable = sum(
            1
            for ch in useful
            if ch == "�" or ord(ch) < 32 or 0xE000 <= ord(ch) <= 0xF8FF
        )
        if unreadable > c.max_unreadable * len(useful):
            return "unreadable_text"
        width, height = page.get_size()
        area = 0.0
        for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=8):
            x0, y0, x1, y1 = obj.get_bounds()
            area += max(0.0, min(x1, width) - max(x0, 0)) * max(
                0.0, min(y1, height) - max(y0, 0)
            )
        if area > c.max_image_fraction * width * height:
            return "images"
        if hidden_text(page, textpage, image, scale, c.min_contrast):
            return "hidden_text"
        return ""
    finally:
        textpage.close()


def load_pdf(data: bytes, cfg: PipelineConfig) -> Document:
    c = cfg.images
    native = cfg.phases.extraction.native_pdf
    doc = Document(kind="pdf", source=data if native.engine == "docling" else None)
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            raise DocumentError(
                "PDF non leggibile, danneggiato o protetto da password"
            ) from exc
        try:
            n = len(pdf)
            if n == 0:
                raise DocumentError("PDF senza pagine")
            if n > c.max_pages:
                raise DocumentError(
                    f"Il PDF ha {n} pagine (limite images.max_pages = {c.max_pages})"
                )
            scale = c.pdf_dpi / 72
            for i in range(n):
                page = pdf[i]
                try:
                    width, height = page.get_size()
                    if width * scale * height * scale > c.max_pixels:
                        raise DocumentError(
                            f"Pagina {i + 1} troppo grande a {c.pdf_dpi} dpi (limite images.max_pixels)"
                        )
                    image = page.render(scale=scale).to_pil().convert("RGB")
                    reason = (
                        analyze_page(page, image, scale, native)
                        if native.engine == "docling"
                        else "vlm_engine"
                    )
                finally:
                    page.close()
                doc.pages.append(
                    Page(index=i, image=image, native=not reason, reason=reason)
                )
        finally:
            pdf.close()
    return doc


def load_image(data: bytes, cfg: PipelineConfig) -> Document:
    c = cfg.images
    try:
        image = Image.open(io.BytesIO(data))
        _check_pixels(image, c.max_pixels)  # before load(): the bitmap is not decoded
        n = getattr(image, "n_frames", 1)
        if n > c.max_pages:
            raise DocumentError(
                f"L'immagine ha {n} pagine (limite images.max_pages = {c.max_pages})"
            )
        doc = Document(kind="image")
        for i in range(n):
            image.seek(i)
            _check_pixels(
                image, c.max_pixels
            )  # the frames of a TIFF may have different sizes
            image.load()
            doc.pages.append(Page(index=i, image=_rgb(image.copy())))
    except DocumentError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise DocumentError("Immagine non leggibile") from exc
    return doc


def decode(data: bytes, encodings: list[str]) -> str:
    """UTF-16 only with a BOM (without one almost any byte sequence "decodes" into junk), then the
    configured encodings in order; the last one replaces invalid bytes instead of failing."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    *first, last = encodings
    for encoding in first:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode(last, errors="replace")


def split_blocks(text: str, maximum: int) -> list[str]:
    """Split the text into blocks of at most ``maximum`` characters, cutting at line ends (or, for
    very long lines, at a space). ``"".join(split_blocks(text)) == text``."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if len(text) - i <= maximum:
            out.append(text[i:])
            break
        end = text.rfind("\n", i, i + maximum) + 1
        if end <= i:
            end = text.rfind(" ", i, i + maximum) + 1
        if end <= i:
            end = i + maximum
        out.append(text[i:end])
        i = end
    return out or [""]


def load_text(data: bytes, cfg: PipelineConfig) -> Document:
    text = decode(data, cfg.text.encodings)
    blocks = split_blocks(text, cfg.text.max_block_chars)
    return Document(
        kind="text", pages=[Page(index=i, text=b) for i, b in enumerate(blocks)]
    )


def load(name: str, data: bytes, cfg: PipelineConfig) -> Document:
    """Load a document.

    Raises:
        DocumentError: Empty, unsupported, unreadable or too large file.
    """
    if not data:
        raise DocumentError("File vuoto")
    kind = file_kind(name, data)
    if kind == "pdf":
        return load_pdf(data, cfg)
    if kind == "image":
        return load_image(data, cfg)
    return load_text(data, cfg)
