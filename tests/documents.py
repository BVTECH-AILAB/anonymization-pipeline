"""Synthetic clinical PDFs and fake engines for unit tests.

Everything here is invented. The only codice fiscale is the textbook
``RSSMRA85M01H501U`` (Mario Rossi).
"""

import io
import re
from pathlib import Path

import numpy as np
import pymupdf
import qrcode
from PIL import Image, ImageDraw

from anonmyzation_pipeline.services.anonymization.layers import Span
from anonmyzation_pipeline.services.anonymization.ocr import Word
from tests.conftest import FAKE_CF

PATIENT = "Mario Rossi"
DOCTOR = "Dott.ssa Laura Bianchi"
PHONE = "333 123 4567"
EMAIL = "mario.rossi85@example.it"

LETTER_LINES = [
    "Ospedale Maggiore della Carita - U.O.C. Medicina Interna",
    "LETTERA DI DIMISSIONE",
    f"Paziente: {PATIENT}",
    "Nato il 01/08/1985 a Roma",
    f"Codice fiscale: {FAKE_CF}",
    "Residenza: Via Garibaldi 12, 00185 Roma",
    f"Telefono: {PHONE}  Email: {EMAIL}",
    "Nosologico 2026123456  Ricovero dal 03/02/2026",
    "Il Sig. Rossi e stato ricoverato per riacutizzazione di morbo di Crohn.",
    "Riflesso di Babinski assente. Terapia con metformina 1000 mg.",
    f"Il medico: {DOCTOR}",
]


def letter_pdf(
    path: Path,
    lines: list[str] | None = None,
    qr_text: str | None = None,
    signature: bool = False,
) -> Path:
    """A one-page native letter with a coloured header band, metadata and optional QR/signature."""
    with pymupdf.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(0, 0, 595, 60), color=None, fill=(0.05, 0.36, 0.45))
        page.insert_text(
            (40, 35), "Azienda Sanitaria Test", fontsize=14, color=(1, 1, 1)
        )
        y = 100
        for line in lines or LETTER_LINES:
            page.insert_text((40, y), line, fontsize=10)
            y += 22
        if qr_text:
            buf = io.BytesIO()
            qrcode.make(qr_text).save(buf)
            page.insert_image(pymupdf.Rect(450, 650, 540, 740), stream=buf.getvalue())
        if signature:
            page.insert_image(pymupdf.Rect(300, 700, 420, 740), stream=signature_png())
        doc.set_metadata(
            {"title": f"Lettera {PATIENT}", "author": DOCTOR, "subject": FAKE_CF}
        )
        doc.save(path)
    return path


def signature_png() -> bytes:
    """A blue pen scribble on a transparent background."""
    img = Image.new("RGBA", (360, 120), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    xs = np.linspace(10, 350, 60)
    pts = [(float(x), float(60 + 35 * np.sin(x / 9))) for x in xs]
    draw.line(pts, fill=(20, 30, 120, 255), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def scanned_pdf(
    native: Path, path: Path, dpi: int = 150, scribble: bool = False
) -> Path:
    """Rasterise ``native`` into an image-only PDF, like a scanner would."""
    with pymupdf.open(native) as src, pymupdf.open() as out:
        for page in src:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if scribble:
                draw = ImageDraw.Draw(img)
                s = dpi / 72
                xs = np.linspace(300 * s, 420 * s, 50)
                draw.line(
                    [
                        (float(x), float(720 * s + 15 * s * np.sin(x / (6 * s))))
                        for x in xs
                    ],
                    fill=(40, 40, 40),
                    width=3,
                )
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=buf.getvalue())
        out.save(path)
    return path


def native_words(native: Path) -> dict[int, list[Word]]:
    """What a perfect OCR would read: the words of the native PDF, one line per text line."""
    out: dict[int, list[Word]] = {}
    with pymupdf.open(native) as doc:
        for page in doc:
            words = []
            for x0, y0, x1, y1, text, block, line, _ in page.get_text("words"):
                words.append(
                    Word(text, (x0, y0, x1, y1), 0.99, "fake-ocr", block * 1000 + line)
                )
            out[page.number] = words
    return out


class FakeOCR:
    """OCR stand-in: returns pre-computed words per page."""

    def __init__(self, words: dict[int, list[Word]] | None = None) -> None:
        self.words = words or {}

    @property
    def ready(self) -> bool:
        return True

    def load(self) -> None:
        return None

    def page_words(self, page: pymupdf.Page) -> list[Word]:
        return list(self.words.get(page.number, []))


class FakeNER:
    """NER stand-in: tags the given names as persons wherever they appear."""

    def __init__(self, names: tuple[str, ...] = (PATIENT, "Laura Bianchi")) -> None:
        self.names = names

    @property
    def ready(self) -> bool:
        return True

    def load(self) -> None:
        return None

    def detect(self, text: str) -> list[Span]:
        out = []
        for name in self.names:
            for m in re.finditer(re.escape(name), text):
                out.append(
                    Span(m.start(), m.end(), "PERSON", 0.95, "fake-ner", m.group())
                )
        return out
