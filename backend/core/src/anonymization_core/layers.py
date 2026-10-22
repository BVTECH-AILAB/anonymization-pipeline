"""Text layers: page text with a link from every character back to its box.

A page can have several layers (native text layer, OCR). Detection runs on
``layer.text``; ``layer.rects(start, end)`` turns a detected span back into
one rectangle per line, which is what redaction needs.
"""

import re
from dataclasses import dataclass, field

from anonymization_core.geometry import Rect, union


@dataclass
class Word:
    """One OCR word in PDF points (top-left origin)."""

    text: str
    rect: Rect
    conf: float = 1.0
    source: str = "native"
    line: int = 0
    # reading direction inside ``rect``: ltr, rtl, ttb (top to bottom), btt (bottom to top)
    direction: str = "ltr"


@dataclass
class Token:
    text: str
    rect: Rect
    chars: list[Rect]  # one box per character of ``text``
    line: int
    source: str
    conf: float = 1.0
    direction: str = "ltr"  # reading direction, as in ``Word``


@dataclass
class Span:
    start: int
    end: int
    label: str
    score: float
    source: str  # detector name
    text: str = ""


@dataclass
class TextLayer:
    page: int
    source: str  # native | ocr
    tokens: list[Token]
    text: str = ""
    starts: list[int] = field(default_factory=list)

    @classmethod
    def build(cls, page: int, source: str, tokens: list[Token]) -> "TextLayer":
        """Join the tokens into the layer text: a space within a line, a newline between lines."""
        parts, starts, pos, prev_line = [], [], 0, None
        for t in tokens:
            if prev_line is not None:
                sep = " " if t.line == prev_line else "\n"
                parts.append(sep)
                pos += 1
            starts.append(pos)
            parts.append(t.text)
            pos += len(t.text)
            prev_line = t.line
        return cls(page, source, tokens, "".join(parts), starts)

    def token_range(self, start: int, end: int) -> list[tuple[Token, int, int]]:
        """Tokens overlapping [start, end) with the overlapping char range inside each token."""
        out = []
        for t, s in zip(self.tokens, self.starts, strict=True):
            e = s + len(t.text)
            if e <= start or s >= end:
                continue
            out.append((t, max(start, s) - s, min(end, e) - s))
        return out

    def rects(self, start: int, end: int, pad: float = 0.0) -> list[Rect]:
        """One rectangle per line covering the characters in [start, end)."""
        by_line: dict[int, Rect] = {}
        for t, a, b in self.token_range(start, end):
            r = union(t.chars[a:b])
            if r is None:
                continue
            by_line[t.line] = (
                union([by_line[t.line], r]) or r if t.line in by_line else r
            )
        return [
            (r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad) for r in by_line.values()
        ]


def _cell(text: str) -> str:
    """The character of a one-character cell ("A", "7", "1." or ".1" -> "1", "|"), or ""."""
    c = text.strip(".,") if len(text) == 2 else text  # a dot or a speck read beside it
    return c if len(c) == 1 and (c.isdigit() or c.isupper() or c == "|") else ""


