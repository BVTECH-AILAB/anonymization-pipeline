"""Visual PII: signatures, stamps, barcodes, QR codes.

- native PDFs: embedded raster images (except header logos) are decoded as
  codes, otherwise classified as stamp or signature;
- any rendered page: 1D/2D codes via zxing (vector or raster);
- scanned pages: "unexplained ink", i.e. marks that OCR does not read as text,
  that are not table rules or solid bands. Signatures and stamps end up here.
"""

import re
from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf
import zxingcpp

from anonmyzation_pipeline.services.anonymization.layers import Rect, page_number

_2D = {"QRCode", "MicroQRCode", "RMQRCode", "DataMatrix", "Aztec", "PDF417", "MaxiCode"}


@dataclass
class VisualFinding:
    page: int
    rect: Rect
    label: str
    score: float
    source: str
    note: str = ""


def _rect(r: pymupdf.Rect) -> Rect:
    return (r.x0, r.y0, r.x1, r.y1)


def _render(page: pymupdf.Page, dpi: int, clip=None) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, clip=clip)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def codes(
    page: pymupdf.Page, img: np.ndarray | None = None, dpi: int = 200
) -> list[VisualFinding]:
    img = _render(page, dpi) if img is None else img
    s = img.shape[1] / page.rect.width  # px per point
    out = []
    for b in zxingcpp.read_barcodes(img):
        p = b.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        fmt = b.format.name
        label = "QRCODE" if fmt in _2D else "BARCODE"
        pad = 4 if label == "BARCODE" else 2
        rect = (
            min(xs) / s - pad,
            min(ys) / s - (14 if label == "BARCODE" else pad),
            max(xs) / s + pad,
            max(ys) / s + (14 if label == "BARCODE" else pad),
        )
        out.append(
            VisualFinding(
                page_number(page), rect, label, 1.0, "zxing", f"{fmt}: {b.text}"
            )
        )
    return out


def _bordered(ink: np.ndarray, band: float = 0.12, cover: float = 0.6) -> bool:
    """True when ink runs along at least three of the four edges (a stamp's frame).

    A signature's underline can cover one edge, never three.
    """
    h, w = ink.shape
    bh, bw = max(2, int(h * band)), max(2, int(w * band))
    edges = [
        ink[:bh].any(axis=0),
        ink[-bh:].any(axis=0),
        ink[:, :bw].any(axis=1),
        ink[:, -bw:].any(axis=1),
    ]
    return sum(float(e.mean()) >= cover for e in edges) >= 3


def _ink_class(img: np.ndarray) -> tuple[str, float]:
    """Classify a mark as STAMP (framed) or SIGNATURE; returns the label and the ink fraction."""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    ink = (hsv[..., 2] < 200) | (hsv[..., 1] > 70)
    frac = float(ink.mean())
    if frac > 0.02 and _bordered(ink):
        return "STAMP", frac
    return "SIGNATURE", frac


def embedded_images(page: pymupdf.Page) -> list[VisualFinding]:
    out = []
    W, H = page.rect.width, page.rect.height
    for info in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.is_empty or r.width * r.height > 0.5 * W * H:
            continue  # page-sized background = scanned page, handled elsewhere
        if r.y1 < 0.12 * H and r.width < 0.35 * W:
            continue  # header logo
        img = _render(page, 200, clip=r)
        found = zxingcpp.read_barcodes(img)
        if found:
            fmt = found[0].format.name
            out.append(
                VisualFinding(
                    page_number(page),
                    (r.x0, r.y0, r.x1, r.y1),
                    "QRCODE" if fmt in _2D else "BARCODE",
                    1.0,
                    "zxing",
                    found[0].text,
                )
            )
            continue
        label, _ = _ink_class(img)
        out.append(
            VisualFinding(
                page_number(page), (r.x0, r.y0, r.x1, r.y1), label, 0.8, "image"
            )
        )
    return out


