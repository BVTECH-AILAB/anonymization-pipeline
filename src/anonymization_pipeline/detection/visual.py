"""Visual personal data: signatures, stamps, barcodes and QR codes, as boxes to redact.

- every page: 1D and 2D codes read by zxing on the rendered page (vector or raster);
- native pages: embedded raster images (except header logos) are decoded as codes, otherwise
  redacted whole as a stamp (ink along the frame) or a signature;
- scanned pages: a cluster of OCR words around "OMCeO", "Medico Chirurgo", "timbro" is a stamp;
  with the ``ocr`` extra (OpenCV), marks the OCR does not read as text (not table rules, not solid
  bands) are signatures or stamps.

Works on images rendered by the caller under ``PDFIUM_LOCK`` and on image bounds in display
space: no PDF handle is used here.
"""

import re
from collections.abc import Iterator

import numpy as np
import zxingcpp
from PIL import Image

from anonymization_pipeline.ocr import Word
from anonymization_pipeline.pdf.geometry import Rect, grow, intersects, union
from anonymization_pipeline.render.boxes import Box

try:
    import cv2

    CV2_INSTALLED = True
except ImportError:  # image without the 'ocr' extra
    CV2_INSTALLED = False

_2D = {"QRCode", "MicroQRCode", "RMQRCode", "DataMatrix", "Aztec", "PDF417", "MaxiCode"}
HEADER_FRACTION = (
    0.12  # images whose bottom is above this fraction of the page height...
)
LOGO_WIDTH = 0.35  # ...and narrower than this fraction of its width are header logos
SCAN_FRACTION = 0.5  # images over this fraction of the page are the scan itself
_STAMP_ANCHOR = re.compile(r"omceo|chirurgo|ordine|timbro|iscr", re.I)


def _code_category(fmt: str) -> str:
    return "QR_CODE" if fmt in _2D else "CODICE_A_BARRE"


def codes(page: int, image: np.ndarray, scale: float) -> list[Box]:
    """Barcodes and QR codes on a rendered page (``scale``: pixels per point)."""
    out = []
    for found in zxingcpp.read_barcodes(image):
        p = found.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        category = _code_category(found.format.name)
        # a barcode often has its digits printed under or above it
        dy = 14 if category == "CODICE_A_BARRE" else 2
        dx = 4 if category == "CODICE_A_BARRE" else 2
        rect = (
            min(xs) / scale - dx,
            min(ys) / scale - dy,
            max(xs) / scale + dx,
            max(ys) / scale + dy,
        )
        out.append(Box(page, rect, category, "visual"))
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


def ink_category(image: np.ndarray) -> str:
    """TIMBRO for a framed mark, FIRMA otherwise."""
    hsv = np.asarray(Image.fromarray(image).convert("HSV"))
    ink = (hsv[..., 2] < 200) | (hsv[..., 1] > 70)
    if float(ink.mean()) > 0.02 and _bordered(ink):
        return "TIMBRO"
    return "FIRMA"


def embedded_images(
    page: int,
    image: np.ndarray,
    scale: float,
    size: tuple[float, float],
    bounds: list[Rect],
) -> list[Box]:
    """Raster images of a native page: a code, or a whole stamp or signature.

    Args:
        page: Page number.
        image: The page rendered at ``scale`` pixels per point.
        scale: Pixels per point.
        size: Display width and height of the page.
        bounds: Display rectangles of the raster images of the page.
    """
    width, height = size
    out = []
    for x0, y0, x1, y1 in bounds:
        x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(width, x1), min(height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        if (x1 - x0) * (y1 - y0) > SCAN_FRACTION * width * height:
            continue  # the scan itself: read by OCR
        if y1 < HEADER_FRACTION * height and (x1 - x0) < LOGO_WIDTH * width:
            continue  # header logo
        crop = image[
            int(y0 * scale) : int(y1 * scale) + 1, int(x0 * scale) : int(x1 * scale) + 1
        ]
        if crop.size == 0:
            continue
        found = zxingcpp.read_barcodes(crop)
        category = _code_category(found[0].format.name) if found else ink_category(crop)
        out.append(Box(page, (x0, y0, x1, y1), category, "visual"))
    return out


def stamp_from_text(page: int, words: list[Word]) -> list[Box]:
    """A tight cluster of at least 3 OCR words around 'OMCeO', 'Medico Chirurgo', 'timbro' is a
    stamp: redacted whole."""
    out = []
    used: set[int] = set()
    for i, word in enumerate(words):
        if i in used or not _STAMP_ANCHOR.search(word.text):
            continue
        region = word.rect
        members = {i}
        grew = True
        while grew:
            grew = False
            near = grow(region, 30, 14)
            for j, other in enumerate(words):
                if j in members or not intersects(near, other.rect):
                    continue
                candidate = union(region, other.rect)
                if (
                    candidate[2] - candidate[0] <= 230
                    and candidate[3] - candidate[1] <= 110
                ):
                    region, grew = candidate, True
                    members.add(j)
        used |= members
        if len(members) >= 3:
            out.append(Box(page, grow(region, 6), "TIMBRO", "visual"))
    return out


def _components(
    mask: np.ndarray, scale: float, grow_pt: tuple[int, int]
) -> Iterator[tuple[int, int, int, int, np.ndarray]]:
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (int(grow_pt[0] * scale), int(grow_pt[1] * scale))
    )
    n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, kernel))
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        yield int(x), int(y), int(w), int(h), mask[y : y + h, x : x + w]


