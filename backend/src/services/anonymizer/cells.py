"""Codes printed one character per box on scanned forms.

Forms print the codice fiscale or the tessera sanitaria one character per box.
Read as a page, every box is a separate word and scanner specks inside the
boxes come back as ".C", "i7" or a lower-case "s", so the characters no longer
join into a code (``layers.merge_cells``). Here the boxes are found from the
ruling of the form (two horizontal lines with evenly spaced vertical lines
between them), the ink of every box is cleaned of specks and the box borders,
and the characters are set side by side into one word, which the recogniser
reads far better than isolated characters. The geometry tells how many
characters there are: a reading of another length is not used.

The page is read by the OCR server; the composed word goes to its recognition
endpoint (``read_cells``).
"""

import dataclasses
import re
from collections.abc import Callable

import cv2
import numpy as np

from src.services.anonymizer.detect.rules import is_code
from src.services.anonymizer.geometry import Rect
from src.services.anonymizer.layers import Word

PixRect = tuple[float, float, float, float]
# a word image -> (text, confidence), recognition only
Recognizer = Callable[[np.ndarray], tuple[str, float]]

# height of a box row, in points
MIN_ROW_PT, MAX_ROW_PT = 7.0, 30.0
# width of a box relative to its height
MIN_ASPECT, MAX_ASPECT = 0.4, 1.8
# boxes in a row: shorter runs are not codes
MIN_CELLS = 5
# a column belongs to a vertical border when ink covers this share of the row height
BORDER_COVER = 0.8
# shortest horizontal ruling line, in points
MIN_LINE_PT = 30.0
# most ink inside a row of boxes: a title bar thresholds into a "row" half full of ink
MAX_INSIDE_INK = 0.35
# least ink in a box added past the borders seen: an empty stretch of ruling is not a box
MIN_CHAR_INK = 0.03
# space between the characters of the composed word, in points: repeated
# characters ("99") that touch are read as one
GAP_PT = 3.0
# least confidence of a row of character boxes read again
CELL_CONF = 0.5


def _ink(gray: np.ndarray) -> np.ndarray:
    """Ink mask (255) of a grayscale image, robust to paper tint and uneven light."""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """``(start, end)`` of every run of True values."""
    padded = np.concatenate(([False], flags, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist(), strict=True))


def _row_cells(
    ink: np.ndarray, top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]
) -> list[list[PixRect]]:
    """Runs of evenly sized boxes between two horizontal ruling lines."""
    x0, x1 = max(top[0], bottom[0]), min(top[2], bottom[2])
    y0, y1 = top[3], bottom[1]
    h = y1 - y0
    cover = ink[y0:y1, x0:x1].mean(axis=0) / 255 >= BORDER_COVER
    borders = [x0 + (a + b) / 2 for a, b in _runs(cover)]
    ytop, ybot = (top[1] + top[3]) / 2, (bottom[1] + bottom[3]) / 2
    rows: list[list[PixRect]] = []
    cells: list[PixRect] = []
    for left, right in zip(borders, borders[1:], strict=False):
        w = right - left
        same = not cells or abs(w - (cells[0][2] - cells[0][0])) <= 0.2 * w
        if MIN_ASPECT * h <= w <= MAX_ASPECT * h and same:
            cells.append((left, ytop, right, ybot))
            continue
        if len(cells) >= MIN_CELLS:
            rows.append(cells)
        cells = (
            [(left, ytop, right, ybot)] if MIN_ASPECT * h <= w <= MAX_ASPECT * h else []
        )
    if len(cells) >= MIN_CELLS:
        rows.append(cells)
    # the lines may be broken near the ends: boxes are added while either goes on
    span = (min(top[0], bottom[0]), max(top[2], bottom[2]))
    rows = [_extend(ink, r, borders, *span) for r in rows]
    return [r for r in rows if _inside_ink(ink, r) <= MAX_INSIDE_INK]