def merge_cells(tokens: list[Token], min_run: int = 5) -> list[Token]:
    """Join characters written one per box ("R S S M R A ...") into one word.

    Forms print the codice fiscale or the tessera sanitaria one character per
    cell: each character is a separate word, so no rule sees the code. A run of
    at least ``min_run`` single-character words on the same row, of similar
    height and close together, becomes one token whose character boxes are the
    original cells. OCR noise on a cell is tolerated: boxes of neighbouring
    cells that overlap slightly, a trailing dot ("1."), and a cell read as "|"
    (an "I" or a "1", resolved by the neighbouring character; codes with a check
    character are repaired later by the rules).
    """

    def joinable(a: Token, b: Token) -> bool:
        ax0, ay0, ax1, ay1 = a.rect
        bx0, by0, bx1, by1 = b.rect
        ha, hb = ay1 - ay0, by1 - by0
        overlap = min(ay1, by1) - max(ay0, by0)
        gap = bx0 - ax1
        width = min(ax1 - ax0, bx1 - bx0)
        return (
            overlap >= 0.5 * min(ha, hb)
            and abs(ha - hb) <= 0.35 * max(ha, hb)
            and -max(0.5, 0.3 * width) <= gap <= 2.5 * max(ha, bx1 - bx0)
        )

    # codes are capitals and digits; single-letter words of prose ("e", "a", "o") are lowercase
    cells = [_cell(t.text) for t in tokens]
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        j = i + 1
        if cells[i] and cells[i] != "|":
            while (
                j < len(tokens)
                and cells[j]
                and not (cells[j] == "|" and cells[j - 1] == "|")
                and joinable(tokens[j - 1], tokens[j])
            ):
                j += 1
        if j - i >= min_run:
            run = tokens[i:j]
            text = ""
            for c in cells[i:j]:
                if c == "|":
                    c = "1" if text[-1:].isdigit() else "I"
                text += c
            r = union(t.rect for t in run) or run[0].rect
            out.append(
                Token(
                    text,
                    r,
                    [t.chars[0] if len(t.chars) == 1 else t.rect for t in run],
                    run[0].line,
                    run[0].source,
                    min(t.conf for t in run),
                )
            )
        else:
            j = i + 1
            out.append(tokens[i])
        i = j
    return out


# inside a word: a capitalised word right after lower-case letters, or after a colon
_GLUED = re.compile(
    r"(?<=[a-zà-ÿ]{3})(?=[A-ZÀ-Ü][\wÀ-ÿ-]{2})|(?<=\w:)(?=[A-ZÀ-Ü][a-zà-ÿ]{2})"
)


def split_glued(tokens: list[Token]) -> list[Token]:
    """Split words glued without spaces by a scanner's OCR layer, keeping the character boxes.

    "prevpt:LindaMarshRX-550-2877" becomes "prevpt:", "Linda", "Marsh",
    "RX-550-2877", so no detector misses a name hidden inside one word. Both parts
    need three characters: "McDonald" and "DiMaio" stay whole.
    """
    out: list[Token] = []
    for t in tokens:
        cuts = [m.start() for m in _GLUED.finditer(t.text)]
        if not cuts or len(t.chars) != len(t.text):
            out.append(t)
            continue
        for a, b in zip([0, *cuts], [*cuts, len(t.text)], strict=True):
            r = union(t.chars[a:b]) or t.chars[a]
            out.append(
                Token(
                    t.text[a:b],
                    r,
                    t.chars[a:b],
                    t.line,
                    t.source,
                    t.conf,
                )
            )
    return out


def is_vertical(rect: Rect, text: str) -> bool:
    """True for a box of text written top-down or bottom-up (taller than wide, a real word)."""
    x0, y0, x1, y1 = rect
    return (y1 - y0) > 1.5 * (x1 - x0) and len(text) > 2


def char_boxes(rect: Rect, n: int, direction: str) -> list[Rect]:
    """``n`` character boxes inside a word box, in reading order."""
    x0, y0, x1, y1 = rect
    k = max(n, 1)
    if direction in ("ttb", "btt"):
        step = (y1 - y0) / k
        boxes = [(x0, y0 + i * step, x1, y0 + (i + 1) * step) for i in range(n)]
    else:
        step = (x1 - x0) / k
        boxes = [(x0 + i * step, y0, x0 + (i + 1) * step, y1) for i in range(n)]
    return boxes[::-1] if direction in ("btt", "rtl") else boxes


def ocr_layer(page_no: int, words: list[Word]) -> TextLayer:
    """Tokens from OCR words; character boxes are interpolated inside the word box
    along its reading direction."""
    tokens = []
    for w in words:
        chars = char_boxes(w.rect, len(w.text), w.direction)
        tokens.append(Token(w.text, w.rect, chars, w.line, w.source, w.conf))
    return TextLayer.build(page_no, "ocr", merge_cells(tokens))
