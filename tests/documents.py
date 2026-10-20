"""Synthetic clinical PDFs and fake engines for unit tests.

Everything here is invented. The only codice fiscale is the textbook
``RSSMRA85M01H501U`` (Mario Rossi).

PDFs are drawn with reportlab and edited with pypdf, in the coordinates the
service uses: points, top-left origin, ``y`` the baseline of a text line.
"""

import io
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
import qrcode
from liteparse import LiteParse
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text as TextAnnotation
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from anonmyzation_pipeline.services.anonymization.geometry import Rect
from anonmyzation_pipeline.services.anonymization.layers import Span, Word
from anonmyzation_pipeline.services.anonymization.pdf_pages import PageView
from anonmyzation_pipeline.services.anonymization.reader import (
    LiteparseReader,
    group_lines,
)
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

A4 = (595.0, 842.0)
Colour = tuple[float, float, float]


class Page:
    """One page of a reportlab canvas, drawn in top-left coordinates."""

    def __init__(self, c: canvas.Canvas, height: float) -> None:
        self.c = c
        self.height = height

    def _y(self, y: float) -> float:
        return self.height - y

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 10,
        colour: Colour = (0, 0, 0),
        rotate: int = 0,
        invisible: bool = False,
    ) -> None:
        """Text with its baseline starting at ``(x, y)``; ``rotate`` degrees counter-clockwise."""
        c = self.c
        c.saveState()
        c.translate(x, self._y(y))
        if rotate:
            c.rotate(rotate)
        t = c.beginText(0, 0)
        t.setFont("Helvetica", size)
        t.setFillColorRGB(*colour)
        if invisible:
            t.setTextRenderMode(3)
        t.textOut(s)
        c.drawText(t)
        c.restoreState()

    def rect(
        self,
        r: Rect,
        fill: Colour | None = None,
        stroke: Colour | None = (0, 0, 0),
        width: float = 1.0,
        radius: float = 0.0,
    ) -> None:
        """A rectangle, filled and/or stroked, with rounded corners when ``radius`` > 0."""
        c = self.c
        c.saveState()
        if fill is not None:
            c.setFillColorRGB(*fill)
        if stroke is not None:
            c.setStrokeColorRGB(*stroke)
        c.setLineWidth(width)
        x0, y0, x1, y1 = r
        args = (x0, self._y(y1), x1 - x0, y1 - y0)
        if radius:
            c.roundRect(*args, radius, stroke=stroke is not None, fill=fill is not None)
        else:
            c.rect(*args, stroke=stroke is not None, fill=fill is not None)
        c.restoreState()

    def bezier(
        self, points: list[tuple[float, float]], colour: Colour, width: float
    ) -> None:
        """An open stroke of cubic curves: 1 + 3n points."""
        c = self.c
        c.saveState()
        c.setStrokeColorRGB(*colour)
        c.setLineWidth(width)
        path = c.beginPath()
        path.moveTo(points[0][0], self._y(points[0][1]))
        for i in range(1, len(points) - 2, 3):
            (x1, y1), (x2, y2), (x3, y3) = points[i : i + 3]
            path.curveTo(x1, self._y(y1), x2, self._y(y2), x3, self._y(y3))
        c.drawPath(path, stroke=1, fill=0)
        c.restoreState()

    def circle(self, cx: float, cy: float, r: float, fill: Colour) -> None:
        """A filled disc."""
        c = self.c
        c.saveState()
        c.setFillColorRGB(*fill)
        c.circle(cx, self._y(cy), r, stroke=0, fill=1)
        c.restoreState()

    def image(self, r: Rect, data: bytes) -> None:
        """A raster image (PNG or JPEG bytes) stretched over ``r``."""
        x0, y0, x1, y1 = r
        self.c.drawImage(
            ImageReader(io.BytesIO(data)),
            x0,
            self._y(y1),
            x1 - x0,
            y1 - y0,
            mask="auto",
        )


