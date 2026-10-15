"""OCR of scanned pages: rendered page -> words with boxes in display space.

docTR (Apache-2.0, weights from the docTR release, Apache-2.0) gives word-level boxes, which the
redaction needs. Pages are assumed near-upright (scans have a small skew): the per-crop
orientation classifier is disabled because it flips short lines by 180 degrees. Vertical text in
the side margins is read by a second pass on the margin strips rotated by 90 degrees both ways;
the more confident reading is kept.

The predictor is built once by the application lifespan (``load_engine``) and shared; tests pass
a fake predictor. The page is rendered under ``PDFIUM_LOCK`` by the caller; the inference runs
outside it.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.pdf.geometry import Rect
from anonymization_pipeline.pipeline_config.schema import Ocr

try:
    import torch
    from doctr.models import ocr_predictor

    OCR_INSTALLED = True
except ImportError:  # image without the 'ocr' extra
    OCR_INSTALLED = False

logger = logging.getLogger(__name__)

Direction = Literal["ltr", "btt", "ttb"]  # left to right, bottom to top, top to bottom
# line numbers of the words read in the left and right margin strips (never shared with the page)
LEFT_MARGIN_LINES = 10_000
RIGHT_MARGIN_LINES = 20_000


@dataclass(frozen=True)
class Word:
    """One OCR word in display space (points, top-left origin)."""

    text: str
    rect: Rect
    conf: float
    line: int
    direction: Direction = "ltr"


# image -> words as (text, confidence, box in pixels, line number)
Reading = list[tuple[str, float, Rect, int]]


class Engine(Protocol):
    """A loaded OCR model (not thread-safe: the caller holds ``lock``)."""

    lock: threading.Lock

    def read(self, image: np.ndarray) -> Reading: ...


class DoctrEngine:
    """A docTR predictor: page image -> words with pixel boxes."""

    def __init__(self, predictor: Callable[[list[np.ndarray]], Any]) -> None:
        self._predictor = predictor
        self.lock = threading.Lock()

    def read(self, image: np.ndarray) -> Reading:
        result = self._predictor([image])
        h, w = image.shape[:2]
        out: Reading = []
        line = 0
        for block in result.pages[0].blocks:
            for row in block.lines:
                for word in row.words:
                    (x0, y0), (x1, y1) = word.geometry
                    out.append(
                        (
                            word.value,
                            float(word.confidence),
                            (x0 * w, y0 * h, x1 * w, y1 * h),
                            line,
                        )
                    )
                line += 1
        return out


def load_engine(cfg: Ocr) -> Engine:
    """Build the docTR predictor from its cached weights.

    Raises:
        DetectorUnavailable: Library or weights missing, or the model cannot be built.
    """
    if not OCR_INSTALLED:
        raise DetectorUnavailable(
            "OCR libraries not installed (image without the 'ocr' extra)"
        )
    try:
        if cfg.threads and torch.get_num_threads() != cfg.threads:
            torch.set_num_threads(cfg.threads)
        predictor = ocr_predictor(
            det_arch=cfg.det_arch,
            reco_arch=cfg.reco_arch,
            pretrained=True,
            assume_straight_pages=True,
            straighten_pages=False,
            export_as_straight_boxes=True,
            disable_crop_orientation=True,
            disable_page_orientation=True,
        )
    except Exception as exc:  # missing weights offline, unknown architecture...
        raise DetectorUnavailable(
            f"OCR model cannot be loaded: {type(exc).__name__}"
        ) from exc
    logger.info("OCR model loaded: det=%s reco=%s", cfg.det_arch, cfg.reco_arch)
    return DoctrEngine(predictor)


def _margin_words(
    engine: Engine, image: np.ndarray, scale: float, cfg: Ocr
) -> list[Word]:
    """Read the left and right margin strips rotated both ways; keep the more confident reading.

    A strip readable once rotated clockwise holds text written bottom to top, one readable once
    rotated counter-clockwise text written top to bottom.
    """
    width = int(cfg.margin_pt * scale)
    if width <= 0:
        return []
    out: list[Word] = []
    strips = (
        (0, image[:, :width], LEFT_MARGIN_LINES),
        (image.shape[1] - width, image[:, -width:], RIGHT_MARGIN_LINES),
    )
    for x_off, strip, base in strips:
        h, w = strip.shape[:2]
        best: Reading = []
        best_k = 0
        for k in (-1, 1):  # numpy rot90: -1 clockwise, 1 counter-clockwise
            found = engine.read(np.ascontiguousarray(np.rot90(strip, k)))
            if sum(c * len(t) for t, c, _, _ in found) > sum(
                c * len(t) for t, c, _, _ in best
            ):
                best, best_k = found, k
        for text, conf, (x0, y0, x1, y1), line in best:
            if conf < cfg.margin_min_conf:
                continue
            if best_k == -1:  # clockwise: rotated (xr, yr) -> strip (yr, h - xr)
                sx0, sx1, sy0, sy1 = y0, y1, h - x1, h - x0
                direction: Direction = "btt"
            else:  # counter-clockwise: rotated (xr, yr) -> strip (w - yr, xr)
                sx0, sx1, sy0, sy1 = w - y1, w - y0, x0, x1
                direction = "ttb"
            rect = (
                (sx0 + x_off) / scale,
                sy0 / scale,
                (sx1 + x_off) / scale,
                sy1 / scale,
            )
            out.append(Word(text, rect, conf, base + line, direction))
    return out


def page_words(
    engine: Engine, image: np.ndarray, page_width: float, cfg: Ocr
) -> list[Word]:
    """Words of a rendered page, the rotated margins included.

    Args:
        engine: The OCR model loaded at startup.
        image: The page rendered at ``cfg.dpi`` (RGB array).
        page_width: Display width of the page in points.
        cfg: OCR configuration.
    """
    scale = image.shape[1] / page_width
    with engine.lock:
        words = [
            Word(
                text,
                (r[0] / scale, r[1] / scale, r[2] / scale, r[3] / scale),
                conf,
                line,
            )
            for text, conf, r, line in engine.read(image)
        ]
        words += _margin_words(engine, image, scale, cfg)
    return words
