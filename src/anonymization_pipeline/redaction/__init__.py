"""Redaction: consistent tags, replacement in the Markdown.

Values with the same normalized form in the same family share a tag; the replacement is
mechanical: every detected form is replaced in the WHOLE document, so the encrypted map allows an
exact restore and the non-sensitive text stays identical.
"""

import bisect
from collections import Counter

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.domain import Entity, Group, Occurrence, Residual
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.text import CELL_BORDER, find, replacement_spans

# a chosen replacement: start, end, index of the group
Span = tuple[int, int, int]
_JOIN = "\n\n"  # between consecutive texts, as between paragraphs of the Markdown


def group_entities(entities: list[Entity]) -> list[Group]:
    """Values with the same form (up to spaces and case) in the same family -> same group.

    Different families never share a group (a name never shares its tag with an address); the
    group takes the most informative category of its values.
    """
    reg = registry()
    sets: dict[tuple[str, str], list[Entity]] = {}
    for entity in entities:
        key = reg.family(entity.category), " ".join(entity.text.split()).lower()
        members = sets.setdefault(key, [])
        if all((m.text, m.category) != (entity.text, entity.category) for m in members):
            members.append(entity)
    out = []
    for members in sets.values():
        category = max(
            members, key=lambda e: (reg.rank(e.category), -members.index(e))
        ).category
        out.append(Group(category=category, values=members))
    return out


