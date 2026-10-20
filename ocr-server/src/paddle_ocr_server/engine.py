"""PaddleOCR PP-OCRv5 engine: page and margin passes, one result per word."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime
from paddleocr import PaddleOCR
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


def parse_result(res: Any) -> list[Line]:
    """Lines of one PaddleOCR 3.x result (``predict`` with ``return_word_box``).

    Args:
        res: The result mapping: ``rec_texts``, ``rec_scores``, ``rec_polys`` and,
            per line, ``text_word`` / ``text_word_region``.

    Returns:
        One ``Line`` per recognised line.
    """
    texts = list(res.get("rec_texts") or [])
    scores = list(res.get("rec_scores") or [])
    polys = list(res.get("rec_polys") or res.get("dt_polys") or [])
    words = list(res.get("text_word") or [])
    regions = list(res.get("text_word_region") or [])
    out = []
    for i, text in enumerate(texts):
        if i >= len(polys):
            break
        line_word_boxes: list[tuple[str, Quad]] = []
        if i < len(words) and i < len(regions) and len(words[i]) == len(regions[i]):
            line_word_boxes = [
                (str(t), _quad(q)) for t, q in zip(words[i], regions[i], strict=True)
            ]
        score = float(scores[i]) if i < len(scores) else 0.0
        out.append(Line(str(text), score, _quad(polys[i]), line_word_boxes))
    return out


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


class PaddleEngine:
    """PaddleOCR pipeline, loaded once and used by one request at a time.

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
        """Build the PaddleOCR pipeline."""
        s = self.settings
        kwargs: dict[str, Any] = {
            "text_detection_model_name": s.det_model,
            "text_recognition_model_name": s.rec_model,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "return_word_box": True,
        }
        if s.det_limit_side_len:
            kwargs["text_det_limit_side_len"] = s.det_limit_side_len
            kwargs["text_det_limit_type"] = "max"
        else:  # the full page: only images smaller than 64 px are scaled up
            kwargs["text_det_limit_side_len"] = 64
            kwargs["text_det_limit_type"] = "min"
        if s.model_dir:
            kwargs["text_detection_model_dir"] = str(Path(s.model_dir) / s.det_model)
            kwargs["text_recognition_model_dir"] = str(Path(s.model_dir) / s.rec_model)
        if s.device != "auto":
            kwargs["device"] = s.device
        self._ocr = PaddleOCR(**kwargs)
        logger.info("OCR models loaded: det=%s rec=%s", s.det_model, s.rec_model)

    def predict(self, img: np.ndarray) -> list[Line]:
        """Lines of an image."""
        if self._ocr is None:
            raise RuntimeError("OCR engine not loaded")
        with self._lock:
            res = self._ocr.predict(img)
        return parse_result(res[0]) if res else []

    def read(self, img: np.ndarray, dpi: float) -> list[dict]:
        """LiteParse OCR results of a page image rendered at ``dpi``."""
        s = self.settings
        return page_results(img, dpi, self.predict, s.margin_pt, s.margin_min_score)


def parse_rapid(res: Any) -> list[Line]:
    """Lines of a RapidOCR result (called with ``return_word_box``).

    RapidOCR computes word boxes from the recogniser's character positions and
    the average character width: they already cover the glyphs (no widening).
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
        out.append(Line(str(text), score, _quad(boxes[i]), line_words, widen=False))
    return out


class RapidEngine:
    """The same PP-OCRv5 models through RapidOCR (ONNX Runtime), without paddlepaddle.

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
        if s.det_limit_side_len:
            params["Det.limit_side_len"] = s.det_limit_side_len
            params["Det.limit_type"] = "max"
        if s.cpu_threads:
            # the caller (NER, rendering) runs on the same cores: no oversubscription
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = s.cpu_threads
            params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
        use_cuda = s.device != "cpu" and "CUDAExecutionProvider" in (
            onnxruntime.get_available_providers()
        )
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
        if s.model_dir:
            Path(s.model_dir).mkdir(parents=True, exist_ok=True)
            params["Global.model_root_dir"] = s.model_dir
        self._ocr = RapidOCR(params=params)
        logger.info("OCR models loaded: engine=rapidocr cuda=%s", use_cuda)

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


def make_engine(settings: Settings) -> PaddleEngine | RapidEngine:
    """The OCR engine chosen by ``OCRSRV_ENGINE``."""
    return (
        RapidEngine(settings)
        if settings.engine == "rapidocr"
        else PaddleEngine(settings)
    )