def draw_pdf(
    path: Path,
    *pages: Callable[[Page], None],
    size: tuple[float, float] = A4,
) -> Path:
    """A PDF with one page per drawing function."""
    c = canvas.Canvas(str(path), pagesize=size, pageCompression=1)
    for draw in pages or (lambda _: None,):
        draw(Page(c, size[1]))
        c.showPage()
    c.save()
    return path


def edit_pdf(
    path: Path,
    out: Path | None = None,
    rotate: dict[int, int] | None = None,
    cropbox: dict[int, Rect] | None = None,
    metadata: dict[str, str] | None = None,
    xmp: str | None = None,
    outline: list[str] | None = None,
    notes: list[tuple[int, Rect, str]] | None = None,
    attachments: dict[str, bytes] | None = None,
    password: str | None = None,
) -> Path:
    """Rewrite ``path`` (into ``out``) with the given extras.

    ``rotate`` adds a ``/Rotate`` (degrees clockwise); ``cropbox`` takes PDF user
    space ``(x0, y0, x1, y1)``; ``notes`` are sticky-note annotations.
    """
    writer = PdfWriter(clone_from=PdfReader(path))
    for pno, degrees in (rotate or {}).items():
        writer.pages[pno].rotate(degrees)
    for pno, box in (cropbox or {}).items():
        writer.pages[pno].cropbox = RectangleObject(box)
    if metadata:
        writer.add_metadata(metadata)
    if xmp is not None:
        stream = DecodedStreamObject()
        stream.set_data(xmp.encode())
        stream[NameObject("/Type")] = NameObject("/Metadata")
        stream[NameObject("/Subtype")] = NameObject("/XML")
        writer.root_object[NameObject("/Metadata")] = writer._add_object(stream)
    for title in outline or []:
        writer.add_outline_item(title, 0)
    for pno, r, text in notes or []:
        writer.add_annotation(pno, TextAnnotation(rect=r, text=text, open=False))
    for name, data in (attachments or {}).items():
        writer.add_attachment(name, data)
    if password:
        # RC4-128 needs no crypto library: enough for a password-protected fixture
        writer.encrypt(
            user_password=password, owner_password=password, algorithm="RC4-128"
        )
    dest = out or path
    with dest.open("wb") as fh:
        writer.write(fh)
    return dest


def overlay_pdf(
    path: Path, draw: Callable[[Page], None], out: Path | None = None
) -> Path:
    """``path`` with more drawn on top of its first page (hidden text over a scan, a stamp)."""
    reader = PdfReader(path)
    width, height = (float(v) for v in reader.pages[0].mediabox[2:])
    layer = draw_pdf(
        (out or path).with_suffix(".overlay.pdf"), draw, size=(width, height)
    )
    writer = PdfWriter(clone_from=reader)
    writer.pages[0].merge_page(PdfReader(layer).pages[0])
    dest = out or path
    with dest.open("wb") as fh:
        writer.write(fh)
    layer.unlink()
    return dest


def find_text(path: Path, needle: str, pno: int = 0) -> Rect:
    """Box of the first occurrence of ``needle`` on a page, top-left page space (unrotated)."""
    with pdfium.PdfDocument(path) as pdf:
        page = pdf[pno]
        textpage = page.get_textpage()
        found = textpage.search(needle).get_next()
        if found is None:
            raise AssertionError(f"{needle!r} not on page {pno}")
        start, count = found
        boxes = [textpage.get_charbox(i) for i in range(start, start + count)]
        _, _, _, top = page.get_cropbox()
        x0, _, _, _ = page.get_cropbox()
        return (
            min(b[0] for b in boxes) - x0,
            top - max(b[3] for b in boxes),
            max(b[2] for b in boxes) - x0,
            top - min(b[1] for b in boxes),
        )


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def qr_png(text: str) -> bytes:
    """A QR code as PNG bytes."""
    buf = io.BytesIO()
    qrcode.make(text).save(buf)
    return buf.getvalue()