def select_spans(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> list[list[Span]]:
    """Where every form of every group is in each text; on overlaps the longest span wins."""
    anchoring = cfg.phases.detection.anchoring.model_copy(
        update={"fuzzy": 0}
    )  # forms are exact substrings
    filtered = cfg.phases.detection.detectors.eponyms
    forms: dict[str, int] = {}
    for gi, group in enumerate(groups):
        for entity in group.values:
            forms.setdefault(entity.text, gi)
    out: list[list[Span]] = []
    for text in texts:
        candidates = [
            (s, e, gi)
            for form, gi in forms.items()
            for s, e in find(text, form, anchoring)
            if not (filtered and eponyms.is_excluded(text, s, e, groups[gi].category))
        ]
        chosen: list[Span] = []
        for s, e, gi in sorted(candidates, key=lambda x: (-(x[1] - x[0]), x[0])):
            if all(e <= s2 or s >= e2 for s2, e2, _ in chosen):
                chosen.append((s, e, gi))
        out.append(sorted(chosen))
    return out


def _apply(
    text: str,
    spans: list[Span],
    replacements: dict[int, str],
    groups: list[Group],
    wholes: dict[tuple[int, int], str] | None = None,
) -> tuple[str, list[Occurrence]]:
    parts: list[str] = []
    found: list[Occurrence] = []
    cursor = length = 0
    for s, e, gi in spans:
        replacement = replacements[gi]
        pieces = _cells(text, s, e)
        whole = (
            " ".join(text[ps:pe] for ps, pe in pieces)
            if len(pieces) > 1
            else (wholes or {}).get((s, e))
        )
        for ps, pe in pieces:
            parts.append(text[cursor:ps])
            length += ps - cursor
            found.append(
                Occurrence(
                    start=length,
                    end=length + len(replacement),
                    replacement=replacement,
                    original=text[ps:pe],
                    category=groups[gi].category,
                    whole=whole,
                )
            )
            parts.append(replacement)
            length += len(replacement)
            cursor = pe
    parts.append(text[cursor:])
    return "".join(parts), found


def _cells(text: str, s: int, e: int) -> list[tuple[int, int]]:
    """The pieces of ``text[s:e]`` in each table cell: the borders stay in the text."""
    out = []
    start = s
    for border in CELL_BORDER.finditer(text, s, e):
        out.append((start, border.start()))
        start = border.end()
    out.append((start, e))
    return [(ps, pe) for ps, pe in out if pe > ps]


def redact(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> tuple[list[str], list[list[Occurrence]]]:
    """Replace every form of every group in every page.

    On overlaps the longest span wins; tags are numbered per category in order of first appearance
    in the document.
    """
    c = cfg.phases.redaction
    per_page = select_spans(texts, groups, cfg)
    replacements: dict[int, str] = {}
    counters: Counter[str] = Counter()
    for spans in per_page:
        for _, _, gi in spans:
            if gi not in replacements:
                category = groups[gi].category
                counters[category] += 1
                replacements[gi] = (
                    c.redaction_symbol
                    if c.mode == "redaction"
                    else c.tag_format.format(category=category, n=counters[category])
                )
    for gi, group in enumerate(groups):
        group.tag = replacements.get(gi, "")
    pages: list[str] = []
    occurrences: list[list[Occurrence]] = []
    for text, spans in zip(texts, per_page, strict=True):
        page, found = _apply(text, spans, replacements, groups)
        pages.append(page)
        occurrences.append(found)
    return pages, occurrences


def retag_across(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> tuple[list[str], list[list[Occurrence]]]:
    """Replace the forms in other texts with the tags ``redact`` already gave the groups.

    ``texts`` are in reading order: a value split between consecutive texts gets its tag in each
    of them, and every piece is an occurrence with the whole value. Groups without a tag (never
    found in the document) are left alone.
    """
    replacements = {gi: g.tag for gi, g in enumerate(groups) if g.tag}
    joined = _JOIN.join(texts)
    starts = []
    offset = 0
    for text in texts:
        starts.append(offset)
        offset += len(text) + len(_JOIN)
    inside = select_spans(texts, groups, cfg)
    across: list[list[Span]] = [[] for _ in texts]
    wholes: list[dict[tuple[int, int], str]] = [{} for _ in texts]
    for s, e, gi in select_spans([joined], groups, cfg)[0]:
        first = bisect.bisect_right(starts, s) - 1
        last = bisect.bisect_right(starts, e - 1) - 1
        if gi not in replacements or first == last:
            continue
        whole = " ".join(joined[s:e].split())
        for k in range(first, last + 1):
            ps = max(s, starts[k]) - starts[k]
            pe = min(e, starts[k] + len(texts[k])) - starts[k]
            ps, pe = _strip(texts[k], ps, pe)
            if pe > ps:
                across[k].append((ps, pe, gi))
                wholes[k][(ps, pe)] = whole
    out: list[str] = []
    occurrences: list[list[Occurrence]] = []
    for k, text in enumerate(texts):
        # the longest value wins, as in select_spans: a piece counts as its whole value
        candidates = [
            (e - s, s, e, gi) for s, e, gi in inside[k] if gi in replacements
        ] + [(len(wholes[k][(s, e)]), s, e, gi) for s, e, gi in across[k]]
        kept: list[Span] = []
        for _, s, e, gi in sorted(candidates, key=lambda c: (-c[0], c[1])):
            if all(e <= s2 or s >= e2 for s2, e2, _ in kept):
                kept.append((s, e, gi))
        page, found = _apply(text, sorted(kept), replacements, groups, wholes[k])
        out.append(page)
        occurrences.append(found)
    return out, occurrences


def _strip(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    return s, e


def leftovers(
    texts: list[str], values: set[tuple[str, str]], cfg: PipelineConfig
) -> list[Residual]:
    """``(value, category)`` pairs still in clear text in ``texts`` (``page`` = index + 1).

    The occurrences redaction leaves on purpose ("globuli rossi") are not leftovers.
    """
    anchoring = cfg.phases.detection.anchoring.model_copy(update={"fuzzy": 0})
    filtered = cfg.phases.detection.detectors.eponyms
    out: list[Residual] = []
    for i, text in enumerate(texts):
        exclude = replacement_spans(text, cfg)
        out += [
            Residual(c, i + 1)
            for v, c in values
            if any(
                not (filtered and eponyms.is_excluded(text, s, e, c))
                for s, e in find(text, v, anchoring, exclude)
            )
        ]
    return out