def unexplained_ink(
    page: pymupdf.Page, words, dpi: int = 150, min_conf: float = 0.3
) -> list[VisualFinding]:
    """Ink blobs on a scanned page that are not OCR'd text, table rules or solid bands,
    plus stamp-sized blobs of coloured ink."""
    img = _render(page, dpi)
    s = dpi / 72
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    ink = (
        ((hsv[..., 2] < 190) | ((hsv[..., 1] > 80) & (hsv[..., 2] < 230))).astype(
            np.uint8
        )
    ) * 255
    # solid areas (header bands, filled cells, logos on bands) are layout, not marks
    k = int(6 * s)
    solid = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    )
    solid = cv2.dilate(
        solid, cv2.getStructuringElement(cv2.MORPH_RECT, (int(8 * s), int(8 * s)))
    )
    ink[solid > 0] = 0
    # remove text that OCR explains: confident words of normal text height
    heights = sorted(w.rect[3] - w.rect[1] for w in words) or [10]
    med_h = heights[len(heights) // 2]
    for w in words:
        h = w.rect[3] - w.rect[1]
        if w.conf >= min_conf and h <= 1.8 * med_h and len(w.text) >= 1:
            x0, y0, x1, y1 = w.rect
            cv2.rectangle(
                ink,
                (int(x0 * s) - 2, int(y0 * s) - 2),
                (int(x1 * s) + 2, int(y1 * s) + 2),
                0,
                -1,
            )
    # remove long horizontal / vertical rules (tables, separators)
    for kk in ((int(40 * s), 1), (1, int(40 * s))):
        lines = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, kk)
        )
        ink[cv2.dilate(lines, np.ones((3, 3), np.uint8)) > 0] = 0
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # specks
    out = []
    for x, y, w, h, roi in _components(ink, s, (9, 5)):
        if not (25 <= w / s <= 320 and 10 <= h / s <= 160):
            continue
        density = float((roi > 0).mean())
        if density < 0.01 or density > 0.55:
            continue
        label, _ = _ink_class(img[y : y + h, x : x + w])
        m = 6  # strokes fade at the edges: cover a margin around the blob
        out.append(
            VisualFinding(
                page_number(page),
                (x / s - m, y / s - m, (x + w) / s + m, (y + h) / s + m),
                label,
                0.6,
                "ink",
                f"density {density:.2f}",
            )
        )
    # coloured stamps: saturated, not solid, stamp-sized
    col = (
        ((hsv[..., 1] > 70) & (hsv[..., 2] > 60) & (hsv[..., 2] < 235)).astype(np.uint8)
    ) * 255
    col[solid > 0] = 0
    col = cv2.morphologyEx(col, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    for x, y, w, h, roi in _components(col, s, (6, 6)):
        if 60 <= w / s <= 300 and 25 <= h / s <= 150 and float((roi > 0).mean()) > 0.03:
            r = pymupdf.Rect(x / s - 6, y / s - 6, (x + w) / s + 6, (y + h) / s + 6)
            n_words = sum(
                1
                for wd in words
                if r.contains(pymupdf.Rect(wd.rect).tl) and len(wd.text) >= 2
            )
            out.append(
                VisualFinding(
                    page_number(page),
                    (r.x0, r.y0, r.x1, r.y1),
                    "STAMP" if n_words >= 2 else "SIGNATURE",
                    0.7,
                    "colour",
                )
            )
    return out


def _components(mask: np.ndarray, s: float, grow_pt: tuple[int, int]):
    grouped = cv2.dilate(
        mask,
        cv2.getStructuringElement(
            cv2.MORPH_RECT, (int(grow_pt[0] * s), int(grow_pt[1] * s))
        ),
    )
    n, _, stats, _ = cv2.connectedComponentsWithStats(grouped)
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        yield x, y, w, h, mask[y : y + h, x : x + w]


_STAMP_ANCHOR = re.compile(r"omceo|chirurgo|ordine|timbro|iscr", re.I)


def stamp_from_text(page_no: int, words) -> list[VisualFinding]:
    """A tight cluster of words around 'OMCeO' / 'Medico Chirurgo' is a stamp: redact it whole."""
    out = []
    used = set()
    for i, w in enumerate(words):
        if i in used or not _STAMP_ANCHOR.search(w.text):
            continue
        region = pymupdf.Rect(w.rect)
        members = {i}
        grew = True
        while grew:
            grew = False
            near = region + (-30, -14, 30, 14)
            for j, v in enumerate(words):
                if j not in members and near.intersects(pymupdf.Rect(v.rect)):
                    cand = region | pymupdf.Rect(v.rect)
                    if cand.width <= 230 and cand.height <= 110:
                        region, grew = cand, True
                        members.add(j)
        used |= members
        if len(members) >= 3:
            out.append(
                VisualFinding(
                    page_no, _rect(region + (-6, -6, 6, 6)), "STAMP", 0.75, "stamp-text"
                )
            )
    return out