def letter_pdf(
    path: Path,
    lines: list[str] | None = None,
    qr_text: str | None = None,
    signature: bool = False,
    extra: Callable[[Page], None] | None = None,
) -> Path:
    """A one-page native letter with a coloured header band, metadata and optional
    QR/signature; ``extra`` draws more on the page."""

    def draw(page: Page) -> None:
        page.rect((0, 0, 595, 60), fill=(0.05, 0.36, 0.45), stroke=None)
        page.text(40, 35, "Azienda Sanitaria Test", size=14, colour=(1, 1, 1))
        y = 100.0
        for line in lines or LETTER_LINES:
            page.text(40, y, line, size=10)
            y += 22
        if qr_text:
            page.image((450, 650, 540, 740), qr_png(qr_text))
        if signature:
            page.image((300, 700, 420, 740), signature_png())
        if extra:
            extra(page)

    draw_pdf(path, draw)
    return edit_pdf(
        path,
        metadata={
            "/Title": f"Lettera {PATIENT}",
            "/Author": DOCTOR,
            "/Subject": FAKE_CF,
        },
    )


def signature_png() -> bytes:
    """A blue pen scribble on a transparent background."""
    img = Image.new("RGBA", (360, 120), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    xs = np.linspace(10, 350, 60)
    pts = [(float(x), float(60 + 35 * np.sin(x / 9))) for x in xs]
    draw.line(pts, fill=(20, 30, 120, 255), width=3)
    return _png(img)


def scanned_pdf(
    native: Path, path: Path, dpi: int = 150, scribble: bool = False
) -> Path:
    """Rasterise ``native`` into an image-only PDF, like a scanner would."""
    with pdfium.PdfDocument(native) as src:
        sizes = [src[i].get_size() for i in range(len(src))]
        images = []
        for i in range(len(src)):
            img = (
                src[i]
                .render(scale=dpi / 72, rev_byteorder=True)
                .to_pil()
                .convert("RGB")
            )
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
            images.append(buf.getvalue())
    c = canvas.Canvas(str(path))
    for (w, h), data in zip(sizes, images, strict=True):
        c.setPageSize((w, h))
        c.drawImage(ImageReader(io.BytesIO(data)), 0, 0, w, h)
        c.showPage()
    c.save()
    return path


def _parser(dpi: int = 150) -> LiteParse:
    return LiteParse(
        ocr_enabled=False,
        emit_word_boxes=True,
        keep_headers_footers=True,
        preserve_very_small_text=True,
        include_complexity=True,
        dpi=float(dpi),
        quiet=True,
    )


def native_words(native: Path) -> dict[int, list[Word]]:
    """What a perfect OCR would read: the words of the native PDF (display space)."""
    out: dict[int, list[Word]] = {}
    for page in _parser().parse(str(native)).pages:
        words = [
            Word(w.text, (w.x, w.y, w.x + w.width, w.y + w.height), 0.99, "fake-ocr", n)
            for n, item in enumerate(page.text_items)
            for w in item.words
            if w.text.strip()
        ]
        out[page.page_num - 1] = group_lines(words)
    return out


class FakeReader(LiteparseReader):
    """The LiteParse reader with the OCR server replaced by pre-computed words.

    Native text is read by LiteParse as in production; a scanned page gets the
    words given for it (display space), as the OCR server would return them.
    """

    def __init__(
        self, words: dict[int, list[Word]] | None = None, dpi: int = 150
    ) -> None:
        super().__init__(
            "http://ocr.test",
            dpi=dpi,
            parser=_parser(dpi),
            ocr_parser=lambda _: _parser(dpi),
            client=httpx.Client(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
            ),
        )
        self.words = words or {}
        self.load()

    def _ocr_page(self, path: Path, pno: int) -> list[Word]:
        return group_lines(list(self.words.get(pno, [])))


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


def page_view(path: Path, pno: int = 0) -> PageView:
    """The pypdfium2 view of one page of a fixture file."""
    return PageView(pdfium.PdfDocument(path.read_bytes())[pno], pno)


def page_views(path: Path) -> list[PageView]:
    """Views of every page of a fixture file."""
    pdf = pdfium.PdfDocument(path.read_bytes())
    return [PageView(pdf[i], i) for i in range(len(pdf))]
