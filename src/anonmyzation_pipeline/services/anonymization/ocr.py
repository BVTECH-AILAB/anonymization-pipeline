"""OCR behind one interface: page -> words with boxes in PDF points.

``DoctrOCR`` gives word-level boxes, which surgical redaction needs. The
predictor is built by ``load()`` (called once from the app lifespan) or
injected, so unit tests never load model weights.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np
import pymupdf
import torch
from doctr.models import ocr_predictor

logger = logging.getLogger(__name__)

Rect = tuple[float, float, float, float]


@dataclass
class Word:
    """One OCR word in PDF points (top-left origin)."""

    text: str
    rect: Rect
    conf: float = 1.0
    source: str = "native"
    line: int = 0


class OcrEngine(Protocol):
    """Anything that reads the words of a rendered page."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def page_words(self, page: pymupdf.Page) -> list[Word]: ...


def render_page(page: pymupdf.Page, dpi: int = 300) -> np.ndarray:
    """Render a page to an RGB array.

    Args:
        page: PDF page.
        dpi: Rendering resolution.

    Returns:
        ``(height, width, 3)`` uint8 array.
    """
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def resolve_device(device: str) -> str:
    """Map ``auto`` to ``cuda`` when a GPU is available, otherwise ``cpu``."""
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


class DoctrOCR:
    """docTR detection + recognition, word-level boxes.

    Pages are assumed near-upright (scans have a small skew): the per-crop
    orientation classifier is disabled because it flips short lines by 180
    degrees. Vertical text in the side margins is read by a second pass on
    the margin strips rotated by 90 degrees.
    """

    def __init__(
        self,
        det_arch: str = "db_resnet50",
        reco_arch: str = "parseq",
        dpi: int = 300,
        margin_pt: float = 60,
        device: str = "auto",
        predictor: Callable[[list[np.ndarray]], Any] | None = None,
    ) -> None:
        self.det_arch, self.reco_arch, self.dpi = det_arch, reco_arch, dpi
        self.margin_pt = margin_pt
        self.device = device
        self._predictor = predictor
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once the predictor is available."""
        return self._predictor is not None

    def load(self) -> None:
        """Build the docTR predictor (downloads weights on first use)."""
        with self._lock:
            if self._predictor is not None:
                return
            predictor = ocr_predictor(
                det_arch=self.det_arch,
                reco_arch=self.reco_arch,
                pretrained=True,
                assume_straight_pages=True,
                straighten_pages=False,
                export_as_straight_boxes=True,
                disable_crop_orientation=True,
                disable_page_orientation=True,
            )
            device = resolve_device(self.device)
            if device == "cuda":
                predictor = predictor.cuda()
            self._predictor = predictor
            logger.info(
                "OCR model loaded: det=%s reco=%s device=%s",
                self.det_arch,
                self.reco_arch,
                device,
            )

    def _read(self, img: np.ndarray) -> list[tuple[str, float, Rect, int]]:
        """Words of an image as ``(text, conf, box in pixels, line number)``."""
        if self._predictor is None:
            self.load()
        predictor = self._predictor
        if predictor is None:
            raise RuntimeError("OCR predictor not available")
        res = predictor([img])
        h, w = img.shape[:2]
        out, line_no = [], 0
        for block in res.pages[0].blocks:
            for line in block.lines:
                for wd in line.words:
                    (x0, y0), (x1, y1) = wd.geometry
                    out.append(
                        (
                            wd.value,
                            float(wd.confidence),
                            (x0 * w, y0 * h, x1 * w, y1 * h),
                            line_no,
                        )
                    )
                line_no += 1
        return out

    def page_words(self, page: pymupdf.Page) -> list[Word]:
        """OCR a page, including vertical text in the side margins.

        Args:
            page: The page to read.

        Returns:
            Words with boxes in PDF points.
        """
        img = render_page(page, self.dpi)
        s = img.shape[1] / page.rect.width
        words = [
            Word(t, (r[0] / s, r[1] / s, r[2] / s, r[3] / s), c, "doctr", ln)
            for t, c, r, ln in self._read(img)
        ]
        if self.margin_pt:
            words += self._margin_words(img, s)
        return words

    def _margin_words(self, img: np.ndarray, s: float) -> list[Word]:
        """Read the left and right margin strips rotated both ways; keep the more confident reading."""
        mw = int(self.margin_pt * s)
        out = []
        for x_off, strip in ((0, img[:, :mw]), (img.shape[1] - mw, img[:, -mw:])):
            h = strip.shape[0]
            best: list[tuple[str, float, Rect, int, int]] = []
            for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
                found = self._read(np.ascontiguousarray(cv2.rotate(strip, rot)))
                if sum(c * len(t) for t, c, *_ in found) > sum(
                    c * len(t) for t, c, *_ in best
                ):
                    best = [(t, c, r, ln, rot) for t, c, r, ln in found]
            for t, c, (x0, y0, x1, y1), ln, rot in best:
                if c < 0.5:
                    continue
                if (
                    rot == cv2.ROTATE_90_CLOCKWISE
                ):  # rotated (xr, yr) -> strip (yr, h - xr)
                    sx0, sx1, sy0, sy1 = y0, y1, h - x1, h - x0
                else:  # counter-clockwise: (xr, yr) -> (mw - yr, xr)
                    sx0, sx1, sy0, sy1 = mw - y1, mw - y0, x0, x1
                rect = ((sx0 + x_off) / s, sy0 / s, (sx1 + x_off) / s, sy1 / s)
                out.append(Word(t, rect, c, "doctr-vertical", 10000 + ln))
        return out