def _extend(
    ink: np.ndarray, row: list[PixRect], borders: list[float], x0: int, x1: int
) -> list[PixRect]:
    """Add the boxes whose outer border the scan lost, along the ruling lines.

    The ruling lines span the whole row even when a faint first or last border
    is not seen: boxes of the same width are added while they fit under the
    lines and hold a character, each snapped to a border seen near its edge.
    Only one box per side may lack that border (the lost outer one): past it,
    the lines run over something else (a table, text under a rule).
    """
    w = float(np.median([c[2] - c[0] for c in row]))
    ytop, ybot = row[0][1], row[0][3]

    def snap(x: float) -> float | None:
        dist, b = min(((abs(b - x), b) for b in borders), default=(w, x))
        return b if dist <= 0.25 * w else None

    def holds_char(left: float, right: float) -> bool:
        cell = (left, ytop, right, ybot)
        return _inside_ink(ink, [cell]) >= MIN_CHAR_INK

    out = list(row)
    while out[0][0] - w >= x0 - 0.3 * w:
        seen = snap(out[0][0] - w)
        left = out[0][0] - w if seen is None else seen
        if not holds_char(left, out[0][0]):
            break
        out.insert(0, (left, ytop, out[0][0], ybot))
        if seen is None:
            break
    while out[-1][2] + w <= x1 + 0.3 * w:
        seen = snap(out[-1][2] + w)
        right = out[-1][2] + w if seen is None else seen
        if not holds_char(out[-1][2], right):
            break
        out.append((out[-1][2], ytop, right, ybot))
        if seen is None:
            break
    return out


def _inside_ink(ink: np.ndarray, row: list[PixRect]) -> float:
    """Share of ink between the ruling lines of a row."""
    x0, y0, x1, y1 = (int(v) for v in (row[0][0], row[0][1], row[-1][2], row[-1][3]))
    pad = max(1, (y1 - y0) // 5)
    inside = ink[y0 + pad : y1 - pad, x0:x1]
    return float(inside.mean()) / 255 if inside.size else 1.0


def cell_rows(img: np.ndarray, scale: float) -> list[list[PixRect]]:
    """Rows of character boxes on a page rendering.

    Args:
        img: RGB page rendering.
        scale: Pixels per point of the rendering.

    Returns:
        Every row of at least ``MIN_CELLS`` evenly sized boxes, left to right,
        each box in pixels from the middle of one border to the next.
    """
    ink = _ink(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (int(MIN_LINE_PT * scale), 1))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel)
    _, _, stats, _ = cv2.connectedComponentsWithStats(lines)
    segs = sorted(
        (int(x), int(y), int(x + w), int(y + h)) for x, y, w, h, _ in stats[1:]
    )
    out: list[list[PixRect]] = []
    for top in segs:
        for bottom in segs:
            if not MIN_ROW_PT * scale <= bottom[1] - top[3] <= MAX_ROW_PT * scale:
                continue
            shared = min(top[2], bottom[2]) - max(top[0], bottom[0])
            if shared < 0.6 * min(top[2] - top[0], bottom[2] - bottom[0]):
                continue
            for row in _row_cells(ink, top, bottom):
                cx, cy = (row[0][0] + row[0][2]) / 2, (row[0][1] + row[0][3]) / 2
                if not any(
                    r[0][0] <= cx <= r[-1][2] and r[0][1] <= cy <= r[0][3] for r in out
                ):
                    out.append(row)
    return out


def glyph(gray: np.ndarray, cell: PixRect, scale: float) -> np.ndarray | None:
    """The ink of one box without its borders and specks, cropped to its width.

    Args:
        gray: Grayscale page rendering.
        cell: The box in pixels (border to border).
        scale: Pixels per point of the rendering.

    Returns:
        Grayscale crop (white background), or None for an empty box.
    """
    pad = max(2, int(scale))
    x0, y0, x1, y1 = (int(round(v)) for v in cell)
    crop = gray[y0 + pad : y1 - pad, x0 + pad : x1 - pad]
    if crop.size == 0:
        return None
    ink = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    keep = np.zeros(ink.shape, bool)
    for k in range(1, n):
        # a character stroke is tall; specks and border remnants are small or flat
        if (
            stats[k, cv2.CC_STAT_AREA] >= max(6, 0.004 * ink.size)
            and stats[k, cv2.CC_STAT_HEIGHT] >= 0.25 * ink.shape[0]
        ):
            keep |= labels == k
    if not keep.any():
        return None
    cols = np.flatnonzero(keep.any(axis=0))
    clean = np.where(keep, crop, 255).astype(np.uint8)
    return clean[:, cols[0] : cols[-1] + 1]


