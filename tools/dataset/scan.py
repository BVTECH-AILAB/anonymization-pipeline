"""Turn a native PDF into a realistic scan: raster pages with skew, paper tint,
noise, blur and JPEG artefacts. Optionally adds an invisible OCR text layer,
as scanner software does. Ground-truth boxes follow the same transform."""

import copy
import io
import random
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from anonymization_core.geometry import (
    Rect,
    intersection,
    to_display,
)
from anonymization_core.pdf_pages import PageView
from reportlab import rl_config
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from tools.pdf import words

OCR_FONT = "Helvetica"


def transform_rect(rect: Rect, M: np.ndarray, s: float) -> Rect:
    """A display-space box (points) through the affine transform ``M`` of the
    rendering at scale ``s`` (pixels per point): the bound of its four corners."""
    x0, y0, x1, y1 = (v * s for v in rect)
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]]) @ M.T
    return (
        round(float(pts[:, 0].min()) / s, 2),
        round(float(pts[:, 1].min()) / s, 2),
        round(float(pts[:, 0].max()) / s, 2),
        round(float(pts[:, 1].max()) / s, 2),
    )


@contextmanager
def _binary_streams() -> Iterator[None]:
    """JPEG pages stored as they are, not ASCII85-encoded (a quarter smaller)."""
    previous = rl_config.useA85  # ty: ignore[unresolved-attribute]
    rl_config.useA85 = 0  # ty: ignore[unresolved-attribute]
    try:
        yield
    finally:
        rl_config.useA85 = previous  # ty: ignore[unresolved-attribute]


def _ocr_text(c: canvas.Canvas, word: str, r: Rect, height: float) -> None:
    """One invisible word (text render mode 3) on its box, baseline at 80% of it."""
    size = max(2.0, (r[3] - r[1]) * 0.8)
    text = c.beginText()
    text.setTextRenderMode(3)
    text.setFont(OCR_FONT, size)
    text.setTextOrigin(r[0], height - (r[3] - (r[3] - r[1]) * 0.2))
    text.textOut(word)
    c.drawText(text)


def _degrade(img: np.ndarray, rnd: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    rng = np.random.default_rng(rnd.randint(0, 1 << 30))
    out = img.astype(np.float32)
    # paper tint and uneven illumination
    tint = np.array(
        [rnd.uniform(0.93, 1.0), rnd.uniform(0.92, 0.99), rnd.uniform(0.86, 0.97)]
    )
    grad = np.linspace(rnd.uniform(0.9, 1.0), rnd.uniform(0.95, 1.0), w)[None, :, None]
    out = out * tint * grad
    # toner: slightly lighter blacks, more contrast
    out = (out - 128) * rnd.uniform(1.0, 1.15) + 128 + rnd.uniform(-8, 12)
    out += rng.normal(0, rnd.uniform(3, 9), out.shape)
    # sparse speckles
    mask = rng.random((h, w)) < rnd.uniform(0.0002, 0.0012)
    out[mask] = rnd.uniform(40, 120)
    out = np.clip(out, 0, 255).astype(np.uint8)
    k = rnd.choice([0, 3, 3, 5])
    if k:
        out = cv2.GaussianBlur(out, (k, k), rnd.uniform(0.4, 0.9))
    if rnd.random() < 0.5:
        g = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
        out = cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)
    return out


def _fax(img: np.ndarray, rnd: random.Random) -> np.ndarray:
    """A page sent by fax: low resolution, black and white, noisy, with streaks."""
    h, w = img.shape[:2]
    rng = np.random.default_rng(rnd.randint(0, 1 << 30))
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(g, (int(w * 0.45), int(h * 0.45)), interpolation=cv2.INTER_AREA)
    small = small.astype(np.float32) + rng.normal(0, 22, small.shape)
    bw = np.where(small < rnd.uniform(140, 160), 0, 255).astype(np.uint8)
    out = cv2.resize(bw, (w, h), interpolation=cv2.INTER_NEAREST)
    for _ in range(rnd.randint(3, 6)):  # streaks of a dirty fax drum
        y = rnd.randint(0, h - 4)
        out[y : y + rnd.randint(1, 3), :] = rnd.choice([0, 120])
    mask = rng.random((h, w)) < 0.003
    out[mask] = 0
    return cv2.cvtColor(out, cv2.COLOR_GRAY2RGB)


