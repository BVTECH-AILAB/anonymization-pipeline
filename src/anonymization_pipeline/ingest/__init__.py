"""Ingest: uploaded bytes -> the PDF the pipeline works on.

A PDF is checked and kept as it is. An image becomes a PDF with one page per frame, sized from
the image resolution: a JPEG without EXIF rotation is embedded as it is, every other image as a
lossless bitmap (never recompressed as JPEG). The kind of file comes from its magic bytes, never
from the name.

CPU-bound and synchronous: callers run it in a thread.
"""

import io
from dataclasses import dataclass
from typing import Literal

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps, UnidentifiedImageError

from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pipeline_config.schema import Ingest

Kind = Literal["pdf", "image"]
_EXIF_ORIENTATION = 0x0112


class DocumentError(ValueError):
    """Empty, corrupted, encrypted, too large or unsupported file: an error of the caller.

    The message is in Italian (it reaches the API user) and never contains document content.
    """


@dataclass(frozen=True)
class Ingested:
    """The PDF to anonymize and where it came from."""

    pdf: bytes
    kind: Kind
    pages: int


def file_kind(data: bytes) -> Kind:
    """Kind of the file from its magic bytes.

    Raises:
        DocumentError: Neither a PDF nor a supported image.
    """
    if data[:5] == b"%PDF-":
        return "pdf"
    if (
        data[:8] == b"\x89PNG\r\n\x1a\n"
        or data[:3] == b"\xff\xd8\xff"
        or data[:4] in (b"II*\x00", b"MM\x00*")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        or data[:2] == b"BM"
    ):
        return "image"
    raise DocumentError(
        "Formato non supportato: sono accettati PDF e immagini (PNG, JPEG, TIFF, WEBP, BMP)"
    )


def _check_pdf(data: bytes, limits: Ingest) -> int:
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if pdfium_c.FPDF_GetLastError() == pdfium_c.FPDF_ERR_PASSWORD:
                raise DocumentError("PDF protetto da password") from exc
            raise DocumentError("PDF non leggibile o danneggiato") from exc
        try:
            n = len(pdf)
            if n == 0:
                raise DocumentError("PDF senza pagine")
            if n > limits.max_pages:
                raise DocumentError(f"Il PDF ha {n} pagine (limite {limits.max_pages})")
            scale = limits.render_dpi / 72
            for i in range(n):
                width, height = pdf[i].get_size()
                if width * scale * height * scale > limits.max_pixels:
                    raise DocumentError(f"Pagina {i + 1} troppo grande")
        finally:
            pdf.close()
    return n


def _check_pixels(image: Image.Image, limits: Ingest) -> None:
    if image.width * image.height > limits.max_pixels:
        raise DocumentError("Immagine troppo grande")


def _flat(image: Image.Image) -> Image.Image:
    """Grey or RGB; transparency on a white background (on black, dark text disappears)."""
    if image.mode in ("RGBA", "LA", "P", "PA"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode in ("1", "L"):
        return image.convert("L")
    return image.convert("RGB")


def _dpi(image: Image.Image, default: int) -> tuple[float, float]:
    dpi = image.info.get("dpi")
    if not isinstance(dpi, tuple | list) or len(dpi) < 2:
        return float(default), float(default)
    try:
        x, y = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError):
        return float(default), float(default)
    if x < 50 or y < 50:  # missing or nonsense resolution: 1 dpi would make a huge page
        return float(default), float(default)
    return x, y


def _image_pdf(data: bytes, limits: Ingest) -> tuple[bytes, int]:
    try:
        image = Image.open(io.BytesIO(data))
        _check_pixels(image, limits)  # before load(): the bitmap is not decoded yet
        n = getattr(image, "n_frames", 1)
        if n > limits.max_pages:
            raise DocumentError(f"L'immagine ha {n} pagine (limite {limits.max_pages})")
        passthrough = (
            image.format == "JPEG"
            and n == 1
            and image.getexif().get(_EXIF_ORIENTATION, 1) == 1
            and image.mode in ("L", "RGB")
        )
        frames: list[tuple[Image.Image, tuple[float, float]]] = []
        for i in range(n):
            image.seek(i)
            _check_pixels(image, limits)  # the frames of a TIFF may differ in size
            image.load()
            frame = _flat(ImageOps.exif_transpose(image.copy()))
            frames.append((frame, _dpi(image, limits.image_dpi)))
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

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument.new()
        try:
            for frame, (dpi_x, dpi_y) in frames:
                width, height = frame.width * 72 / dpi_x, frame.height * 72 / dpi_y
                page = pdf.new_page(width, height)
                obj = pdfium.PdfImage.new(pdf)
                if passthrough:
                    obj.load_jpeg(io.BytesIO(data), inline=True)
                else:
                    bitmap = pdfium.PdfBitmap.from_pil(frame)
                    obj.set_bitmap(bitmap)
                    bitmap.close()
                obj.set_matrix(pdfium.PdfMatrix().scale(width, height))
                page.insert_obj(obj)
                page.gen_content()
                page.close()
            out = io.BytesIO()
            pdf.save(out)
        finally:
            pdf.close()
    return out.getvalue(), len(frames)


def ingest(data: bytes, limits: Ingest) -> Ingested:
    """Check the upload and turn it into the PDF to anonymize.

    Args:
        data: Uploaded bytes.
        limits: ``ingest`` section of the pipeline configuration.

    Raises:
        DocumentError: Empty, unsupported, unreadable, encrypted or too large file.
    """
    if not data:
        raise DocumentError("File vuoto")
    kind = file_kind(data)
    if kind == "pdf":
        return Ingested(data, "pdf", _check_pdf(data, limits))
    pdf, pages = _image_pdf(data, limits)
    _check_pdf(pdf, limits)
    return Ingested(pdf, "image", pages)
