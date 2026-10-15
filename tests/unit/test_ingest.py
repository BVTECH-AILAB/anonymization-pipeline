import io
from pathlib import Path
from typing import Any, cast

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, StreamObject

from anonymization_pipeline.ingest import DocumentError, file_kind, ingest
from anonymization_pipeline.pipeline_config.schema import Ingest
from tests.documents import letter_pdf

LIMITS = Ingest(max_pages=5, max_pixels=120_000_000, render_dpi=200, image_dpi=200)


def _image(
    mode: str = "RGB",
    size: tuple[int, int] = (120, 80),
    image_format: str = "PNG",
    **save: Any,
) -> bytes:
    rng = np.random.default_rng(7)
    channels = {"L": 1, "RGB": 3, "RGBA": 4}[mode]
    pixels = rng.integers(0, 256, (size[1], size[0], channels), dtype=np.uint8)
    image = Image.fromarray(pixels.squeeze(), mode)
    buf = io.BytesIO()
    image.save(buf, image_format, **save)
    return buf.getvalue()


def _render(pdf: bytes, scale: float, page: int = 0) -> np.ndarray:
    doc = pdfium.PdfDocument(pdf)
    try:
        return np.asarray(doc[page].render(scale=scale).to_pil().convert("RGB"))
    finally:
        doc.close()


def _embedded(pdf: bytes) -> np.ndarray:
    """Pixels of the only image of the first page, as stored in the PDF."""
    doc = pdfium.PdfDocument(pdf)
    try:
        (image,) = doc[0].get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
        return np.asarray(image.get_bitmap().to_pil().convert("RGB"))
    finally:
        doc.close()


def test_pdf_is_kept_as_it_is(tmp_path: Path) -> None:
    data = letter_pdf(tmp_path / "in.pdf", pages=2).read_bytes()

    result = ingest(data, LIMITS)

    assert (result.pdf, result.kind, result.pages) == (data, "pdf", 2)


def test_png_becomes_a_lossless_pdf_page_sized_from_its_resolution() -> None:
    data = _image("RGB", (240, 120), dpi=(144, 144))

    result = ingest(data, LIMITS)

    assert (result.kind, result.pages) == ("image", 1)
    doc = pdfium.PdfDocument(result.pdf)
    try:
        # 240 px at 144 dpi = 120 pt (PDFium sizes are 32-bit floats)
        assert doc[0].get_size() == pytest.approx((120, 60), abs=0.01)
    finally:
        doc.close()
    original = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    assert np.array_equal(_embedded(result.pdf), original)


def test_jpeg_without_rotation_is_embedded_unchanged() -> None:
    data = _image("RGB", image_format="JPEG", quality=80)

    result = ingest(data, LIMITS)

    resources = cast(
        DictionaryObject, PdfReader(io.BytesIO(result.pdf)).pages[0]["/Resources"]
    )
    xobjects = cast(DictionaryObject, resources["/XObject"].get_object())
    streams = [cast(StreamObject, xobjects[name].get_object()) for name in xobjects]
    assert [(s["/Filter"], s.get_data()) for s in streams] == [("/DCTDecode", data)]


def test_exif_rotation_is_applied() -> None:
    image = Image.new("RGB", (200, 100), "white")
    exif = image.getexif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif.tobytes())

    result = ingest(buf.getvalue(), LIMITS)

    doc = pdfium.PdfDocument(result.pdf)
    try:
        width, height = doc[0].get_size()
    finally:
        doc.close()
    assert height > width


def test_transparent_image_is_put_on_white() -> None:
    image = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    buf = io.BytesIO()
    image.save(buf, "PNG")

    rendered = _render(ingest(buf.getvalue(), LIMITS).pdf, 200 / 72)

    assert rendered.min() >= 250


def test_every_tiff_frame_becomes_a_page() -> None:
    frames = [Image.new("L", (100, 60), shade) for shade in (0, 128, 255)]
    buf = io.BytesIO()
    frames[0].save(buf, "TIFF", save_all=True, append_images=frames[1:])

    result = ingest(buf.getvalue(), LIMITS)

    assert result.pages == 3
    assert _render(result.pdf, 1, page=1).mean() == pytest.approx(128, abs=2)


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=letter_pdf(tmp_path / "in.pdf"))
    writer.encrypt("segreta")
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(DocumentError, match="password"):
        ingest(buf.getvalue(), LIMITS)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "vuoto"),
        (b"PK\x03\x04 docx", "non supportato"),
        (b"%PDF-1.7 garbage", "non leggibile"),
        (b"\x89PNG\r\n\x1a\n garbage", "non leggibile"),
    ],
)
def test_invalid_files_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(DocumentError, match=message):
        ingest(data, LIMITS)


def test_too_many_pages_are_rejected(tmp_path: Path) -> None:
    data = letter_pdf(tmp_path / "in.pdf", pages=6).read_bytes()

    with pytest.raises(DocumentError, match="6 pagine"):
        ingest(data, LIMITS)


def test_oversized_page_is_rejected(tmp_path: Path) -> None:
    data = letter_pdf(tmp_path / "in.pdf").read_bytes()

    with pytest.raises(DocumentError, match="troppo grande"):
        ingest(data, LIMITS.model_copy(update={"max_pixels": 1_000_000}))


def test_oversized_image_is_rejected_before_decoding() -> None:
    data = _image("L", (2000, 2000))

    with pytest.raises(DocumentError, match="troppo grande"):
        ingest(data, LIMITS.model_copy(update={"max_pixels": 1_000_000}))


def test_kind_comes_from_the_magic_bytes() -> None:
    assert file_kind(b"%PDF-1.4") == "pdf"
    assert file_kind(_image()) == "image"
    with pytest.raises(DocumentError):
        file_kind(b"<html>")
