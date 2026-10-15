"""Text layers: page text with a link from every character back to its box.

A page can have several layers (native text layer, OCR). Detection runs on ``layer.text``;
``layer.rects(start, end)`` turns a detected span back into one rectangle per line, which is
what redaction needs. Rectangles are in display space (``pdf.geometry``).
"""

from dataclasses import dataclass, field

from anonymization_pipeline.pdf.geometry import EMPTY, Rect, grow, is_empty, union


@dataclass
class Token:
    """A word of a layer: its text and one box per character."""

    text: str
    rect: Rect
    chars: list[Rect]  # one box per character of ``text``
    line: int
    source: str
    conf: float = 1.0


@dataclass
class TextLayer:
    """The words of one page from one source, joined into a searchable text.

    Words on the same line are joined by a space, lines by a newline: ``starts[i]`` is the
    offset of ``tokens[i]`` in ``text``.
    """

    page: int
    source: str  # native | ocr
    tokens: list[Token]
    text: str = ""
    starts: list[int] = field(default_factory=list)

    @classmethod
    def build(cls, page: int, source: str, tokens: list[Token]) -> "TextLayer":
        """Join the tokens into the layer text.

        Args:
            page: Zero-based page number.
            source: ``native`` or ``ocr``.
            tokens: Words in reading order.
        """
        parts: list[str] = []
        starts: list[int] = []
        pos = 0
        prev_line: int | None = None
        for t in tokens:
            if prev_line is not None:
                parts.append(" " if t.line == prev_line else "\n")
                pos += 1
            starts.append(pos)
            parts.append(t.text)
            pos += len(t.text)
            prev_line = t.line
        return cls(page, source, tokens, "".join(parts), starts)

    def token_range(self, start: int, end: int) -> list[tuple[Token, int, int]]:
        """Tokens overlapping ``[start, end)`` with the overlapping character range in each."""
        out = []
        for t, s in zip(self.tokens, self.starts, strict=True):
            e = s + len(t.text)
            if e <= start or s >= end:
                continue
            out.append((t, max(start, s) - s, min(end, e) - s))
        return out

    def rects(self, start: int, end: int, pad: float = 0.0) -> list[Rect]:
        """One rectangle per line covering the characters in ``[start, end)``.

        Args:
            start: First character offset in ``text``.
            end: Offset after the last character.
            pad: Points added on every side of each rectangle.
        """
        by_line: dict[int, Rect] = {}
        for t, a, b in self.token_range(start, end):
            r = EMPTY
            for c in t.chars[a:b]:
                r = union(r, c)
            if is_empty(r):
                continue
            by_line[t.line] = union(by_line.get(t.line, EMPTY), r)
        return [grow(r, pad) for r in by_line.values()]
