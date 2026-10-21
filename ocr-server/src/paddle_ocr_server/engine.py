"""PP-OCRv5 on RapidOCR (ONNX Runtime): page and margin passes, one result per word.

A page read with low confidence is read again turned by 180 degrees (a sheet fed
upside down). On an upright page, a line read as noise is read again turned (a
label pasted upside down); the other noise lines are dropped.
"""

import ctypes
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime
from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

from paddle_ocr_server.config import Settings
from paddle_ocr_server.words import (
    Line,
    Quad,
    line_words,
    results,
    rotate_back,
    to_result,
)

logger = logging.getLogger(__name__)

Predict = Callable[[np.ndarray], list[Line]]
# a word image -> (text, confidence), recognition only
Recognize = Callable[[np.ndarray], tuple[str, float]]

# below this mean confidence per character the page is read again turned by 180
# degrees; the turned reading must reach it to be kept
UPSIDE_DOWN_CONF = 0.8
# a line read below this confidence is noise, or text upside down
LINE_CONF = 0.5
# least confidence of a line read again turned by 180 degrees
TURNED_LINE_CONF = 0.8


def _quad(points: Any) -> Quad:
    p = [(float(x), float(y)) for x, y in np.asarray(points).reshape(4, 2)]
    return (p[0], p[1], p[2], p[3])


def _weight(lines: list[Line]) -> float:
    """Confidence summed over the characters read: more text read surely weighs more."""
    return sum(line.score * len(line.text) for line in lines)


def _mean_conf(lines: list[Line]) -> float:
    """Mean confidence per character read (0 when nothing is read)."""
    n = sum(len(line.text) for line in lines)
    return _weight(lines) / n if n else 0.0


Box = tuple[float, float, float, float]


def _box(quad: Quad) -> Box:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (min(xs), min(ys), max(xs), max(ys))


def _same_band(a: Box, b: Box) -> bool:
    """Two pieces of one line: on the same band, at most two heights apart."""
    h = min(a[3] - a[1], b[3] - b[1])
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    gap = max(a[0], b[0]) - min(a[2], b[2])
    return overlap >= 0.5 * h and gap <= 2 * h


def _turn_quad(quad: Quad, w: int, h: int) -> Quad:
    """A quadrilateral read on the image turned by 180 degrees, on the image.

    The point order is kept: TL -> TR now runs right to left, the reading frame
    of text upside down.
    """
    p = [(w - x, h - y) for x, y in quad]
    return (p[0], p[1], p[2], p[3])


def _turn_back(lines: list[Line], w: int, h: int) -> list[Line]:
    return [
        Line(
            line.text,
            line.score,
            _turn_quad(line.quad, w, h),
            [(t, _turn_quad(q, w, h)) for t, q in line.words],
        )
        for line in lines
    ]


def turned_line(
    img: np.ndarray, pieces: list[Line], recognize: Recognize
) -> Line | None:
    """The pieces of a line read again as one crop turned by 180 degrees, or None.

    Returns:
        One line over the box of the pieces, in the reading frame of text upside
        down, when the reading is confident.
    """
    boxes = [_box(p.quad) for p in pieces]
    bx0, by0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    bx1, by1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    x0, y0 = int(max(0, bx0 - 2)), int(max(0, by0 - 2))
    x1, y1 = int(min(img.shape[1], bx1 + 2)), int(min(img.shape[0], by1 + 2))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = np.ascontiguousarray(cv2.rotate(img[y0:y1, x0:x1], cv2.ROTATE_180))
    text, conf = recognize(crop)
    text = text.strip()
    if conf < TURNED_LINE_CONF or not text:
        return None
    # upside down the reading runs from the right edge of the box to the left
    return Line(text, conf, ((bx1, by1), (bx0, by1), (bx0, by0), (bx1, by0)))


