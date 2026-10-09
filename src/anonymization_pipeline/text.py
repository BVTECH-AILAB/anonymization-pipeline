"""Anchoring: search in the Markdown for the values returned by the detectors.

The model output is not trusted: a value counts only if it is found in the text. The search is:
  1. literal, tolerating spaces/newlines and Markdown emphasis markers (*, _) between words;
  2. case-insensitive over ``case_insensitive_over`` characters;
  3. approximate (difflib) over the ``fuzzy`` threshold, when the literal search finds nothing.
A value that is not found is a hallucination and is discarded.
"""

import re
from difflib import SequenceMatcher

from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.pipeline_config.schema import Anchoring

Span = tuple[int, int]

_TOKEN = re.compile(r"\S+")
_PUNCTUATION = ",.;:)]}!?»\"'"
_PLACEHOLDERS = re.compile(r"(\{category\}|\{n\})")


def _normalize(s: str) -> str:
    return " ".join(s.split()).lower()


def _literal(text: str, value: str, a: Anchoring) -> list[Span]:
    pattern = r"[*_]*\s+[*_]*".join(re.escape(word) for word in value.split())
    flags = re.I if len(value) > a.case_insensitive_over else 0
    return [
        m.span() for m in re.finditer(r"(?<!\w)" + pattern + r"(?!\w)", text, flags)
    ]


def _approximate(text: str, value: str, threshold: float) -> list[Span]:
    needle = _normalize(value)
    n = len(needle.split())
    tokens = [m.span() for m in _TOKEN.finditer(text)]
    candidates: list[tuple[float, int, int]] = []
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(len(tokens) - size + 1):
            s, e = tokens[i][0], tokens[i + size - 1][1]
            while (
                e > s
                and text[e - 1] in _PUNCTUATION
                and not value.endswith(text[e - 1])
            ):
                e -= 1
            while s < e and text[s] in "([{«\"'" and not value.startswith(text[s]):
                s += 1
            candidate = _normalize(text[s:e])
            if not candidate:
                continue
            matcher = SequenceMatcher(None, needle, candidate, autojunk=False)
            if (
                matcher.real_quick_ratio() < threshold
                or matcher.quick_ratio() < threshold
            ):
                continue
            ratio = matcher.ratio()
            if ratio >= threshold:
                candidates.append((ratio, s, e))
    chosen: list[Span] = []
    for _, s, e in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if all(e <= s2 or s >= e2 for s2, e2 in chosen):
            chosen.append((s, e))
    return sorted(chosen)


def find(
    text: str, value: str, a: Anchoring, exclude: list[Span] | None = None
) -> list[Span]:
    """Occurrences of ``value`` in ``text``, outside the ``exclude`` spans (tags already inserted)."""
    value = value.strip()
    if len(value) < a.min_length:
        return []
    spans = _literal(text, value, a)
    if not spans and a.fuzzy > 0 and len(value) >= 4:
        spans = _approximate(text, value, a.fuzzy)
    if exclude:
        spans = [
            (s, e) for s, e in spans if all(e <= s2 or s >= e2 for s2, e2 in exclude)
        ]
    return spans


def replacement_regex(cfg: PipelineConfig) -> re.Pattern[str]:
    """Matches in the redacted text the tags ([PERSONA_1]) and the redaction blocks (█████)."""
    c = cfg.phases.redaction
    parts = _PLACEHOLDERS.split(c.tag_format)
    tag = "".join(
        "[A-Z0-9_]+" if p == "{category}" else r"\d+" if p == "{n}" else re.escape(p)
        for p in parts
    )
    return re.compile(f"{tag}|(?:{re.escape(c.redaction_symbol)})+")


def tag_category(replacement: str, cfg: PipelineConfig) -> str | None:
    """Category of a tag (``[PERSONA_1]`` -> ``PERSONA``); None for redaction blocks."""
    parts = _PLACEHOLDERS.split(cfg.phases.redaction.tag_format)
    pattern = "".join(
        "(?P<category>[A-Z0-9_]+)"
        if p == "{category}"
        else r"\d+"
        if p == "{n}"
        else re.escape(p)
        for p in parts
    )
    match = re.fullmatch(pattern, replacement)
    return match.group("category") if match else None


def replacement_spans(text: str, cfg: PipelineConfig) -> list[Span]:
    return [m.span() for m in replacement_regex(cfg).finditer(text)]
