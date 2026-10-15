"""Synthetic clinical documents for unit tests, built with reportlab.

Everything here is invented. The only codice fiscale is the textbook ``FAKE_CF`` (Mario Rossi).
"""

import io
import math
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from anonymization_pipeline.layers import TextLayer, Token
from tests.conftest import FAKE_CF

PATIENT = "Mario Rossi"
DOCTOR = "Dott.ssa Laura Bianchi"
PHONE = "333 123 4567"
EMAIL = "mario.rossi85@example.it"
WIDTH, HEIGHT = 595, 842  # A4 in points
HEADER_COLOUR = (0.05, 0.36, 0.45)

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


def _top(y: float) -> float:
    """Display y (from the top) -> reportlab y (from the bottom)."""
    return HEIGHT - y


def letter_pdf(
    path: Path,
    lines: list[str] | None = None,
    qr_text: str | None = None,
    signature: bool = False,
    pages: int = 1,
) -> Path:
    """A native letter with a coloured header band, metadata and an optional QR and signature.

    Args:
        path: Where to write the PDF.
        lines: Body lines, one every 22 points from y=100 (display space).
        qr_text: Content of a QR code drawn at (450, 650)-(540, 740).
        signature: Draw a blue scribble image at (300, 700)-(420, 740).
        pages: Number of identical pages.
    """
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    cv.setTitle(f"Lettera {PATIENT}")
    cv.setAuthor(DOCTOR)
    cv.setSubject(FAKE_CF)
    for _ in range(pages):
        cv.setFillColorRGB(*HEADER_COLOUR)
        cv.rect(0, _top(60), WIDTH, 60, stroke=0, fill=1)
        cv.setFillColorRGB(1, 1, 1)
        cv.setFont("Helvetica", 14)
        cv.drawString(40, _top(35), "Azienda Sanitaria Test")
        cv.setFillColorRGB(0, 0, 0)
        cv.setFont("Helvetica", 10)
        y = 100.0
        for line in lines or LETTER_LINES:
            cv.drawString(40, _top(y), line)
            y += 22
        if qr_text:
            widget = QrCodeWidget(qr_text)
            x0, y0, x1, y1 = widget.getBounds()
            drawing = Drawing(
                90, 90, transform=[90 / (x1 - x0), 0, 0, 90 / (y1 - y0), 0, 0]
            )
            drawing.add(widget)
            renderPDF.draw(drawing, cv, 450, _top(740))
        if signature:
            cv.drawImage(
                ImageReader(io.BytesIO(signature_png())),
                300,
                _top(740),
                width=120,
                height=40,
                mask="auto",
            )
        cv.showPage()
    cv.save()
    return path


def signature_png() -> bytes:
    """A blue pen scribble on a transparent background."""
    img = Image.new("RGBA", (360, 120), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    pts = [
        (10 + i * 340 / 59, 60 + 35 * math.sin((10 + i * 340 / 59) / 9))
        for i in range(60)
    ]
    draw.line(pts, fill=(20, 30, 120, 255), width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def text_pdf(
    path: Path, items: list[tuple[float, float, str]], size: float = 10
) -> Path:
    """One page with each string drawn at its display-space baseline position ``(x, y)``."""
    cv = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT))
    cv.setFont("Helvetica", size)
    for x, y, text in items:
        cv.drawString(x, _top(y), text)
    cv.save()
    return path


def string_width(text: str, size: float = 10) -> float:
    """Width of ``text`` in Helvetica at ``size`` points."""
    return canvas.Canvas(io.BytesIO()).stringWidth(text, "Helvetica", size)


def text_layer(page: int, text: str, source: str = "native") -> TextLayer:
    """A text layer for ``text``: one token per word, one line per text line, dummy boxes."""
    tokens = []
    for line, row in enumerate(text.split("\n")):
        for word in row.split():
            box = (0.0, float(line), 1.0, float(line) + 1)
            tokens.append(Token(word, box, [box] * len(word), line, source))
    return TextLayer.build(page, source, tokens)


def overlay(path: Path, draw: Callable[[canvas.Canvas], None], page: int = 0) -> Path:
    """Draw on one page of ``path`` (reportlab coordinates, from the bottom) and save it in full."""
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(WIDTH, HEIGHT))
    draw(cv)
    cv.save()
    writer = PdfWriter(clone_from=path)
    writer.pages[page].merge_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    writer.write(path)
    return path


def trapped_letter(path: Path) -> Path:
    """The letter with a QR code, a signature and the traps a redaction must not miss.

    Invisible white text with the codice fiscale, a note annotation with the patient's name added
    as an incremental update (the previous revision stays in the file) and metadata with personal
    data.
    """
    letter_pdf(path, qr_text="https://x.example.it/r?id=2026123456", signature=True)

    def white_text(cv: canvas.Canvas) -> None:
        cv.setFillColorRGB(1, 1, 1)
        cv.setFont("Helvetica", 3)
        cv.drawString(300, 12, f"REF {FAKE_CF}")

    overlay(path, white_text)
    writer = PdfWriter(path, incremental=True)
    writer.add_annotation(
        0, Text(rect=(500, 700, 520, 720), text=f"Richiamare {PATIENT}")
    )
    writer.write(path)
    return path
