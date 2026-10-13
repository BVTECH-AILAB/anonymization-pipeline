"""Tolerant text search: the same value written by the generator and extracted by docling or a VLM
may differ in case, spacing, dashes, quotes, Markdown escapes and line breaks.

Two views of a text, each with a map back to the original offsets:

- ``normal``: NFKC, lower case, one space for every whitespace run, uniform dashes and quotes;
- ``compact``: letters and digits only (values drawn one character per box, split by a table).
"""

import re
import unicodedata
from dataclasses import dataclass

_SAME = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "−": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "­": "",
    "\\": "",
}
COMPACT_MIN = 6


@dataclass(frozen=True)
class View:
    """A transformed text and, for each of its characters, the offset in the original."""

    text: str
    offsets: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Original offsets of ``text[start:end]``."""
        return self.offsets[start], self.offsets[end - 1] + 1


def normal(text: str) -> View:
    """Lower case, NFKC, single spaces, uniform dashes and quotes."""
    out: list[str] = []
    offsets: list[int] = []
    for i, char in enumerate(text):
        if char.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                offsets.append(i)
            continue
        for c in unicodedata.normalize("NFKC", _SAME.get(char, char)).lower():
            out.append(c)
            offsets.append(i)
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return View("".join(out), tuple(offsets))


def compact(text: str) -> View:
    """Letters and digits only, lower case."""
    view = normal(text)
    keep = [(c, o) for c, o in zip(view.text, view.offsets, strict=True) if c.isalnum()]
    return View("".join(c for c, _ in keep), tuple(o for _, o in keep))


def _pattern(needle: str) -> re.Pattern[str]:
    body = re.escape(needle)
    before = r"(?<!\w)" if needle[:1].isalnum() else ""
    after = r"(?!\w)" if needle[-1:].isalnum() else ""
    return re.compile(before + body + after)


def find(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Original offsets of every occurrence of ``needle`` in ``haystack``.

    Whole words in the normal view first; values of at least ``COMPACT_MIN`` letters and digits
    are then searched in the compact view too (spaced characters, values split by a line break).
    """
    target = normal(needle).text
    if not target:
        return []
    view = normal(haystack)
    spans = [
        view.span(m.start(), m.end()) for m in _pattern(target).finditer(view.text)
    ]
    if spans:
        return spans
    small = compact(needle).text
    if len(small) < COMPACT_MIN:
        return []
    view = compact(haystack)
    return [
        view.span(m.start(), m.end()) for m in re.finditer(re.escape(small), view.text)
    ]


def alnum_count(text: str) -> int:
    """Letters and digits in ``text``."""
    return sum(1 for c in text if c.isalnum())