def settle_lines(
    img: np.ndarray, lines: list[Line], recognize: Recognize | None
) -> list[Line]:
    """Keep the lines read with confidence; read the others again turned by 180 degrees.

    A line below ``LINE_CONF`` is noise or text upside down. The detector cuts a
    line upside down into pieces, some read just above that threshold: with a
    recogniser, every low line is grouped with the unsure pieces next to it on
    its band (below ``UPSIDE_DOWN_CONF``) and the group is read again turned, as
    one crop. A confident reading takes the place of the whole group; otherwise
    the low lines are dropped and the unsure pieces kept.

    Args:
        img: Page image.
        lines: Every line read on the page, whatever its confidence.
        recognize: Recognition only; None drops the low lines (a page already
            read turned).
    """
    boxes = [_box(line.quad) for line in lines]
    group_of: dict[int, int] = {}
    if recognize is not None:
        for i, line in enumerate(lines):
            if line.score >= LINE_CONF or i in group_of:
                continue
            group_of[i], todo = i, [i]
            while todo:
                a = boxes[todo.pop()]
                for j, other in enumerate(lines):
                    if (
                        j not in group_of
                        and other.score < UPSIDE_DOWN_CONF
                        and _same_band(a, boxes[j])
                    ):
                        group_of[j] = i
                        todo.append(j)
    out: list[Line] = []
    done: set[int] = set()
    for i, line in enumerate(lines):
        head = group_of.get(i)
        if head is None:
            if line.score >= LINE_CONF:
                out.append(line)
            continue
        if head in done:
            continue
        done.add(head)
        members = [lines[m] for m, h in sorted(group_of.items()) if h == head]
        turned = turned_line(img, members, recognize) if recognize else None
        if turned is not None:
            out.append(turned)
        else:  # not upside down: noise dropped, unsure pieces kept
            out += [m for m in members if m.score >= LINE_CONF]
    return out


