"""Propagation: a value found anywhere is personal data everywhere in the document.

- ``propagate``: every value found is searched in every layer (``text.find``), so a codice
  fiscale found in the header is redacted in the footer and in a hidden OCR layer too; the name
  of a person only where it is capitalised.
- ``name_variants``: the names of persons also in their other forms: a single name token
  ("Sig. Rossi" after "Mario Rossi"), with an initial ("ROSSI M.", "M. Rossi"), the full name
  inverted or glued ("Rossi Mario", "ROSSIMARIO"). A token is searched only where it is
  capitalised ("sostanza bianca" is not Dott.ssa Bianca), never after a saint's title (Ospedale
  San Camillo) and never where the eponym filter excludes it (morbo di Crohn).

Both return ``(layer, start, end, category)``; values are never logged.
"""

import re

from anonymization_pipeline.detectors import eponyms
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.text import find

Hit = tuple[int, int, int, str]

_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'][A-Za-zÀ-ÖØ-öø-ÿ'-]+")
_HONORIFIC = re.compile(
    r"^(?:sig\.?(?:ra|na)?|signor[ae]?|dott\.?(?:ssa)?|prof\.?(?:ssa)?|dr\.?|ing\.?|avv\.?)$",
    re.I,
)
_SAINT = re.compile(
    r"(?:\b(?:san|santa|sant'|santo|santi|santissima|ss\.|s\.)\s*)$", re.I
)
_INITIAL_AFTER = re.compile(r"[ \t]+[A-Z]\.")
_INITIAL_BEFORE = re.compile(r"[A-Z]\.[ \t]+$")


def same_case(value: str, category: str, text: str, start: int) -> bool:
    """False for a lowercase occurrence of a capitalised name: "sostanza bianca" is not the
    patient Bianca. Only for the PERSONA family; other values match in any case."""
    if registry().family(category) != "PERSONA":
        return True
    return not (value[:1].isupper() and text[start : start + 1].islower())


def propagate(
    layers: list[TextLayer], values: set[tuple[str, str]], cfg: PipelineConfig
) -> list[Hit]:
    """Every value in every layer.

    Args:
        layers: Text layers of the document.
        values: ``(text, category)`` already found.
        cfg: Configuration of the run (anchoring, eponym filter).
    """
    filtered = cfg.detection.detectors.eponyms
    out: list[Hit] = []
    for i, layer in enumerate(layers):
        for value, category in values:
            for s, e in find(layer.text, value, cfg.detection.anchoring):
                if not same_case(value, category, layer.text, s):
                    continue
                if filtered and eponyms.is_excluded(layer.text, s, e, category):
                    continue
                out.append((i, s, e, category))
    return out


def _tokens(value: str, min_token: int) -> list[str]:
    """Name tokens of a value: words long enough, no honorifics, drugs or nouns.

    Common words ("Rossi", "Bianchi") stay: a token is searched only where it is capitalised,
    which is how the eponym filter tells them apart too.
    """
    lists = eponyms.lists()
    out = []
    for word in _WORD.findall(value):
        key = word.lower()
        if (
            len(word) < min_token
            or _HONORIFIC.match(word)
            or key in lists.drugs
            or key in lists.nouns
        ):
            continue
        out.append(key)
    return out


def _person_values(
    values: set[tuple[str, str]], min_token: int
) -> tuple[dict[str, str], dict[tuple[str, ...], str]]:
    """Name tokens and full names (up to three tokens) of the PERSONA family, with a category."""
    reg = registry()
    tokens: dict[str, str] = {}
    full: dict[tuple[str, ...], str] = {}
    for value, category in sorted(values):
        if reg.family(category) != "PERSONA":
            continue
        words = _tokens(value, min_token)
        for word in words:
            tokens.setdefault(word, category)
        if len(words) >= 2:
            full.setdefault(tuple(words[:3]), category)
    return tokens, full


def name_variants(
    layers: list[TextLayer], values: set[tuple[str, str]], cfg: PipelineConfig
) -> list[Hit]:
    """Other forms of the names of persons found anywhere in the document.

    Args:
        layers: Text layers of the document.
        values: ``(text, category)`` already found.
        cfg: Configuration of the run.
    """
    tokens, full = _person_values(values, cfg.detection.propagation.min_token)
    filtered = cfg.detection.detectors.eponyms
    out: list[Hit] = []
    for i, layer in enumerate(layers):
        text = layer.text
        low = text.lower()
        for token, category in tokens.items():
            for m in re.finditer(rf"(?<!\w){re.escape(token)}(?!\w)", low):
                a, b = m.start(), m.end()
                if not text[a].isupper():
                    continue
                if _SAINT.search(text[max(0, a - 12) : a]):
                    continue
                if filtered and eponyms.is_excluded(text, a, b, "PERSONA"):
                    continue
                if tail := _INITIAL_AFTER.match(text, b):
                    b = tail.end()
                if head := _INITIAL_BEFORE.search(text[max(0, a - 4) : a]):
                    a -= len(head.group())
                out.append((i, a, b, category))
        for words, category in full.items():
            for order in (words, words[::-1]):
                pattern = r"\s*".join(re.escape(w) for w in order)
                for m in re.finditer(rf"(?<!\w){pattern}(?!\w)", low):
                    out.append((i, m.start(), m.end(), category))
    return out