def unexplained_ink(
    page: int, image: np.ndarray, scale: float, words: list[Word], min_conf: float = 0.3
) -> list[Box]:
    """Marks on a scanned page that are not OCR'd text, table rules or solid bands (signatures,
    stamps), plus stamp-sized marks of coloured ink. Needs OpenCV (``ocr`` extra)."""
    if not CV2_INSTALLED:
        return []
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    ink = (
        ((hsv[..., 2] < 190) | ((hsv[..., 1] > 80) & (hsv[..., 2] < 230))).astype(
            np.uint8
        )
    ) * 255
    # solid areas (header bands, filled cells) are layout, not marks
    k = int(6 * scale)
    solid = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    )
    solid = cv2.dilate(
        solid,
        cv2.getStructuringElement(cv2.MORPH_RECT, (int(8 * scale), int(8 * scale))),
    )
    ink[solid > 0] = 0
    # text the OCR explains: confident words of normal height
    heights = sorted(w.rect[3] - w.rect[1] for w in words) or [10.0]
    median = heights[len(heights) // 2]
    for w in words:
        if w.conf >= min_conf and w.rect[3] - w.rect[1] <= 1.8 * median:
            x0, y0, x1, y1 = w.rect
            cv2.rectangle(
                ink,
                (int(x0 * scale) - 2, int(y0 * scale) - 2),
                (int(x1 * scale) + 2, int(y1 * scale) + 2),
                0,
                -1,
            )
    # long horizontal and vertical rules (tables, separators)
    for size in ((int(40 * scale), 1), (1, int(40 * scale))):
        lines = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size)
        )
        ink[cv2.dilate(lines, np.ones((3, 3), np.uint8)) > 0] = 0
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    out = []
    for x, y, w, h, roi in _components(ink, scale, (9, 5)):
        if not (25 <= w / scale <= 320 and 10 <= h / scale <= 160):
            continue
        density = float((roi > 0).mean())
        if density < 0.01 or density > 0.55:
            continue
        margin = 6  # strokes fade at the edges
        rect = (
            x / scale - margin,
            y / scale - margin,
            (x + w) / scale + margin,
            (y + h) / scale + margin,
        )
        out.append(Box(page, rect, ink_category(image[y : y + h, x : x + w]), "visual"))
    # coloured stamps: saturated, not solid, stamp-sized
    colour = (
        ((hsv[..., 1] > 70) & (hsv[..., 2] > 60) & (hsv[..., 2] < 235)).astype(np.uint8)
    ) * 255
    colour[solid > 0] = 0
    colour = cv2.morphologyEx(colour, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    for x, y, w, h, roi in _components(colour, scale, (6, 6)):
        if (
            60 <= w / scale <= 300
            and 25 <= h / scale <= 150
            and float((roi > 0).mean()) > 0.03
        ):
            rect = (
                x / scale - 6,
                y / scale - 6,
                (x + w) / scale + 6,
                (y + h) / scale + 6,
            )
            words_inside = sum(
                1
                for word in words
                if rect[0] <= word.rect[0] <= rect[2]
                and rect[1] <= word.rect[1] <= rect[3]
                and len(word.text) >= 2
            )
            out.append(
                Box(page, rect, "TIMBRO" if words_inside >= 2 else "FIRMA", "visual")
            )
    return out


def dedupe(boxes: list[Box]) -> list[Box]:
    """Codes win over images, images over ink: a box mostly covered by a stronger one is dropped."""
    rank = {"QR_CODE": 0, "CODICE_A_BARRE": 0, "TIMBRO": 1, "FIRMA": 2}
    out: list[Box] = []
    for box in sorted(boxes, key=lambda b: rank.get(b.category, 9)):
        area = (box.rect[2] - box.rect[0]) * (box.rect[3] - box.rect[1])
        covered = any(
            o.page == box.page and _overlap(o.rect, box.rect) > 0.5 * area for o in out
        )
        if not covered:
            out.append(box)
    return out


def _overlap(a: Rect, b: Rect) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)