def margin_results(
    img: np.ndarray, margin_px: int, predict: Predict, min_score: float
) -> list[dict]:
    """Vertical text of the side margins: each strip is read rotated both ways
    and the more confident reading is kept.

    Args:
        img: Page image, ``(h, w, 3)``.
        margin_px: Width of each side strip in pixels.
        predict: The OCR engine.
        min_score: Lines below this confidence are dropped.

    Returns:
        LiteParse OCR results in page pixels, in the reading frame of the text.
    """
    h, w = img.shape[:2]
    mw = min(margin_px, w // 2)  # the strips never overlap
    if mw <= 0:
        return []
    out: list[dict] = []
    for x_off, strip in ((0, img[:, :mw]), (w - mw, img[:, -mw:])):
        best: list[Line] = []
        best_cw = True
        for rot, clockwise in (
            (cv2.ROTATE_90_CLOCKWISE, True),
            (cv2.ROTATE_90_COUNTERCLOCKWISE, False),
        ):
            found = predict(np.ascontiguousarray(cv2.rotate(strip, rot)))
            if _weight(found) > _weight(best):
                best, best_cw = found, clockwise
        for line in best:
            if line.score < min_score or not line.text.strip():
                continue
            for text, quad in line_words(line):
                back = rotate_back(quad, best_cw, mw, h, x_off)
                out.append(to_result(text, back, line.score))
    return out


def page_results(
    img: np.ndarray,
    dpi: float,
    predict: Predict,
    margin_pt: float,
    min_score: float,
    recognize: Recognize | None = None,
) -> list[dict]:
    """Every word of a page image: the full page plus the margin strips.

    A page read with low confidence is read again turned by 180 degrees; the
    turned reading is kept only when it is both confident and richer, its boxes
    mapped back onto the image. On an upright page the noise lines are read
    again turned (``settle_lines``).

    Args:
        img: Page image.
        dpi: Rendering resolution of the image.
        predict: Detection and recognition; every line comes back, whatever its
            confidence.
        margin_pt: Width of the side strips read rotated by 90 degrees; 0 = off.
        min_score: Margin lines below this confidence are dropped.
        recognize: Recognition only, for the lines read again turned.
    """
    lines = predict(img)
    upright = True
    if _mean_conf(lines) < UPSIDE_DOWN_CONF:
        turned = predict(np.ascontiguousarray(cv2.rotate(img, cv2.ROTATE_180)))
        if _mean_conf(turned) >= UPSIDE_DOWN_CONF and _weight(turned) > _weight(lines):
            h, w = img.shape[:2]
            lines, upright = _turn_back(turned, w, h), False
    out = results(settle_lines(img, lines, recognize if upright else None))
    if margin_pt > 0:
        out += margin_results(img, round(margin_pt * dpi / 72), predict, min_score)
    return out


def gpu_count() -> int:
    """NVIDIA GPUs this process can use, asked to the driver (0 without one).

    Checked before ONNX Runtime is asked for CUDA: on a node without a GPU it
    prints an "EP Error" block per model, outside the log format, and only then
    falls back to the CPU.
    """
    try:
        cuda = ctypes.CDLL("libcuda.so.1")
    except OSError:
        return 0  # no NVIDIA driver (a container started without a GPU)
    if cuda.cuInit(0) != 0:
        return 0
    count = ctypes.c_int()
    if cuda.cuDeviceGetCount(ctypes.byref(count)) != 0:
        return 0
    return count.value


class _ReleasingSession:
    """An ONNX Runtime session that gives its GPU memory back after every run."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._options = onnxruntime.RunOptions()
        self._options.add_run_config_entry(
            "memory.enable_memory_arena_shrinkage", "gpu:0"
        )

    def run(self, names: Any, feed: Any) -> Any:
        return self._session.run(names, feed, self._options)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def release_gpu_memory_after_runs(ocr: Any) -> bool:
    """Make the loaded models of a RapidOCR engine release GPU memory after each run.

    ONNX Runtime keeps the memory of the largest run for every input size: on an
    8 GB card it ends up holding 7.8 GB for a real peak of about 4 GB, and the
    NER models on the same GPU starve. Releasing it costs no speed.

    Models that ONNX Runtime moved to the CPU (no usable GPU) are left as they are.

    Args:
        ocr: A RapidOCR engine whose models are loaded (after a first read).

    Returns:
        True when the models run on the GPU.
    """
    on_gpu = False
    for name in ("text_det", "text_cls", "text_rec"):
        model = getattr(ocr, name, None)
        if model is None:
            continue
        session = model.session.session
        if "CUDAExecutionProvider" in session.get_providers():
            model.session.session = _ReleasingSession(session)
            on_gpu = True
    return on_gpu


def parse_rapid(res: Any) -> list[Line]:
    """Lines of a RapidOCR result (called with ``return_word_box``).

    RapidOCR computes word boxes from the recogniser's character positions and
    the average character width: they already cover the glyphs.
    """
    texts = list(getattr(res, "txts", None) or ())
    scores = list(getattr(res, "scores", None) or ())
    boxes = getattr(res, "boxes", None)
    boxes = list(boxes) if boxes is not None else []
    words = list(getattr(res, "word_results", None) or ())
    out = []
    for i, text in enumerate(texts):
        if i >= len(boxes):
            break
        line_words: list[tuple[str, Quad]] = []
        if i < len(words) and isinstance(words[i], (list, tuple)):
            line_words = [
                (str(w[0]), _quad(w[2]))
                for w in words[i]
                if isinstance(w, (list, tuple)) and len(w) == 3 and w[2] is not None
            ]
        score = float(scores[i]) if i < len(scores) else 0.0
        out.append(Line(str(text), score, _quad(boxes[i]), line_words))
    return out


class RapidEngine:
    """PP-OCRv5 through RapidOCR (ONNX Runtime), loaded once, one request at a time.

    Args:
        settings: Server settings.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ocr: Any = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once the models are loaded."""
        return self._ocr is not None

    def load(self) -> None:
        """Build the RapidOCR engine (models from ``model_dir``, else its own cache)."""
        s = self.settings
        detector = ModelType.MOBILE if "mobile" in s.det_model else ModelType.SERVER
        params: dict[str, Any] = {
            "Global.log_level": "warning",
            # the line classifier turns upright lines by 180 degrees: read as noise,
            # they fall under the score threshold and whole lines are lost; text
            # upside down is handled by page_results instead
            "Global.use_cls": False,
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": detector,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,  # the only Latin recogniser of PP-OCRv5
            "Rec.lang_type": LangRec.LATIN,
        }
        # RapidOCR shrinks every image to a long side of 2000 px first; never scale
        # up after that (its default would blow the thin margin strips up to 736 px)
        params["Det.limit_side_len"] = 2000
        params["Det.limit_type"] = "max"
        if s.cpu_threads:
            # the caller (NER, rendering) runs on the same cores: no oversubscription
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = s.cpu_threads
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
        use_cuda = s.device != "cpu" and "CUDAExecutionProvider" in (
            onnxruntime.get_available_providers()
        )
        if use_cuda and gpu_count() == 0:
            if s.device == "gpu":
                raise RuntimeError("OCRSRV_DEVICE=gpu but no usable GPU")
            logger.warning("No usable GPU: OCR runs on the CPU, about 20 s per page")
            use_cuda = False
        if use_cuda:
            onnxruntime.preload_dlls()  # the CUDA libraries installed as wheels
            params["EngineConfig.onnxruntime.use_cuda"] = True
            # grow GPU memory only as needed: the card is shared with the NER models
            params["EngineConfig.onnxruntime.cuda_ep_cfg.arena_extend_strategy"] = (
                "kSameAsRequested"
            )
            # EXHAUSTIVE benchmarks cuDNN algorithms for every new input size (every
            # text line), slow when GPU memory is short; HEURISTIC picks without running
            params["EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search"] = (
                s.cudnn_search
            )
            if s.gpu_mem_limit_mb:
                params["EngineConfig.onnxruntime.cuda_ep_cfg.gpu_mem_limit"] = (
                    s.gpu_mem_limit_mb * 1024 * 1024
                )
        if s.model_dir:
            Path(s.model_dir).mkdir(parents=True, exist_ok=True)
            params["Global.model_root_dir"] = s.model_dir
        ocr = RapidOCR(params=params)
        # RapidOCR loads (and downloads) its models on the first image: read a sample
        # now, so /health is 503 until every model works and a missing one fails here
        sample = np.full((64, 320, 3), 255, np.uint8)
        cv2.putText(
            sample, "Referto 2026", (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2
        )
        if not getattr(ocr(sample), "txts", None):
            raise RuntimeError("OCR models loaded but the sample image reads nothing")
        on_gpu = release_gpu_memory_after_runs(ocr) if use_cuda else False
        self._ocr = ocr
        logger.info("OCR models loaded: engine=rapidocr cuda=%s", on_gpu)

    # the steps are passed on every call: RapidOCR keeps them on the engine
    def predict(self, img: np.ndarray) -> list[Line]:
        """Every line of an image, whatever its confidence (sorted by settle_lines)."""
        if self._ocr is None:
            raise RuntimeError("OCR engine not loaded")
        with self._lock:
            res = self._ocr(
                img,
                use_det=True,
                use_cls=False,
                use_rec=True,
                return_word_box=True,
                text_score=0.0,
            )
        return parse_rapid(res)

    def recognize(self, img: np.ndarray) -> tuple[str, float]:
        """Text of an image holding one line, recognition only (no detection)."""
        if self._ocr is None:
            raise RuntimeError("OCR engine not loaded")
        with self._lock:
            res = self._ocr(
                img, use_det=False, use_cls=False, use_rec=True, text_score=0.0
            )
        txts, scores = getattr(res, "txts", None), getattr(res, "scores", None)
        return (str(txts[0]), float(scores[0])) if txts and scores else ("", 0.0)

    def read(self, img: np.ndarray, dpi: float) -> list[dict]:
        """LiteParse OCR results of a page image rendered at ``dpi``."""
        s = self.settings
        return page_results(
            img, dpi, self.predict, s.margin_pt, s.margin_min_score, self.recognize
        )
