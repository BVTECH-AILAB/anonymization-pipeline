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
import zxingcpp

from anonmyzation_pipeline.services.anonymization.geometry import (
    Rect,
    contains_point,
    grow,
    intersection,
    intersects,
    is_empty,
    to_display,
    union,
)
from anonmyzation_pipeline.services.anonymization.pdf_pages import PageView

# share of ink pixels above which an image is a picture, not marks on paper
PICTURE_INK = 0.5
# mean HSV saturation above which a picture is in colour (a photo, not a clinical image)
COLOUR_SATURATION = 40
_2D = {"QRCode", "MicroQRCode", "RMQRCode", "DataMatrix", "Aztec", "PDF417", "MaxiCode"}


@dataclass
class VisualFinding:
    page: int
    rect: Rect
    label: str
    score: float
    source: str
    note: str = ""


def codes(
    page: PageView, img: np.ndarray | None = None, dpi: int = 200
) -> list[VisualFinding]:
    """Barcodes and QR codes decoded on the rendered page (display space)."""
    img = page.render(dpi) if img is None else img
    s = img.shape[1] / page.width  # px per point
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
            VisualFinding(page.number, rect, label, 1.0, "zxing", f"{fmt}: {b.text}")
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


def _image_regions(page: PageView) -> list[Rect]:
    """Embedded images in page space, without page-sized backgrounds and header logos."""
    W, H = page.page_width, page.page_height
    out = []
    for placed in page.images():
        r = intersection(placed, (0.0, 0.0, W, H))
        if is_empty(r) or (r[2] - r[0]) * (r[3] - r[1]) > 0.5 * W * H:
            continue  # page-sized background = scanned page, handled elsewhere
        if r[3] < 0.12 * H and (r[2] - r[0]) < 0.35 * W:
            continue  # header logo
        out.append(r)
    return out


def _is_picture(img: np.ndarray) -> bool:
    """A photographic image (X-ray, ultrasound, photo, embedded scan): mostly ink, not marks on paper.

    Stamps and signatures are a little ink on a white background.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    return float(((hsv[..., 2] < 200) | (hsv[..., 1] > 70)).mean()) > PICTURE_INK


def _picture_kind(page: PageView, r: Rect) -> bool | None:
    """``None`` for marks on paper, else ``True`` for a colour picture, ``False`` for a grayscale one.

    The one decision shared by ``pictures`` (OCR of grayscale pictures) and
    ``embedded_images`` (redaction of the rest): taken once, at one resolution, an
    image is never left both unread and unredacted.
    """
    img = page.render(72, clip=to_display(r, page.info))  # clip in display space
    if not _is_picture(img):
        return None
    sat = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[..., 1]
    return float(sat.mean()) > COLOUR_SATURATION


def pictures(page: PageView) -> list[tuple[Rect, bool]]:
    """Photographic images of a native page in page space, each with ``True`` when in colour.

    A grayscale picture is a clinical image (X-ray, CT, ultrasound, scan): it stays
    visible and the text burned into it is read by OCR. A colour picture may show a
    person: it is redacted whole (``embedded_images`` labels it FACE).
    """
    out = []
    for r in _image_regions(page):
        kind = _picture_kind(page, r)
        if kind is not None:
            out.append((r, kind))
    return out


def embedded_images(page: PageView) -> list[VisualFinding]:
    """Codes, signatures, stamps and colour photos among the embedded images of a native page.

    Grayscale clinical images are left to OCR (``pictures``): their burned-in text is
    redacted, the image is not.
    """
    out = []
    for r in _image_regions(page):
        img = page.render(200, clip=to_display(r, page.info))  # clip in display space
        found = zxingcpp.read_barcodes(img)
        if found:
            fmt = found[0].format.name
            out.append(
                VisualFinding(
                    page.number,
                    r,
                    "QRCODE" if fmt in _2D else "BARCODE",
                    1.0,
                    "zxing",
                    found[0].text,
                )
            )
            continue
        kind = _picture_kind(page, r)
        if kind is not None:
            if kind:  # a colour photo: it may show a person
                out.append(VisualFinding(page.number, r, "FACE", 0.6, "image"))
            continue
        label, _ = _ink_class(img)
        out.append(VisualFinding(page.number, r, label, 0.8, "image"))
    return out


def vector_marks(page: PageView) -> list[VisualFinding]:
    """Signatures and stamps drawn as vector paths on a native page.

    A handwritten signature is an open stroke made of curves; a stamp frame is a
    thick stroked rounded rectangle or ellipse (often doubled). Filled curves are
    logos, rectangles are tables and form fields: both are ignored.
    """
    sigs: list[Rect] = []
    stamps: list[Rect] = []
    for d in page.drawings():
        if d.filled or not d.stroked:
            continue
        kinds = d.kinds
        r = d.rect
        width, height = r[2] - r[0], r[3] - r[1]
        curves = kinds.count("c")
        if not curves:
            continue
        if curves >= 3 and curves >= 0.75 * len(kinds) and "re" not in kinds:
            if 20 <= width <= 320 and 6 <= height <= 120 and width > 1.2 * height:
                sigs.append(r)
                continue
        frame = ("l" in kinds and curves >= 4) or (curves == 4 and len(kinds) <= 5)
        if frame and d.width >= 0.45 and 40 <= width <= 340 and 20 <= height <= 220:
            stamps.append(r)
    out = []
    for label, rects, pad in (("SIGNATURE", sigs, 3), ("STAMP", stamps, 2)):
        merged: list[Rect] = []
        for r in sorted(rects, key=lambda r: (r[0], r[1])):
            for i, m in enumerate(merged):
                if intersects(grow(m, 6), r):
                    merged[i] = union([m, r]) or m
                    break
            else:
                merged.append(r)
        out += [
            VisualFinding(page.number, grow(m, pad), label, 0.85, "vector")
            for m in merged
        ]
    return out


def unexplained_ink(
    page: PageView, words, dpi: int = 150, min_conf: float = 0.3
) -> list[VisualFinding]:
    """Ink blobs on a scanned page that are not OCR'd text, table rules or solid bands,
    plus stamp-sized blobs of coloured ink."""
    img = page.render(dpi)
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
                page.number,
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
            r = (x / s - 6, y / s - 6, (x + w) / s + 6, (y + h) / s + 6)
            n_words = sum(
                1
                for wd in words
                if contains_point(r, wd.rect[0], wd.rect[1]) and len(wd.text) >= 2
            )
            out.append(
                VisualFinding(
                    page.number,
                    r,
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
        region: Rect = w.rect
        members = {i}
        grew = True
        while grew:
            grew = False
            near = (region[0] - 30, region[1] - 14, region[2] + 30, region[3] + 14)
            for j, v in enumerate(words):
                if j not in members and intersects(near, v.rect):
                    cand = union([region, v.rect]) or region
                    if cand[2] - cand[0] <= 230 and cand[3] - cand[1] <= 110:
                        region, grew = cand, True
                        members.add(j)
        used |= members
        if len(members) >= 3:
            out.append(
                VisualFinding(page_no, grow(region, 6), "STAMP", 0.75, "stamp-text")
            )
    return out