def make_scan(
    native: Path,
    gt: dict,
    out_path: Path,
    rnd: random.Random,
    ocr_layer: bool,
    turned: frozenset[int] = frozenset(),
    faxed: frozenset[int] = frozenset(),
) -> dict:
    """Write a scanned twin of ``native`` and return its ground truth.

    Ground-truth boxes (and the words of the optional OCR layer) are in the page
    space of the native file; the scan is made from the rendered, possibly
    rotated, page, so each box goes through the page rotation and then through
    the scanner's skew. Pages in ``turned`` are fed upside down (skew plus 180
    degrees); pages in ``faxed`` are degraded like a fax.

    Returns:
        The ground truth with moved boxes and a ``scan`` entry (dpi, OCR layer,
        per-page affine transforms).
    """
    dpi = rnd.choice([200, 240, 300])
    s = dpi / 72
    transforms = []
    sizes = []
    infos = []
    with _binary_streams():
        c = canvas.Canvas(str(out_path), pageCompression=1)
        c.setProducer("ScanSnap Manager 7.2")
        c.setCreator("ScanSnap")
        c.setTitle("")
        c.setAuthor("")
        src = pdfium.PdfDocument(native)
        try:
            for pno in range(len(src)):
                page = src[pno]
                view = PageView(page, pno)
                infos.append(view.info)
                img = view.render(dpi)
                h, w = img.shape[:2]
                angle = rnd.uniform(-1.6, 1.6) + (180 if pno in turned else 0)
                M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                M[:, 2] += (rnd.uniform(-0.01, 0.01) * w, rnd.uniform(-0.01, 0.01) * h)
                img = cv2.warpAffine(
                    img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(250, 250, 248)
                )
                img = _degrade(img, rnd)
                if pno in faxed:
                    img = _fax(img, rnd)
                ok, jpg = cv2.imencode(
                    ".jpg",
                    cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, rnd.randint(55, 85)],
                )
                width, height = w / s, h / s
                sizes.append((width, height))
                c.setPageSize((width, height))
                c.drawImage(
                    ImageReader(io.BytesIO(jpg.tobytes())),
                    0,
                    0,
                    width=width,
                    height=height,
                )
                if ocr_layer:
                    for rect, word in words(page):
                        r = transform_rect(to_display(rect, view.info), M, s)
                        _ocr_text(c, word, r, height)
                c.showPage()
                transforms.append(M)
                page.close()
        finally:
            src.close()
        c.save()

    sgt = copy.deepcopy(gt)
    for p, (width, height) in zip(sgt["pages"], sizes, strict=True):
        p["width"], p["height"] = width, height
    for m in sgt["mentions"]:
        if m["kind"] in ("metadata", "annotation"):
            m["kind"] = "dropped"  # not carried into a scan
        for b in m["boxes"]:
            shown = to_display(tuple(b["rect"]), infos[b["page"]])
            moved = transform_rect(shown, transforms[b["page"]], s)
            # skew can push a box at the edge partly outside the scanned page
            page_rect = (0.0, 0.0, *sizes[b["page"]])
            b["rect"] = [round(v, 2) for v in intersection(moved, page_rect)]
    for k in sgt["keep"]:
        shown = to_display(tuple(k["rect"]), infos[k["page"]])
        k["rect"] = list(transform_rect(shown, transforms[k["page"]], s))
    # per-page affine transform (pixels at `dpi`), to map native coordinates onto the scan
    sgt["scan"] = {
        "dpi": dpi,
        "ocr_layer": ocr_layer,
        "transforms": [m.tolist() for m in transforms],
    }
    return sgt
