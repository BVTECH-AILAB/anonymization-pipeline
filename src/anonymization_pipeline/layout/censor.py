"""Layout returned with the censored document, its fail-closed check and its restore.

``censor`` keeps the position of every character outside the replaced values; a replaced value
becomes one ``tag`` run with the box it took on its first line (its other lines are dropped), so
no geometry of single words or characters of a value survives. ``restore`` puts the original
values back in those boxes, with the entries of the map.
"""

from collections import Counter
from typing import Any

from anonymization_pipeline.layout.align import ClearField, ClearRun
from anonymization_pipeline.layout.model import (
    FieldLayout,
    Layout,
    LayoutError,
    PageLayout,
    Run,
)
from anonymization_pipeline.layout.pdf import Extraction
from anonymization_pipeline.redaction.structure import (
    Censored,
    Field,
    StructureLeak,
    entries,
    fields,
)

_BASELINE = 0.5  # points: pieces of a value on the same line

# a replacement: offsets in the original text, offsets in the censored text
_Span = tuple[int, int, int, int]


def _spans(found: list[tuple[int, int, str, str]]) -> list[_Span]:
    out: list[_Span] = []
    shift = 0
    for start, end, _, original in sorted(found):
        out.append((start - shift, start - shift + len(original), start, end))
        shift += (end - start) - len(original)
    return out


def _piece(run: ClearRun, start: int, end: int) -> tuple[float, float]:
    """Left edge and width of the characters ``[start, end)`` of ``run``."""
    left = run.xs[start - run.start]
    return left, run.xs[end - run.start] - left


def _censor_field(clear: ClearField, spans: list[_Span]) -> list[Run] | None:
    out: list[Run] = []
    cuts = sorted({s for span in spans for s in span[:2]})
    for run in clear.runs:
        bounds = sorted(
            {run.start, run.end, *(c for c in cuts if run.start < c < run.end)}
        )
        for start, end in zip(bounds, bounds[1:], strict=False):
            if any(os <= start and end <= oe for os, oe, _, _ in spans):
                continue
            shift = sum(
                (ce - cs) - (oe - os) for os, oe, cs, ce in spans if oe <= start
            )
            x, width = _piece(run, start, end)
            out.append(
                Run(
                    start=start + shift,
                    end=end + shift,
                    x=x,
                    baseline=run.baseline,
                    width=max(width, 0.0),
                    style=run.style,
                )
            )
    for os, oe, cs, ce in spans:
        pieces = [
            (run, max(run.start, os), min(run.end, oe))
            for run in clear.runs
            if run.start < oe and os < run.end
        ]
        if not pieces:
            return None
        first = pieces[0][0]
        boxes = [
            _piece(run, start, end)
            for run, start, end in pieces
            if abs(run.baseline - first.baseline) <= _BASELINE
        ]
        left = min(x for x, _ in boxes)
        right = max(x + w for x, w in boxes)
        out.append(
            Run(
                start=cs,
                end=ce,
                x=left,
                baseline=first.baseline,
                width=max(right - left, 0.0),
                style=first.style,
                tag=True,
            )
        )
    return sorted(out, key=lambda r: r.start)


def censor(
    clear: dict[Field, ClearField],
    extraction: Extraction,
    censored: Censored,
    counts: Counter[str],
) -> Layout:
    """The layout of the censored structure: offsets of the censored texts, tags in their boxes."""
    found = entries(censored.elements)
    out: list[FieldLayout] = []
    for (ref, cell), field in clear.items():
        runs = _censor_field(field, _spans(found.get((ref, cell), [])))
        if runs is None:
            counts["fields_fallback"] += 1
            continue
        out.append(FieldLayout(ref=ref, cell=cell, page_no=field.page_no, runs=runs))
    pages = [
        PageLayout(page_no=n, source="pdf", graphics=g.graphics)
        for n, g in sorted(extraction.pages.items())
    ]
    counts["fields_styled"] += len(out)
    return Layout(styles=extraction.styles, pages=pages, fields=out)


def check(layout: Layout, censored: Censored) -> None:
    """Fail closed if the layout does not fit the censored structure.

    Every field exists once, every run is inside its text, the tag runs are exactly the tags of
    the field and no other run touches a tag.

    Raises:
        StructureLeak: The layout cannot be returned.
    """
    texts = dict(fields(censored.document))
    pages = {p.page_no for p in layout.pages}
    seen: set[Field] = set()
    for f in layout.fields:
        where = (f.ref, f.cell)
        if where not in texts or where in seen or f.page_no not in pages:
            raise StructureLeak("layout field not in the structure")
        seen.add(where)
        length = len(texts[where])
        marks = set(censored.marks.get(where, []))
        tags = {(r.start, r.end) for r in f.runs if r.tag}
        if tags != marks:
            raise StructureLeak("layout tags do not match the structure")
        for r in f.runs:
            if not 0 <= r.start < r.end <= length or r.style >= len(layout.styles):
                raise StructureLeak("layout run outside its field")
            if not r.tag and any(r.start < e and s < r.end for s, e in marks):
                raise StructureLeak("layout run over a tag")


def restore(layout: Layout, elements: list[Any]) -> Layout:
    """The layout of the original structure: every tag run holds the original value again.

    Raises:
        LayoutError: The map does not match the layout.
    """
    found = entries(elements)
    out: list[FieldLayout] = []
    for f in layout.fields:
        values = sorted(found.get((f.ref, f.cell), []))
        by_tag = {(start, end): original for start, end, _, original in values}
        if {(r.start, r.end) for r in f.runs if r.tag} != set(by_tag):
            raise LayoutError("the map does not match the layout")
        runs: list[Run] = []
        for r in f.runs:
            shift = sum(
                len(original) - (end - start)
                for start, end, _, original in values
                if end <= r.start
            )
            length = len(by_tag[(r.start, r.end)]) if r.tag else r.end - r.start
            runs.append(
                r.model_copy(
                    update={"start": r.start + shift, "end": r.start + shift + length}
                )
            )
        out.append(f.model_copy(update={"runs": runs}))
    return layout.model_copy(update={"fields": out})
