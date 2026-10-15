"""Geometric scoring of one document: ground truth occurrences against the redaction boxes.

A redacted page of the service holds no text (it is rasterised), so the scoring cannot look for
the values in the output: it compares the boxes the service applied with the position of every
occurrence of every ground truth value in the original PDF (``locate``).

- A character is covered when its centre is inside a box of its page.
- A value is *detected* when at least one occurrence has ``DETECTED`` of its characters covered,
  *protected* when its occurrences not covered in full are at most its ``unmarked`` copies (the
  same string used as plain text, which the service may rightly leave).
- A value is *expected* when its category, or its generic category, is active for the profile.
- A box is correct when it covers a character of an expected value, *disabled* when it covers only
  values of inactive categories, a false positive otherwise.
- A negative (an eponym, a scale) is violated when an occurrence is covered by a box of a
  forbidden family and is not part of a value of that family ("Neri" in the surname of a relative).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from anonval.locate import Occurrence, PageText, locate
from anonval.text import alnum_count, find
from anonymization_pipeline.pdf.geometry import Rect, center, contains_point
from anonymization_pipeline.pipeline_config.categories import registry

DETECTED = 0.5
STRONG_MIN = 6  # letters and digits of a value searched in the text of the released PDF


@dataclass(frozen=True)
class Box:
    page: int  # zero-based
    rect: Rect
    category: str


@dataclass
class ValueOutcome:
    value: str
    category: str
    family: str
    expected: bool
    occurrences: int  # located in the original PDF
    unmarked: int
    coverage: float  # best occurrence
    uncovered: int  # occurrences not covered in full
    pages: list[int]  # one-based

    @property
    def located(self) -> bool:
        return self.occurrences > 0

    @property
    def detected(self) -> bool:
        return self.coverage >= DETECTED

    @property
    def protected(self) -> bool:
        return self.uncovered <= self.unmarked

    @property
    def leaked(self) -> bool:
        return self.expected and self.located and not self.protected


@dataclass(frozen=True)
class Hit:
    """A box the ground truth does not justify, or a violated negative."""

    page: int  # one-based
    category: str
    value: str | None = None  # negatives only (synthetic data)


@dataclass
class DocScore:
    id: str
    type: str
    difficulty: str
    status: str  # done | blocked | failed | cancelled | error
    reason: str | None = None
    error: str | None = None
    values: list[ValueOutcome] = field(default_factory=list)
    boxes: int = 0
    correct_boxes: int = 0
    disabled_boxes: int = 0
    false_positives: list[Hit] = field(default_factory=list)
    negatives_violated: list[Hit] = field(default_factory=list)
    # values in the text of the released PDF
    text_leaks: list[str] = field(default_factory=list)
    markdown: bool = False  # a Markdown was released and scored
    markdown_leaks: list[str] = field(
        default_factory=list
    )  # expected values readable in it
    pipeline_s: float | None = None

    @property
    def released(self) -> bool:
        return self.status == "done"


def active_categories(
    catalog: Iterable[dict[str, Any]],
    enable: list[str] | None,
    disable: list[str] | None,
) -> set[str]:
    """Categories active for a profile: the service defaults, plus ``enable``, minus
    ``disable`` (groups or categories; ``disable`` wins)."""
    reg = registry()
    active = {c["category"] for c in catalog if c["default"]}
    active |= set(reg.expand(enable or []))
    active -= set(reg.expand(disable or []))
    return active


def expected(category: str, active: set[str]) -> bool:
    """The service redacts the category, itself or as its generic one."""
    return category in active or registry().generic.get(category) in active


def _covered(char: Rect, boxes: list[Box], page: int) -> bool:
    x, y = center(char)
    return any(b.page == page and contains_point(b.rect, x, y) for b in boxes)


def _coverage(occurrence: Occurrence, boxes: list[Box]) -> float:
    covered = sum(_covered(c, boxes, occurrence.page) for c in occurrence.chars)
    return covered / len(occurrence.chars)


def _inside(char: Rect, box: Box, page: int) -> bool:
    return box.page == page and contains_point(box.rect, *center(char))


def boxes_of(status: dict[str, Any]) -> list[Box]:
    """Redaction boxes of a finished job (pages made zero-based)."""
    result = status.get("result") or {}
    return [
        Box(b["page"] - 1, tuple(b["rect"]), b["category"])  # type: ignore[arg-type]
        for b in result.get("boxes", [])
    ]


def _strong(value: str, family: str) -> bool:
    if family == "PERSONA":
        return len(value.split()) >= 2
    return alnum_count(value) >= STRONG_MIN


# tags ([PERSONA_1]) and redaction blocks of an anonymized Markdown
_REPLACEMENT = re.compile(r"\[[A-Z0-9_]+_\d+\]|█+")


def markdown_leaks(values: list[ValueOutcome], markdown: str) -> list[str]:
    """Expected values still readable in the Markdown, beyond their unmarked copies.

    Replacements are blanked first: a value equal to a category name ("gravidanza") is not a leak.
    """
    text = _REPLACEMENT.sub(" ", markdown)
    return [
        v.value
        for v in values
        if v.expected and v.located and len(find(text, v.value)) > v.unmarked
    ]


def score_document(
    gold: dict[str, Any],
    pages: list[PageText],
    status: dict[str, Any],
    active: set[str],
    released: list[PageText] | None = None,
    markdown: str | None = None,
) -> DocScore:
    """Score one document.

    Args:
        gold: Ground truth of the document.
        pages: Text and character boxes of the original PDF (``locate.page_texts``).
        status: Final status of the job (``GET /anonymizations/{id}``).
        active: Active categories of the profile.
        released: Text of the released PDF, when there is one.
        markdown: The released Markdown, when there is one.
    """
    reg = registry()
    score = DocScore(
        gold["id"],
        gold["type"],
        gold["difficulty"],
        status["status"],
        reason=status.get("reason"),
        error=status.get("error"),
    )
    result = status.get("result") or {}
    score.pipeline_s = (result.get("timings") or {}).get("total")
    if not score.released:
        return score
    boxes = boxes_of(status)
    score.boxes = len(boxes)
    located: list[tuple[ValueOutcome, list[Occurrence]]] = []
    for entry in gold["entities"]:
        occurrences = locate(pages, entry["value"])
        coverages = [_coverage(o, boxes) for o in occurrences]
        outcome = ValueOutcome(
            value=entry["value"],
            category=entry["category"],
            family=reg.family(entry["category"]),
            expected=expected(entry["category"], active),
            occurrences=len(occurrences),
            unmarked=entry.get("unmarked", 0),
            coverage=max(coverages, default=0.0),
            uncovered=sum(1 for c in coverages if c < 1.0),
            pages=sorted({o.page + 1 for o in occurrences}),
        )
        score.values.append(outcome)
        located.append((outcome, occurrences))

    for box in boxes:
        hit = [
            v
            for v, occurrences in located
            if any(_inside(c, box, o.page) for o in occurrences for c in o.chars)
        ]
        if any(v.expected for v in hit):
            score.correct_boxes += 1
        elif hit:
            score.disabled_boxes += 1
        else:
            score.false_positives.append(Hit(box.page + 1, box.category))

    for negative in gold.get("negatives", []):
        families = {reg.family(c) for c in negative["forbidden_categories"]}
        forbidden = [b for b in boxes if reg.family(b.category) in families]
        for occurrence in locate(pages, negative["value"]):
            if _coverage(occurrence, forbidden) < DETECTED:
                continue
            same_family = [
                occ
                for v, occs in located
                if v.family in families
                for occ in occs
                if occ.page == occurrence.page
            ]
            if any(set(occurrence.chars) <= set(occ.chars) for occ in same_family):
                continue  # inside a value of that family: a correct redaction
            score.negatives_violated.append(
                Hit(occurrence.page + 1, forbidden[0].category, negative["value"])
            )

    if markdown is not None:
        score.markdown = True
        score.markdown_leaks = markdown_leaks(score.values, markdown)
    if released is not None:
        text = "\n".join(p.text for p in released)
        score.text_leaks = [
            v.value
            for v in score.values
            if v.expected
            and _strong(v.value, v.family)
            and len(find(text, v.value)) > v.unmarked
        ]
    return score
