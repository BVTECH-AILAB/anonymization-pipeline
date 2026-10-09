import io

import pytest
from PIL import Image

from anonymization_pipeline.ingest import DocumentError, decode, load, split_blocks
from tests.pipeline import pipeline_config


def _pdf(pages: int) -> bytes:
    images = [Image.new("RGB", (595, 842), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(
        buffer, "PDF", save_all=True, append_images=images[1:], resolution=72
    )
    return buffer.getvalue()


def test_every_pdf_page_is_rendered() -> None:
    cfg = pipeline_config()

    doc = load("referto.pdf", _pdf(2), cfg)

    assert doc.kind == "pdf" and len(doc.pages) == 2
    image = doc.pages[0].image
    assert image is not None
    w, h = image.size
    assert (
        abs(w - 595 * cfg.images.pdf_dpi / 72) <= 2
        and abs(h - 842 * cfg.images.pdf_dpi / 72) <= 2
    )


def test_pdf_limits_and_corruption() -> None:
    with pytest.raises(DocumentError, match="pagine"):
        load("a.pdf", _pdf(3), pipeline_config({"images": {"max_pages": 2}}))
    with pytest.raises(DocumentError, match="PDF"):
        load("a.pdf", b"%PDF-1.4 broken", pipeline_config())


def test_multipage_tiff_and_transparency() -> None:
    cfg = pipeline_config()
    buffer = io.BytesIO()
    first, second = (
        Image.new("RGB", (100, 50), "white"),
        Image.new("RGB", (120, 60), "white"),
    )
    first.save(buffer, "TIFF", save_all=True, append_images=[second])

    doc = load("scan.tif", buffer.getvalue(), cfg)

    assert [p.image.size for p in doc.pages if p.image] == [(100, 50), (120, 60)]

    buffer = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buffer, "PNG")
    image = load("x.png", buffer.getvalue(), cfg).pages[0].image
    assert image is not None
    assert image.mode == "RGB" and image.getpixel((5, 5)) == (255, 255, 255)


def test_pixel_limit_before_decoding() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buffer, "PNG")

    with pytest.raises(DocumentError, match="troppo grande"):
        load(
            "x.png",
            buffer.getvalue(),
            pipeline_config({"images": {"max_pixels": 1000}}),
        )


def test_invalid_formats() -> None:
    cfg = pipeline_config()
    with pytest.raises(DocumentError, match="non supportato"):
        load("x.docx", b"PK\x03\x04", cfg)
    with pytest.raises(DocumentError, match="non leggibile"):
        load("x.png", b"\x89PNG\r\n\x1a\nbroken", cfg)
    with pytest.raises(DocumentError, match="vuoto"):
        load("x.txt", b"", cfg)


def test_text_decoding() -> None:
    encodings = ["utf-8-sig", "cp1252", "latin-1"]

    assert decode("Città".encode("utf-16"), encodings) == "Città"
    assert decode("Città €".encode("cp1252"), encodings) == "Città €"
    assert decode("﻿ciao".encode(), encodings) == "ciao"


def test_blocks_rebuild_the_text() -> None:
    text = (
        "".join(f"Riga {i}: Mario Rossi abita in via Roma {i}\n" for i in range(300))
        + "x" * 900
    )

    blocks = split_blocks(text, 1000)

    assert "".join(blocks) == text
    assert all(len(b) <= 1000 for b in blocks)
    assert all(b.endswith("\n") for b in blocks[:-2])  # cuts at line ends


def test_text_file_in_blocks() -> None:
    cfg = pipeline_config({"text": {"max_block_chars": 200}})
    text = "Paziente: Mario Rossi\n" * 30

    doc = load("nota.txt", text.encode(), cfg)

    assert doc.kind == "text" and len(doc.pages) > 1
    assert "".join(p.text for p in doc.pages) == text
