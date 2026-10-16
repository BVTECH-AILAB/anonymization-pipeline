"""OCR behind one interface: page -> words with boxes in PDF points.

``RapidOcrEngine`` runs PaddleOCR's PP-OCRv5 models (detection + Latin recognition)
through RapidOCR, without the Paddle framework: the text detector on PyTorch
when a GPU is available (ONNX Runtime otherwise), the recogniser on ONNX
Runtime. A pool of engines can read several pages at a time. It gives
word-level boxes, which surgical redaction needs. Chosen by measurement on the
scanned evaluation set: against docTR (db_resnet50 + parseq) it misses one
value in five fewer at the same precision (docs/ANONYMIZATION.md, "OCR choice").

The engine is built by ``load()`` (called once from the app lifespan) or a
reader is injected, so unit tests never load model weights.
"""

import logging
import queue
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import pymupdf
from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

from anonmyzation_pipeline.services.anonymization.device import resolve_device
from anonmyzation_pipeline.services.anonymization.layers import Rect, Word

logger = logging.getLogger(__name__)

# line ids of the words read in the left and right margin strips (never shared with the page)
MARGIN_LINES = (10_000, 20_000)
# a page image -> words as (text, confidence, box in pixels, line number)
Reader = Callable[[np.ndarray], list[tuple[str, float, Rect, int]]]


