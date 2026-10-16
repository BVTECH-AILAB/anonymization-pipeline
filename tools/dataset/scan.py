"""Turn a native PDF into a realistic scan: raster pages with skew, paper tint,
noise, blur and JPEG artefacts. Optionally adds an invisible OCR text layer,
as scanner software does. Ground-truth boxes follow the same transform."""

import copy
import random
from pathlib import Path

import cv2
import numpy as np
import pymupdf


def _transform_rect(rect, M, s: float) -> list[float]:
    x0, y0, x1, y1 = (v * s for v in rect)
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]]) @ M.T
    return [
        round(float(pts[:, 0].min()) / s, 2),
        round(float(pts[:, 1].min()) / s, 2),
        round(float(pts[:, 0].max()) / s, 2),
        round(float(pts[:, 1].max()) / s, 2),
    ]


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


def make_scan(
    native: Path, gt: dict, out_path: Path, rnd: random.Random, ocr_layer: bool
) -> dict:
    """Write a scanned twin of ``native`` and return its ground truth.

    Ground-truth boxes (and the words of the optional OCR layer) are in the page
    space of the native file; the scan is made from the rendered, possibly
    rotated, page, so each box goes through the page rotation and then through
    the scanner's skew.

    Returns:
        The ground truth with moved boxes and a ``scan`` entry (dpi, OCR layer,
        per-page affine transforms).
    """
    src = pymupdf.open(native)
    rotations = [page.rotation_matrix for page in src]  # page space -> rendering
    out = pymupdf.open()
    dpi = rnd.choice([200, 240, 300])
    s = dpi / 72
    transforms = []
    sizes = []
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rnd.uniform(-1.6, 1.6), 1.0)
        M[:, 2] += (rnd.uniform(-0.01, 0.01) * w, rnd.uniform(-0.01, 0.01) * h)
        img = cv2.warpAffine(
            img, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(250, 250, 248)
        )
        img = _degrade(img, rnd)
        ok, jpg = cv2.imencode(
            ".jpg",
            cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, rnd.randint(55, 85)],
        )
        np_page = out.new_page(width=w / s, height=h / s)
        sizes.append((np_page.rect.width, np_page.rect.height))
        np_page.insert_image(np_page.rect, stream=jpg.tobytes())
        if ocr_layer:
            for x0, y0, x1, y1, word, *_ in page.get_text("words"):
                shown = pymupdf.Rect(x0, y0, x1, y1) * page.rotation_matrix
                r = _transform_rect(tuple(shown), M, s)
                size = max(2.0, (r[3] - r[1]) * 0.8)
                np_page.insert_text(
                    (r[0], r[3] - (r[3] - r[1]) * 0.2),
                    word,
                    fontsize=size,
                    render_mode=3,
                )
        transforms.append(M)
    out.set_metadata({"producer": "ScanSnap Manager 7.2", "creator": "ScanSnap"})
    out.save(out_path, garbage=3, deflate=True)
    out.close()
    src.close()

    sgt = copy.deepcopy(gt)
    for p, (width, height) in zip(sgt["pages"], sizes, strict=True):
        p["width"], p["height"] = width, height
    for m in sgt["mentions"]:
        if m["kind"] in ("metadata", "annotation"):
            m["kind"] = "dropped"  # not carried into a scan
        for b in m["boxes"]:
            shown = tuple(pymupdf.Rect(b["rect"]) * rotations[b["page"]])
            moved = pymupdf.Rect(_transform_rect(shown, transforms[b["page"]], s))
            # skew can push a box at the edge partly outside the scanned page
            page = pymupdf.Rect(0, 0, *sizes[b["page"]])
            b["rect"] = [round(v, 2) for v in moved & page]
    for k in sgt["keep"]:
        shown = tuple(pymupdf.Rect(k["rect"]) * rotations[k["page"]])
        k["rect"] = _transform_rect(shown, transforms[k["page"]], s)
    # per-page affine transform (pixels at `dpi`), to map native coordinates onto the scan
    sgt["scan"] = {
        "dpi": dpi,
        "ocr_layer": ocr_layer,
        "transforms": [m.tolist() for m in transforms],
    }
    return sgt
