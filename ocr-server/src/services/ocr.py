"""Reading a page image: the full page, the side margins, text upside down.

A page read with low confidence is read again turned by 180 degrees (a sheet fed
upside down). On an upright page, a line read as noise is read again turned (a
label pasted upside down); the other noise lines are dropped. The engine
(``OcrEngine``, ``src.llms.rapidocr.RapidEngine`` in production) only detects
and recognises.
"""

import io
import logging
import time
from collections.abc import Callable
from typing import Protocol

import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

from src.config import Settings
from src.errors import UnreadableImageError
from src.services.words import (
    Line,
    Quad,
    line_words,
    results,
    rotate_back,
    to_result,
)

logger = logging.getLogger(__name__)


class OcrEngine(Protocol):
    """What the server needs from an OCR engine."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def predict(self, img: np.ndarray, /) -> list[Line]: ...

    def recognize(self, img: np.ndarray, /) -> tuple[str, float]: ...


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


def load_image(data: bytes) -> np.ndarray:
    """An uploaded image as RGB pixels.

    Raises:
        UnreadableImageError: Not an image, or a decompression bomb.
    """
    try:
        return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise UnreadableImageError() from None


def read_page(
    engine: OcrEngine,
    img: np.ndarray,
    dpi: float,
    settings: Settings,
    language: str = "en",
) -> list[dict]:
    """Every word of a page image rendered at ``dpi``, as LiteParse OCR results.

    Only sizes, counts and timings are logged: the text read is health data.
    """
    t0 = time.perf_counter()
    found = page_results(
        img,
        dpi,
        engine.predict,
        settings.margin_pt,
        settings.margin_min_score,
        engine.recognize,
    )
    logger.info(
        "OCR page read: size=%dx%d dpi=%.0f words=%d seconds=%.2f language=%s",
        img.shape[1],
        img.shape[0],
        dpi,
        len(found),
        time.perf_counter() - t0,
        language[:8] if language.isalpha() else "invalid",
    )
    return found
