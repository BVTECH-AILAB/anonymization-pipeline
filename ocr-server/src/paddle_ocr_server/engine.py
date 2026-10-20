"""PP-OCRv5 on RapidOCR (ONNX Runtime): page and margin passes, one result per word."""

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


def _quad(points: Any) -> Quad:
    p = [(float(x), float(y)) for x, y in np.asarray(points).reshape(4, 2)]
    return (p[0], p[1], p[2], p[3])


def _weight(lines: list[Line]) -> float:
    return sum(line.score * len(line.text) for line in lines)


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
    img: np.ndarray, dpi: float, predict: Predict, margin_pt: float, min_score: float
) -> list[dict]:
    """Every word of a page image: the full page plus the margin strips."""
    out = results(predict(img))
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

    def predict(self, img: np.ndarray) -> list[Line]:
        """Lines of an image."""
        if self._ocr is None:
            raise RuntimeError("OCR engine not loaded")
        with self._lock:
            res = self._ocr(img, return_word_box=True)
        return parse_rapid(res)

    def read(self, img: np.ndarray, dpi: float) -> list[dict]:
        """LiteParse OCR results of a page image rendered at ``dpi``."""
        s = self.settings
        return page_results(img, dpi, self.predict, s.margin_pt, s.margin_min_score)
