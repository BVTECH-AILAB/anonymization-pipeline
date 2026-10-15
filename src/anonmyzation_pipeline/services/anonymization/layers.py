"""Text layers: page text with a link from every character back to its box.

A page can have several layers (native text layer, OCR). Detection runs on
``layer.text``; ``layer.rects(start, end)`` turns a detected span back into
one rectangle per line, which is what redaction needs.
"""

from dataclasses import dataclass, field

import pymupdf

Rect = tuple[float, float, float, float]


@dataclass
class Token:
    text: str
    rect: Rect
    chars: list[Rect]  # one box per character of ``text``
    line: int
    source: str
    conf: float = 1.0


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
        by_line: dict[int, pymupdf.Rect] = {}
        for t, a, b in self.token_range(start, end):
            r = pymupdf.Rect()
            for c in t.chars[a:b]:
                r |= c
            if r.is_empty:
                continue
            by_line[t.line] = by_line[t.line] | r if t.line in by_line else r
        return [tuple(r + (-pad, -pad, pad, pad)) for r in by_line.values()]


# ------------------------------------------------------------------ native ---
_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES
)  # no MEDIABOX_CLIP: off-page text too


def page_number(page: pymupdf.Page) -> int:
    """Zero-based number of a page that belongs to a document."""
    number = page.number
    if number is None:
        raise ValueError("page does not belong to a document")
    return number


def _flush(
    tokens: list[Token], text: list[str], chars: list[Rect], line_no: int
) -> None:
    """Close the current word: append it as a token and reset the buffers."""
    if not text:
        return
    r = pymupdf.Rect()
    for c in chars:
        r |= c
    tokens.append(
        Token("".join(text), (r.x0, r.y0, r.x1, r.y1), list(chars), line_no, "native")
    )
    text.clear()
    chars.clear()


def native_layer(page: pymupdf.Page) -> TextLayer:
    """Tokens from the PDF text layer, with exact per-character boxes.
    Includes invisible text (white, render mode 3), which must be redacted too."""
    tokens: list[Token] = []
    line_no = 0
    for block in page.get_text("rawdict", flags=_FLAGS)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            cur_txt: list[str] = []
            cur_chars: list[Rect] = []

            horizontal = abs(line["dir"][0]) >= abs(line["dir"][1])
            for span in line["spans"]:
                for ch in span["chars"]:
                    c = ch["c"]
                    if c.isspace():
                        _flush(tokens, cur_txt, cur_chars, line_no)
                        continue
                    if cur_chars:  # words written without space characters (e.g. scanner OCR layers)
                        prev, bb = cur_chars[-1], ch["bbox"]
                        size = max(span["size"], 1)
                        # distance between the two character boxes along the writing direction
                        gap = (
                            (bb[0] - prev[2])
                            if horizontal
                            else max(prev[1] - bb[3], bb[1] - prev[3])
                        )
                        if gap > 0.12 * size:
                            _flush(tokens, cur_txt, cur_chars, line_no)
                    cur_txt.append(c)
                    cur_chars.append(tuple(ch["bbox"]))
            _flush(tokens, cur_txt, cur_chars, line_no)
            line_no += 1
    return TextLayer.build(page_number(page), "native", tokens)


def ocr_layer(page_no: int, words) -> TextLayer:
    """Tokens from OCR words; character boxes are interpolated inside the word box."""
    tokens = []
    for w in words:
        x0, y0, x1, y1 = w.rect
        n = max(len(w.text), 1)
        if (y1 - y0) > 1.5 * (
            x1 - x0
        ) and n > 2:  # vertical word: characters run bottom to top
            step = (y1 - y0) / n
            chars = [
                (x0, y1 - (i + 1) * step, x1, y1 - i * step) for i in range(len(w.text))
            ]
        else:
            step = (x1 - x0) / n
            chars = [
                (x0 + i * step, y0, x0 + (i + 1) * step, y1) for i in range(len(w.text))
            ]
        tokens.append(Token(w.text, w.rect, chars, w.line, w.source, w.conf))
    return TextLayer.build(page_no, "ocr", tokens)
