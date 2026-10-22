"""PDFium is not thread-safe: every engine entry point uses it under PDFIUM_LOCK.

Two threads inside PDFium at once crash the process (segmentation fault): a
caller analyses a document in one thread while others convert uploads and
render pages.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from src.services.anonymizer.convert import upload_to_pdf
from src.services.anonymizer.document import release_markdown, release_pdf, render_png
from src.services.anonymizer.pipeline import Pipeline
from tests.documents import (
    FakeNER,
    FakeReader,
    letter_pdf,
    record_unlocked_pdfium,
)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


def test_engine_uses_pdfium_only_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    letter = letter_pdf(tmp_path / "lettera.pdf")
    unlocked = record_unlocked_pdfium(monkeypatch)

    upload_to_pdf(letter.read_bytes(), ".pdf", tmp_path / "original.pdf", 10)
    upload_to_pdf(_png(), ".png", tmp_path / "photo.pdf", 10)
    analysis = Pipeline(FakeReader(), FakeNER()).analyze(tmp_path / "original.pdf")
    pdf, docx = tmp_path / "anonymized.pdf", tmp_path / "anonymized.docx"
    release_pdf(tmp_path / "original.pdf", analysis, pdf, docx, style="black", tol=2e-4)
    release_markdown(analysis, tmp_path / "anonymized.md")
    render_png(pdf, 0, 80, tmp_path / "page.png")

    assert unlocked == []
