"""Markdown export (fast mode): the anonymized text, line by line, with page delimiters.

Redacted parts become their label tag (``[NOME]``, ``[CODICE FISCALE]``). Tables
found by LiteParse become Markdown tables, each cell anonymized as any other
text; a table whose text does not all fall in its cells is exported as lines,
so no text is lost. Figures become ``[IMMAGINE]``. Same text as the Word export
(``export_docx``), without the layout of the PDF.
"""

from itertools import groupby
from pathlib import Path

from anonymization_core.export_docx import (
    Redaction,
    char_label,
    page_lines,
    redactions,
)
from anonymization_core.geometry import (
    Rect,
    contains_point,
    grow,
)
from anonymization_core.layers import Token
from anonymization_core.pipeline import Analysis
from anonymization_core.reader import Region
from anonymization_core.taxonomy import LABELS

FIGURE = "[IMMAGINE]"
# tokens are matched to cells by their centre; a tight cell of a borderless table
# is the box of its own text, so it is grown a little
CELL_MARGIN = 1.0

Grid = list[list[list[Token]]]


def export_markdown(analysis: Analysis, out: Path) -> None:
    """Write the anonymized text of ``analysis`` to ``out`` as Markdown."""
    red = redactions(analysis)
    blocks: list[str] = []
    for pno in range(len(analysis.pages)):
        blocks.append(f"<!-- pagina {pno + 1} -->")
        rects = red.get(pno, [])
        lines = page_lines(analysis, pno)
        tokens = [t for line in lines for t in line]
        # (top, markdown) of the figures and tables, in reading order
        pending: list[tuple[float, str]] = []
        claimed: set[int] = set()
        regions = sorted(
            (r for page, r in analysis.regions if page == pno),
            key=lambda r: r.rect[1],
        )
        for region in regions:
            if region.kind == "figure":
                pending.append((region.rect[1], FIGURE))
                continue
            grid = _grid(region, tokens)
            if grid is None:
                continue
            pending.append((region.rect[1], _table(grid, rects)))
            claimed |= {id(t) for row in grid for cell in row for t in cell}
        for toks in lines:
            kept = [t for t in toks if id(t) not in claimed]
            if not kept:
                continue
            top = min(t.rect[1] for t in kept)
            # a region starts above this line: it comes first in reading order
            while pending and pending[0][0] <= top:
                blocks.append(pending.pop(0)[1])
            blocks.append(_line(kept, rects))
        blocks += [md for _, md in pending]
    out.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _centre_in(r: Rect, box: Rect) -> bool:
    return contains_point(r, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _grid(region: Region, tokens: list[Token]) -> Grid | None:
    """Tokens of the table, cell by cell; None when one of them falls in no cell."""
    if not region.cells:
        return None
    cells = [
        [None if c is None else grow(c, CELL_MARGIN) for c in row]
        for row in region.cells
    ]
    grid: Grid = [[[] for _ in row] for row in cells]
    for t in tokens:
        if not _centre_in(region.rect, t.rect):
            continue
        at = next(
            (
                (i, j)
                for i, row in enumerate(cells)
                for j, c in enumerate(row)
                if c is not None and _centre_in(c, t.rect)
            ),
            None,
        )
        if at is None:
            return None
        grid[at[0]][at[1]].append(t)
    return grid


def _table(grid: Grid, red: list[Redaction]) -> str:
    """A Markdown table; the first row is its header."""
    width = max(len(row) for row in grid)
    rows = [
        [_line(cell, red).replace("|", "\\|") for cell in row]
        + [""] * (width - len(row))
        for row in grid
    ]
    rows.insert(1, ["---"] * width)
    return "\n".join(f"| {' | '.join(row)} |" for row in rows)


def _line(toks: list[Token], red: list[Redaction]) -> str:
    """Text of the tokens; every run of redacted characters becomes one tag."""
    chars: list[tuple[str, str | None]] = []
    for i, t in enumerate(toks):
        if i:
            chars.append((" ", None))
        chars += [
            (ch, char_label(red, cr)) for ch, cr in zip(t.text, t.chars, strict=False)
        ]
    # a space between two parts of the same value is part of it: "[NOME]", not "[NOME] [NOME]"
    for i in range(1, len(chars) - 1):
        left, right = chars[i - 1][1], chars[i + 1][1]
        if chars[i][0] == " " and left is not None and left == right:
            chars[i] = (" ", left)
    return "".join(
        "".join(ch for ch, _ in run) if label is None else _tag(label)
        for label, run in groupby(chars, key=lambda c: c[1])
    )


def _tag(label: str) -> str:
    known = LABELS.get(label)
    return f"[{known.tag if known else 'OMISSIS'}]"
