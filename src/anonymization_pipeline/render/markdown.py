"""Markdown renderer: the values found by the detection replaced in the text of every page.

Values with the same normalized form in the same family share a tag; the replacement is
mechanical: every detected form is replaced in the WHOLE document, so the encrypted map allows an
exact restore and the non-sensitive text stays identical. The same values are the boxes of the
PDF: one detection, two outputs.
"""

from collections import Counter
from dataclasses import dataclass, field

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.text import CELL_BORDER, find, replacement_spans

# a chosen replacement: start, end, index of the group
Span = tuple[int, int, int]


@dataclass
class Group:
    """Values that refer to the same entity: they get the same tag."""

    category: str
    values: list[tuple[str, str]]  # (text, category), IN CLEAR TEXT
    tag: str = ""


@dataclass(frozen=True)
class Occurrence:
    """A replacement in the redacted text: offsets in the redacted text and original value.

    A value across table cells is replaced cell by cell: every piece is an occurrence with its
    own ``original`` and the whole value in ``whole``.
    """

    start: int
    end: int
    replacement: str
    original: str
    category: str
    whole: str | None = None

    @property
    def value(self) -> str:
        """The whole value (what must not be left in clear text anywhere)."""
        return self.whole or self.original


@dataclass(frozen=True)
class Leftover:
    """A replaced value still in clear text (category and page only, never the value)."""

    category: str
    page: int  # one-based


@dataclass
class Rendered:
    """The redacted Markdown of the document."""

    markdown: str
    occurrences: list[Occurrence] = field(
        default_factory=list
    )  # offsets in ``markdown``
    groups: list[Group] = field(default_factory=list)


def group_values(values: set[tuple[str, str]]) -> list[Group]:
    """Values with the same form (up to spaces and case) in the same family -> same group.

    Different families never share a group (a name never shares its tag with an address); the
    group takes the most informative category of its values.
    """
    reg = registry()
    sets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for text, category in sorted(values):
        key = reg.family(category), " ".join(text.split()).lower()
        sets.setdefault(key, []).append((text, category))
    out = []
    for members in sets.values():
        category = max(members, key=lambda v: (reg.rank(v[1]), -members.index(v)))[1]
        out.append(Group(category=category, values=members))
    return out


def select_spans(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> list[list[Span]]:
    """Where every form of every group is in each text; on overlaps the longest span wins."""
    anchoring = cfg.detection.anchoring.model_copy(
        update={"fuzzy": 0}
    )  # exact substrings
    filtered = cfg.detection.detectors.eponyms
    forms: dict[str, int] = {}
    for gi, group in enumerate(groups):
        for text, _ in group.values:
            forms.setdefault(text, gi)
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


def _cells(text: str, s: int, e: int) -> list[tuple[int, int]]:
    """The pieces of ``text[s:e]`` in each table cell: the borders stay in the text."""
    out = []
    start = s
    for border in CELL_BORDER.finditer(text, s, e):
        out.append((start, border.start()))
        start = border.end()
    out.append((start, e))
    return [(ps, pe) for ps, pe in out if pe > ps]


def _apply(
    text: str, spans: list[Span], replacements: dict[int, str], groups: list[Group]
) -> tuple[str, list[Occurrence]]:
    parts: list[str] = []
    found: list[Occurrence] = []
    cursor = length = 0
    for s, e, gi in spans:
        replacement = replacements[gi]
        pieces = _cells(text, s, e)
        whole = " ".join(text[ps:pe] for ps, pe in pieces) if len(pieces) > 1 else None
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


def redact_texts(
    texts: list[str], groups: list[Group], cfg: PipelineConfig
) -> tuple[list[str], list[list[Occurrence]]]:
    """Replace every form of every group in every page.

    On overlaps the longest span wins; tags are numbered per category in order of first appearance
    in the document.
    """
    c = cfg.redaction
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


def leftovers(
    texts: list[str], values: set[tuple[str, str]], cfg: PipelineConfig
) -> list[Leftover]:
    """``(value, category)`` pairs still in clear text in ``texts``.

    The occurrences redaction leaves on purpose ("globuli rossi", an eponym) are not leftovers.
    """
    anchoring = cfg.detection.anchoring.model_copy(update={"fuzzy": 0})
    filtered = cfg.detection.detectors.eponyms
    out: list[Leftover] = []
    for i, text in enumerate(texts):
        exclude = replacement_spans(text, cfg.redaction)
        out += [
            Leftover(c, i + 1)
            for v, c in values
            if any(
                not (filtered and eponyms.is_excluded(text, s, e, c))
                for s, e in find(text, v, anchoring, exclude)
            )
        ]
    return out


def render(
    texts: list[str], values: set[tuple[str, str]], cfg: PipelineConfig
) -> tuple[Rendered, list[Leftover]]:
    """The redacted Markdown of the pages, joined with the page separator, and the values left
    in clear text (an empty list when the redaction is complete)."""
    groups = group_values(values)
    pages, occurrences = redact_texts(texts, groups, cfg)
    left = leftovers(pages, values, cfg)
    parts: list[str] = []
    joined: list[Occurrence] = []
    length = 0
    for i, (page, found) in enumerate(zip(pages, occurrences, strict=True)):
        separator = cfg.markdown.page_separator.replace("{n}", str(i + 1)) if i else ""
        length += len(separator)
        joined += [
            Occurrence(
                o.start + length,
                o.end + length,
                o.replacement,
                o.original,
                o.category,
                o.whole,
            )
            for o in found
        ]
        parts.append(separator + page)
        length += len(page)
    return Rendered("".join(parts), joined, groups), left
