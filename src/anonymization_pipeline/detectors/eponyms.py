"""Common filter for eponyms, scales and homonymous drugs (``resources/detectors/eponyms.yaml``).

"Morbo di Parkinson", "segno di Giordano", "Glasgow 15", "Coumadin" are not personal data, but
Parkinson or Giordano can also be the surname of a patient. So the decision is taken on each
occurrence, from the text around it, whichever detector proposed the value (VLM included):

- proposed as a person (PERSONA family): eponyms and scales only in a medical context (a clinical
  term before, an outcome or, for scales, a score after) or inside a compound of the list
  ("Wolff-Parkinson-White"); nouns and drugs always; common words only in lowercase. Everywhere
  else the value is redacted: in doubt, a person;
- proposed as a drug: eponyms, scales and nouns ("manovra di Valsalva" is not a drug);
- never as a diagnosis, because the rest of the diagnosis would stay in clear text.
"""

import functools
import re
from dataclasses import dataclass
from typing import Any

from anonymization_pipeline.detectors.patterns import compile_regex, fragments
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.resources import read_yaml

FILE = "detectors/eponyms.yaml"
_LISTS = ("eponyms", "scales", "nouns", "drugs", "common_words")
_KEYS = ("fragments", "context", *_LISTS)
_CONTEXT = ("window", "before", "after", "score")


@dataclass(frozen=True)
class Lists:
    """Content of ``eponyms.yaml``, validated, entries in lowercase with single spaces.

    Attributes:
        compounds: Eponyms and scales of more than one word ("Wolff-Parkinson-White").
        before: Clinical term that ends right before an occurrence.
        after: Outcome that starts right after an occurrence.
        score: Value that starts right after a scale.
    """

    eponyms: frozenset[str]
    scales: frozenset[str]
    nouns: frozenset[str]
    drugs: frozenset[str]
    common_words: frozenset[str]
    compounds: re.Pattern[str] | None
    window: int
    before: re.Pattern[str]
    after: re.Pattern[str]
    score: re.Pattern[str]


def _normalize(s: str) -> str:
    return " ".join(s.split())


def _mapping(data: Any, keys: tuple[str, ...], where: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping")
    if missing := [k for k in keys if k not in data]:
        raise ValueError(f"{where}: missing keys {', '.join(missing)}")
    if unknown := sorted(set(map(str, data)) - set(keys)):
        raise ValueError(f"{where}: unknown keys {', '.join(unknown)}")
    return data


def _compound(entry: str) -> str:
    """Regex of a compound entry: spaces and hyphens with optional spaces around them."""
    parts = re.split(r"(\s+|-)", entry)
    return "".join(
        r"\s*-\s*" if p == "-" else r"\s+" if p.isspace() else re.escape(p)
        for p in parts
    )


@functools.cache
def lists() -> Lists:
    """Lists and context of ``eponyms.yaml`` (read and compiled at first use, then cached).

    Raises:
        ValueError: On a missing or unknown key, an entry listed twice or an invalid regex.
    """
    data = _mapping(read_yaml(FILE), _KEYS, FILE)
    seen: dict[str, str] = {}
    entries: dict[str, frozenset[str]] = {}
    for key in _LISTS:
        values = data[key]
        if not isinstance(values, list) or not all(
            isinstance(v, str) and v.strip() for v in values
        ):
            raise ValueError(f"{FILE}, {key}: expected a list of words")
        for value in (_normalize(v).lower() for v in values):
            if value in seen:
                raise ValueError(
                    f"{FILE}: '{value}' listed twice ({seen[value]}, {key})"
                )
            seen[value] = key
        entries[key] = frozenset(_normalize(v).lower() for v in values)
    raw = fragments(data["fragments"], f"{FILE}, fragments")
    context = _mapping(data["context"], _CONTEXT, f"{FILE}, context")
    window = context["window"]
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise ValueError(f"{FILE}, context.window: expected an integer >= 1")

    def regex(key: str, flags: int) -> str:
        return compile_regex(context[key], raw, f"{FILE}, context.{key}", flags).pattern

    many = sorted(
        (e for e in entries["eponyms"] | entries["scales"] if re.search(r"[\s-]", e)),
        key=len,
        reverse=True,
    )
    return Lists(
        **entries,
        compounds=(
            re.compile(rf"(?<!\w)(?:{'|'.join(map(_compound, many))})(?!\w)", re.I)
            if many
            else None
        ),
        window=window,
        before=re.compile(rf"(?:{regex('before', re.I)})[\s*_]*$", re.I),
        after=re.compile(rf"^[\s*_:|]*(?:{regex('after', re.I)})", re.I),
        score=re.compile(rf"^[\s*_:|=]*(?:{regex('score', 0)})"),
    )


def _in_compound(text: str, start: int, end: int, compounds: re.Pattern[str]) -> bool:
    """True if a compound of the list covers ``text[start:end]``."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    return any(
        m.start() + line_start <= start and end <= m.end() + line_start
        for m in compounds.finditer(text[line_start:line_end])
    )


def _medical_context(text: str, start: int, end: int, lst: Lists, scale: bool) -> bool:
    before = text[max(0, start - lst.window) : start]
    before = before[before.rfind("\n") + 1 :]
    after = text[end : end + lst.window].split("\n", 1)[0]
    return bool(
        lst.before.search(before)
        or lst.after.match(after)
        or (scale and lst.score.match(after))
    )


def is_excluded(text: str, start: int, end: int, category: str) -> bool:
    """True if the occurrence ``text[start:end]`` proposed in ``category`` is not personal data.

    Args:
        text: The whole page text, for the context of the occurrence.
        start: Start of the occurrence in ``text``.
        end: End of the occurrence in ``text``.
        category: Category the value was proposed in.
    """
    lst = lists()
    value = _normalize(text[start:end])
    key = value.lower()
    if category == "FARMACO":
        return key in lst.eponyms or key in lst.scales or key in lst.nouns
    if registry().family(category) != "PERSONA":
        return False
    if key in lst.nouns or key in lst.drugs:
        return True
    if value.islower() and key in lst.common_words:
        return True
    if lst.compounds is not None and _in_compound(text, start, end, lst.compounds):
        return True
    if key not in lst.eponyms and key not in lst.scales:
        return False
    return _medical_context(text, start, end, lst, key in lst.scales)