def compose(glyphs: list[np.ndarray], scale: float) -> np.ndarray:
    """Set the characters side by side into one word image for the recogniser.

    Args:
        glyphs: Grayscale crops of the characters, in reading order.
        scale: Pixels per point of the rendering.

    Returns:
        RGB image of the word with a white margin.
    """
    h = max(g.shape[0] for g in glyphs)
    gap = np.full((h, max(2, int(GAP_PT * scale))), 255, np.uint8)
    parts: list[np.ndarray] = []
    for g in glyphs:
        if parts:
            parts.append(gap)
        parts.append(
            cv2.copyMakeBorder(
                g, 0, h - g.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=255
            )
        )
    word = cv2.copyMakeBorder(
        np.hstack(parts), 10, 10, 12, 12, cv2.BORDER_CONSTANT, value=255
    )
    return cv2.cvtColor(word, cv2.COLOR_GRAY2RGB)


def read_cells(
    img: np.ndarray, scale: float, words: list[Word], recognize: Recognizer
) -> list[Word]:
    """Replace the page OCR of every row of character boxes by a clean reading.

    A row is read again as one word made of its cleaned boxes; the reading is
    used only when it has one capital or digit per box and enough confidence,
    and only when the row holds the whole code: no word of the page OCR runs out
    of it, no single character is read right next to it, and the page OCR did
    not already read a valid code there that the clean reading is not. Its
    characters then take the place of the words the page OCR read in the row,
    one word per box, so ``merge_cells`` joins them into the code with the boxes
    as character boxes.

    Args:
        img: RGB page rendering, upright (the page is not read upside down).
        scale: Pixels per point of the rendering.
        words: OCR words of the page in points, display space, with line ids.
        recognize: Recognition only, on an image holding one word.

    Returns:
        The words, with the rows read again in place of their page OCR.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    found = list(words)
    next_line = max((w.line for w in found), default=0) + 1
    for row in cell_rows(img, scale):
        glyphs = [glyph(gray, c, scale) for c in row]
        if any(g is None for g in glyphs):
            continue
        text, conf = recognize(compose([g for g in glyphs if g is not None], scale))
        text = text.replace(" ", "").upper()
        if len(text) != len(row) or not text.isalnum() or conf < CELL_CONF:
            continue
        cells: list[Rect] = [
            (a / scale, b / scale, c / scale, d / scale) for a, b, c, d in row
        ]
        x0, y0, x1, y1 = cells[0][0], cells[0][1], cells[-1][2], cells[-1][3]
        w = cells[0][2] - cells[0][0]
        # words on the row's band, from one box before it to one box after
        band = {
            i
            for i, f in enumerate(found)
            if f.rect[0] < x1 + w
            and f.rect[2] > x0 - w
            and min(f.rect[3], y1) - max(f.rect[1], y0) >= 0.5 * (f.rect[3] - f.rect[1])
        }
        inside = {i for i in band if found[i].rect[0] < x1 and found[i].rect[2] > x0}
        # the page OCR is kept when it read across the boxes and beyond, or read
        # single characters right next to them (boxes the ruling did not show):
        # replacing part of a code would split it
        if any(
            found[i].rect[0] < x0 - w / 2 or found[i].rect[2] > x1 + w / 2
            for i in inside
        ) or any(len(found[i].text.strip(".,:;|[]()")) <= 2 for i in band - inside):
            continue
        page = re.sub(
            r"[^A-Z0-9]", "", "".join(found[i].text for i in sorted(inside)).upper()
        )
        if is_code(page) and not is_code(text):
            continue
        # the code takes the place (and the line) of the first word read in its boxes
        at = min(inside, default=len(found))
        if inside:
            first = found[at]
        else:
            first = Word("", cells[0], conf, "ocr", next_line)
            next_line += 1
        chars = [
            dataclasses.replace(first, text=ch, rect=cell, conf=conf, direction="ltr")
            for ch, cell in zip(text, cells, strict=True)
        ]
        kept = [f for i, f in enumerate(found) if i not in inside]
        found = kept[:at] + chars + kept[at:]
    return found
