"""Redaction: consistent tags, replacement in the Markdown.

Values with the same normalized form in the same family share a tag; the replacement is
mechanical: every detected form is replaced in the WHOLE document, so the encrypted map allows an
exact restore and the non-sensitive text stays identical.
"""

from collections import Counter

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.domain import Entity, Group, Occurrence
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.text import find


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


def redact(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> tuple[list[str], list[list[Occurrence]]]:
    """Replace every form of every group in every page.

    On overlaps the longest span wins; tags are numbered per category in order of first appearance
    in the document.
    """
    c = cfg.phases.redaction
    anchoring = cfg.phases.detection.anchoring.model_copy(
        update={"fuzzy": 0}
    )  # forms are exact substrings
    filtered = cfg.phases.detection.detectors.eponyms
    forms: dict[str, int] = {}
    for gi, group in enumerate(groups):
        for entity in group.values:
            forms.setdefault(entity.text, gi)
    per_page: list[list[tuple[int, int, int]]] = []
    for text in texts:
        candidates = [
            (s, e, gi)
            for form, gi in forms.items()
            for s, e in find(text, form, anchoring)
            if not (filtered and eponyms.is_excluded(text, s, e, groups[gi].category))
        ]
        chosen: list[tuple[int, int, int]] = []
        for s, e, gi in sorted(candidates, key=lambda x: (-(x[1] - x[0]), x[0])):
            if all(e <= s2 or s >= e2 for s2, e2, _ in chosen):
                chosen.append((s, e, gi))
        per_page.append(sorted(chosen))
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
        parts: list[str] = []
        found: list[Occurrence] = []
        cursor = length = 0
        for s, e, gi in spans:
            parts.append(text[cursor:s])
            length += s - cursor
            replacement = replacements[gi]
            found.append(
                Occurrence(
                    start=length,
                    end=length + len(replacement),
                    replacement=replacement,
                    original=text[s:e],
                    category=groups[gi].category,
                )
            )
            parts.append(replacement)
            length += len(replacement)
            cursor = e
        parts.append(text[cursor:])
        pages.append("".join(parts))
        occurrences.append(found)
    return pages, occurrences