class OcrEngine(Protocol):
    """Anything that reads the words of a rendered page."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def pages_words(
        self,
        pages: Sequence[pymupdf.Page],
        done: Callable[[int], None] | None = None,
    ) -> list[list[Word]]: ...


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


def parse_words(lines: Any) -> list[tuple[str, float, Rect, int]]:
    """RapidOCR word results -> ``(text, conf, box in pixels, line number)``.

    Args:
        lines: Per line, ``(text, confidence, quadrilateral)`` for every word. An
            image without text gives empty entries or a bare ``("", 1.0, None)``
            placeholder: anything that is not a word with a box is skipped.

    Returns:
        One tuple per word, with the axis-aligned box of its quadrilateral.
    """
    out: list[tuple[str, float, Rect, int]] = []
    for line_no, line in enumerate(lines):
        if not isinstance(line, (list, tuple)):
            continue
        for w in line:
            if not (
                isinstance(w, (list, tuple))
                and len(w) == 3
                and w[0]
                and w[2] is not None
            ):
                continue
            text, conf, quad = w
            xs = [float(p[0]) for p in quad]
            ys = [float(p[1]) for p in quad]
            out.append(
                (str(text), float(conf), (min(xs), min(ys), max(xs), max(ys)), line_no)
            )
    return out


class RapidOcrEngine:
    """PaddleOCR PP-OCRv5 (via RapidOCR): word-level boxes, Latin alphabet.

    Vertical text in the side margins (print traces, wristband labels) is read
    by a second pass on the margin strips rotated by 90 degrees both ways.

    Args:
        dpi: Rendering resolution of the page.
        margin_pt: Width of the margin strips read again rotated (0 = off).
        model_type: PP-OCRv5 text detector, ``server`` (more accurate, the default)
            or ``mobile`` (about twice as fast); recognition is the Latin mobile
            model either way, the only one PP-OCRv5 has.
        model_dir: Folder of the models (empty: inside the package); they are
            downloaded there on first load (``just models``).
        device: ``auto``, ``cuda`` or ``cpu``: where the text detector runs.
        workers: Pages read at the same time, one engine each.
        reader: Injected reader (tests); no model is loaded.
    """

    def __init__(
        self,
        dpi: int = 300,
        margin_pt: float = 60,
        model_type: str = "server",
        model_dir: str = "",
        device: str = "auto",
        workers: int = 1,
        reader: Reader | None = None,
    ) -> None:
        self.dpi, self.margin_pt = dpi, margin_pt
        self.model_type, self.model_dir = model_type, model_dir
        self.device, self.workers = device, max(1, workers)
        self._reader = reader
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once the models are loaded."""
        return self._reader is not None

    def load(self) -> None:
        """Build the pool of RapidOCR engines (downloads the models on first use)."""
        with self._lock:
            if self._reader is not None:
                return
            device = resolve_device(self.device)
            kind = ModelType.SERVER if self.model_type == "server" else ModelType.MOBILE
            params: dict[str, Any] = {
                "Global.log_level": "warning",
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": kind,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,  # the only Latin recogniser of PP-OCRv5
                "Rec.lang_type": LangRec.LATIN,
            }
            if device == "cuda":
                # same detector weights on PyTorch; the Latin recogniser exists only as ONNX
                params["Det.engine_type"] = EngineType.TORCH
                params["EngineConfig.torch.use_cuda"] = True
            if self.model_dir:
                Path(self.model_dir).mkdir(parents=True, exist_ok=True)
                params["Global.model_root_dir"] = self.model_dir
            # one engine per worker: an engine runs one image at a time
            pool: queue.Queue[RapidOCR] = queue.Queue()
            for _ in range(self.workers):
                pool.put(RapidOCR(params=params))

            def read(img: np.ndarray) -> list[tuple[str, float, Rect, int]]:
                engine = pool.get()
                try:
                    res = engine(img, return_word_box=True)
                finally:
                    pool.put(engine)
                return parse_words(getattr(res, "word_results", None) or ())

            self._reader = read
            logger.info(
                "OCR model loaded: PP-OCRv5 %s detector=%s workers=%d",
                self.model_type,
                device,
                self.workers,
            )

    def _read(self, img: np.ndarray) -> list[tuple[str, float, Rect, int]]:
        """Words of an image as ``(text, conf, box in pixels, line number)``."""
        if self._reader is None:
            self.load()
        if self._reader is None:
            raise RuntimeError("OCR engine not available")
        return self._reader(img)

    def pages_words(
        self,
        pages: Sequence[pymupdf.Page],
        done: Callable[[int], None] | None = None,
    ) -> list[list[Word]]:
        """OCR several pages, ``workers`` at a time.

        Pages are rendered here, one by one (PyMuPDF is not thread-safe); only the
        renderings go to the workers, a batch of ``workers`` pages at a time so
        memory stays bounded.

        Args:
            pages: The pages to read.
            done: Called with the number of pages read so far.

        Returns:
            The words of every page, in order: boxes in PDF points, in display
            (rendered) space, including vertical text in the side margins.
        """
        out: list[list[Word]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            for i in range(0, len(pages), self.workers):
                batch = [
                    (render_page(p, self.dpi), p.rect.width)
                    for p in pages[i : i + self.workers]
                ]
                out += executor.map(lambda b: self._image_words(*b), batch)
                if done:
                    done(len(out))
        return out

    def _image_words(self, img: np.ndarray, page_width: float) -> list[Word]:
        """Words of a page rendering, in PDF points."""
        s = img.shape[1] / page_width
        words = [
            Word(t, (r[0] / s, r[1] / s, r[2] / s, r[3] / s), c, "ocr", ln)
            for t, c, r, ln in self._read(img)
        ]
        if self.margin_pt:
            words += self._margin_words(img, s)
        return words

    def _margin_words(self, img: np.ndarray, s: float) -> list[Word]:
        """Read the left and right margin strips rotated both ways; keep the more confident reading."""
        mw = min(int(self.margin_pt * s), img.shape[1] // 2)  # strips never overlap
        out = []
        for side, (x_off, strip) in enumerate(
            ((0, img[:, :mw]), (img.shape[1] - mw, img[:, -mw:]))
        ):
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
                if rot == cv2.ROTATE_90_CLOCKWISE:
                    # rotated (xr, yr) -> strip (yr, h - xr): the text runs bottom to top
                    sx0, sx1, sy0, sy1 = y0, y1, h - x1, h - x0
                    direction = "btt"
                else:  # counter-clockwise: (xr, yr) -> (mw - yr, xr): top to bottom
                    sx0, sx1, sy0, sy1 = mw - y1, mw - y0, x0, x1
                    direction = "ttb"
                rect = ((sx0 + x_off) / s, sy0 / s, (sx1 + x_off) / s, sy1 / s)
                # one line id range per strip: the two margins never share a line
                line = MARGIN_LINES[side] + ln
                out.append(Word(t, rect, c, "ocr-vertical", line, direction))
        return out
