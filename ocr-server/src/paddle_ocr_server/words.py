"""Word geometry: OCR lines -> one LiteParse OCR result per word.

LiteParse treats every OCR result as one word, while PaddleOCR reads lines.
Each line is split into words here, so a redaction box covers a word and not
the whole line.

Quadrilaterals are four ``(x, y)`` pixel points ordered TL, TR, BR, BL in the
reading frame of the text: TL -> TR is the reading direction. LiteParse
derives the rotation of vertical text from that order.
"""

from dataclasses import dataclass, field

Point = tuple[float, float]
Quad = tuple[Point, Point, Point, Point]


@dataclass
class Line:
    """One text line read by the OCR engine.

    Attributes:
        text: Recognised text.
        score: Recognition confidence (0-1).
        quad: Line quadrilateral in the reading frame.
        words: Word quadrilaterals from the recogniser; may be empty, or may not
            match the words of ``text``.
        widen: True when ``words`` span CTC columns (PaddleX) and must be widened
            to whole characters; False when they already cover the glyphs (RapidOCR).
    """

    text: str
    score: float
    quad: Quad
    words: list[tuple[str, Quad]] = field(default_factory=list)
    widen: bool = True


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _spans(text: str) -> list[tuple[str, int, int]]:
    """Words of ``text`` with their character offsets."""
    out: list[tuple[str, int, int]] = []
    start = None
    for i, ch in enumerate(text + " "):
        if ch.isspace():
            if start is not None:
                out.append((text[start:i], start, i))
                start = None
        elif start is None:
            start = i
    return out


def proportional_words(text: str, quad: Quad) -> list[tuple[str, Quad]]:
    """Split a line quadrilateral into words, in proportion to their characters.

    Args:
        text: Line text.
        quad: Line quadrilateral in the reading frame.

    Returns:
        One ``(word, quad)`` per word of ``text``, along TL -> TR.
    """
    n = max(len(text), 1)
    tl, tr, br, bl = quad
    return [
        (
            word,
            (
                _lerp(tl, tr, a / n),
                _lerp(tl, tr, b / n),
                _lerp(bl, br, b / n),
                _lerp(bl, br, a / n),
            ),
        )
        for word, a, b in _spans(text)
    ]


def is_vertical(quad: Quad) -> bool:
    """True when the long side of the quadrilateral is vertical on the image."""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (max(ys) - min(ys)) > 1.5 * (max(xs) - min(xs))


def widen(line: Line, words: list[tuple[str, Quad]]) -> list[tuple[str, Quad]]:
    """Recogniser word boxes widened to whole characters, as RapidOCR computes them.

    PaddleX places a word between the CTC columns of its first and last
    character; a column is about a sixth of the line height, so a single digit
    gets a box narrower than its glyph and a redaction would leave it partly
    visible. Each word is widened to half a character pitch before the centre of
    its first column and after the centre of its last one, clamped to the line;
    neighbours that overlap are split in the middle.

    Args:
        line: The line (horizontal on the image it was read from).
        words: Recogniser boxes of its words, in reading order.

    Returns:
        The words with their widened, axis-aligned boxes.
    """
    xs = [p[0] for p in line.quad]
    ys = [p[1] for p in line.quad]
    lx0, lx1 = min(xs), max(xs)
    column = (max(ys) - min(ys)) / 6
    pitch = (lx1 - lx0) / max(len(line.text), 1)
    spans: list[list[float]] = []
    for _, q in words:
        a, b = min(p[0] for p in q), max(p[0] for p in q)
        first = a + column / 2
        last = max(b - column / 2, first)
        spans.append([max(lx0, first - pitch / 2), min(lx1, last + pitch / 2)])
    for left, right in zip(spans, spans[1:], strict=False):
        if left[1] > right[0]:
            mid = (left[1] + right[0]) / 2
            left[1] = right[0] = mid
    out = []
    for (text, q), (x0, x1) in zip(words, spans, strict=True):
        y0, y1 = min(p[1] for p in q), max(p[1] for p in q)
        out.append((text, ((x0, y0), (x1, y0), (x1, y1), (x0, y1))))
    return out


def line_words(line: Line) -> list[tuple[str, Quad]]:
    """Words of a horizontal line: widened recogniser boxes when they match the
    text, otherwise a proportional split.

    Args:
        line: The line.

    Returns:
        One ``(word, quad)`` per word.
    """
    words = [(t.strip(), q) for t, q in line.words if t.strip()]
    if [t for t, _ in words] == line.text.split():
        return widen(line, words) if line.widen else words
    return proportional_words(line.text, line.quad)


def to_result(text: str, quad: Quad, score: float) -> dict:
    """One result of the LiteParse OCR API."""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return {
        "text": text,
        "bbox": [min(xs), min(ys), max(xs), max(ys)],
        "confidence": round(float(score), 4),
        "polygon": [[float(x), float(y)] for x, y in quad],
    }


def results(lines: list[Line]) -> list[dict]:
    """LiteParse OCR results of a page: one per word of every horizontal line.

    A vertical line read on the full page keeps its whole text in one result:
    its words are split by the caller, which knows the reading direction.
    """
    out: list[dict] = []
    for line in lines:
        if not line.text.strip():
            continue
        if is_vertical(line.quad):
            out.append(to_result(line.text.strip(), line.quad, line.score))
            continue
        out += [to_result(t, q, line.score) for t, q in line_words(line)]
    return out


def rotate_back(
    quad: Quad, clockwise: bool, strip_w: int, strip_h: int, x_off: int
) -> Quad:
    """A quadrilateral read on a margin strip rotated by 90 degrees, on the page.

    Args:
        quad: Quadrilateral on the rotated strip.
        clockwise: True when the strip was rotated clockwise before reading.
        strip_w: Width of the strip before rotation.
        strip_h: Height of the strip before rotation (the page height in pixels).
        x_off: Left edge of the strip on the page.

    Returns:
        The quadrilateral in page pixels. The point order is kept, so it stays
        in the reading frame of the text.
    """
    if clockwise:
        # rotated (xr, yr) -> strip (yr, h - xr): the text runs bottom to top
        p = [(yr + x_off, strip_h - xr) for xr, yr in quad]
    else:
        # counter-clockwise: rotated (xr, yr) -> strip (w - yr, xr): top to bottom
        p = [(strip_w - yr + x_off, xr) for xr, yr in quad]
    return (p[0], p[1], p[2], p[3])
