"""Redaction: coreference with the VLM, consistent tags, replacement in the Markdown.

The model decides which values refer to the same entity (coreference); the replacement is
mechanical: every detected form is replaced in the WHOLE document, so the encrypted map allows an
exact restore and the non-sensitive text stays identical.
"""

import logging
import re
from collections import Counter
from typing import Any

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.domain import Entity, Group, Occurrence
from anonymization_pipeline.pipeline_config import (
    PipelineConfig,
    Registry,
    registry,
    render,
)
from anonymization_pipeline.text import find
from anonymization_pipeline.vlm import VLMClient, VLMError
from anonymization_pipeline.vlm.responses import groups_schema, parse_groups

# coreference answer: about one number, a comma and a bracket per value, plus the JSON around;
# never under _TOKENS_MIN, for models that reason before answering (the phase limit still applies)
_TOKENS_PER_VALUE = 16
_TOKENS_BASE = 64
_TOKENS_MIN = 1024

logger = logging.getLogger(__name__)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def root(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.root(a), self.root(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _unique(entities: list[Entity]) -> list[Entity]:
    seen: set[tuple[str, str]] = set()
    out = []
    for entity in entities:
        if (entity.text, entity.category) not in seen:
            seen.add((entity.text, entity.category))
            out.append(entity)
    return out


def _exact_key(entity: Entity, reg: Registry) -> tuple[str, str]:
    return reg.family(entity.category), " ".join(entity.text.split()).lower()


def _vlm_key(entity: Entity, reg: Registry) -> tuple[str, ...]:
    """How far the code accepts a merge proposed by the model: in the free-coreference families the
    family is enough; in the others the value must match up to spaces, case and punctuation."""
    family = reg.family(entity.category)
    if family in reg.free_coreference:
        return (family,)
    return family, re.sub(r"[\W_]", "", entity.text.lower())


def _compose(values: list[Entity], uf: _UnionFind, reg: Registry) -> list[Group]:
    """Groups from the union: same normalized form and same family always go together; different
    families never (a name never shares its tag with an address, whatever the model says)."""
    first: dict[tuple[str, str], int] = {}
    for i, entity in enumerate(values):
        key = _exact_key(entity, reg)
        if key in first:
            uf.union(first[key], i)
        else:
            first[key] = i
    sets: dict[tuple[int, str], list[Entity]] = {}
    for i, entity in enumerate(values):
        sets.setdefault((uf.root(i), reg.family(entity.category)), []).append(entity)
    out = []
    for members in sets.values():
        category = max(
            members, key=lambda e: (reg.rank(e.category), -members.index(e))
        ).category
        out.append(Group(category=category, values=members))
    return out


async def group_entities(
    entities: list[Entity], client: VLMClient, cfg: PipelineConfig
) -> tuple[list[Group], dict[str, Any]]:
    """Coreference: values of the same entity -> same group (and same tag)."""
    reg = registry()
    values = _unique(entities)
    uf = _UnionFind(len(values))
    info: dict[str, Any] = {"method": cfg.phases.redaction.coreference, "groups": 0}
    if cfg.phases.redaction.coreference == "vlm" and len(values) > 1:
        lines = "\n".join(
            f"{i}. [{e.category}] {e.text}" for i, e in enumerate(values, 1)
        )
        try:
            groups, _ = await client.call_json(
                "redaction",
                render(cfg, "redaction", "system"),
                render(cfg, "redaction", "user", values=lines),
                None,
                groups_schema(len(values)),
                parse_groups,
                max_tokens=max(
                    _TOKENS_MIN, _TOKENS_BASE + _TOKENS_PER_VALUE * len(values)
                ),
            )
            used: set[int] = set()
            for group in groups:
                ids = [
                    i - 1 for i in group if 1 <= i <= len(values) and i - 1 not in used
                ]  # each value once
                used.update(ids)
                blocks: dict[tuple[str, ...], list[int]] = {}
                for i in ids:
                    blocks.setdefault(_vlm_key(values[i], reg), []).append(i)
                for block in blocks.values():
                    for i in block[1:]:
                        uf.union(block[0], i)
        except VLMError as exc:
            # coreference is not about data protection: fall back to the exact form
            logger.warning(
                "Coreference with the VLM failed, using the exact form: %s",
                type(exc).__name__,
            )
            info["method"] = "exact (fallback)"
    out = _compose(values, uf, reg)
    info["groups"] = len(out)
    return out, info


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
