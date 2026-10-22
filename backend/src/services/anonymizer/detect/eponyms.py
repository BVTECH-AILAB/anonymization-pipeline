"""Eponyms, scales and drug brands that look like person names (``eponyms.yaml``).

"Morbo di Parkinson", "segno di Giordano", "Glasgow 15", "Coumadin" are not
personal data, but Parkinson or Giordano can also be a patient's surname, so the
decision is taken on each occurrence from the text around it (ported from
feat/reconstructed-architecture):

- eponyms and scales: not a person only in a medical context (a clinical term
  before, an outcome after or, for scales, a score after) or inside a compound
  of the list ("Wolff-Parkinson-White");
- nouns and drug brands: never a person;
- common words that are also surnames ("globuli rossi"): not a person only in lowercase.

Everywhere else the value stays a person: in doubt, redact.
"""

import functools
import re
from dataclasses import dataclass
from importlib import resources

import yaml

from src.services.anonymizer.detect.labels import compile_pattern

_LISTS = ("eponyms", "scales", "nouns", "drugs", "common_words")


@dataclass(frozen=True)
class Lists:
    eponyms: frozenset[str]
    scales: frozenset[str]
    nouns: frozenset[str]
    drugs: frozenset[str]
    common_words: frozenset[str]
    compounds: re.Pattern[str]
    window: int
    before: re.Pattern[str]
    after: re.Pattern[str]
    score: re.Pattern[str]


def _normalize(s: str) -> str:
    return " ".join(s.split())


def _compound(entry: str) -> str:
    """Regex of a compound entry: spaces and hyphens with optional spaces around them."""
    return "".join(
        r"\s*-\s*" if p == "-" else r"\s+" if p.isspace() else re.escape(p)
        for p in re.split(r"(\s+|-)", entry)
    )


@functools.cache
def lists() -> Lists:
    """Lists and context rules of ``eponyms.yaml`` (validated, cached)."""
    data = yaml.safe_load(
        resources.files(__package__)
        .joinpath("eponyms.yaml")
        .read_text(encoding="utf-8")
    )
    seen: dict[str, str] = {}
    entries: dict[str, frozenset[str]] = {}
    for key in _LISTS:
        values = [_normalize(str(v)).lower() for v in data[key]]
        for v in values:
            if v in seen:
                raise ValueError(f"eponyms.yaml: '{v}' listed twice ({seen[v]}, {key})")
            seen[v] = key
        entries[key] = frozenset(values)
    frags = {
        k: "|".join(f"(?:{x})" for x in v) if isinstance(v, list) else str(v)
        for k, v in data["fragments"].items()
    }
    ctx = data["context"]
    many = sorted(
        (e for e in entries["eponyms"] | entries["scales"] if re.search(r"[\s-]", e)),
        key=len,
        reverse=True,
    )
    return Lists(
        **entries,
        compounds=re.compile(
            rf"(?<!\w)(?:{'|'.join(map(_compound, many))})(?!\w)", re.I
        ),
        window=int(ctx["window"]),
        before=re.compile(
            rf"(?:{compile_pattern(ctx['before'], frags, 'before').pattern})[\s*_]*$",
            re.I,
        ),
        after=re.compile(
            rf"^[\s*_:|]*(?:{compile_pattern(ctx['after'], frags, 'after').pattern})",
            re.I,
        ),
        score=re.compile(
            rf"^[\s*_:|=]*(?:{compile_pattern(ctx['score'], frags, 'score').pattern})"
        ),
    )


def is_excluded(text: str, start: int, end: int) -> bool:
    """True if the person-like occurrence ``text[start:end]`` is not personal data."""
    lst = lists()
    value = _normalize(text[start:end])
    key = value.lower()
    if key in lst.nouns or key in lst.drugs:
        return True
    if value.islower() and key in lst.common_words:
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : len(text) if line_end < 0 else line_end]
    if any(
        m.start() + line_start <= start and end <= m.end() + line_start
        for m in lst.compounds.finditer(line)
    ):
        return True
    if key not in lst.eponyms and key not in lst.scales:
        return False
    before = text[max(line_start, start - lst.window) : start]
    after = text[end : end + lst.window].split("\n", 1)[0]
    return bool(
        lst.before.search(before)
        or lst.after.match(after)
        or (key in lst.scales and lst.score.match(after))
    )
