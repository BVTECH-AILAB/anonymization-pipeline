"""Label -> value detector: uses the typical labels of forms, reports and identity documents.

Examples:  "Cognome: ROSSI"   "Paziente: Mario Rossi"   "nato a Roma il 01/01/1980"
           "| Codice paziente | OSP-2026-048213 |"   "COGNOME / SURNAME" and "ROSSI" on the line below

Labels, value shapes and thresholds are in ``resources/detectors/labels.yaml`` (read and compiled
at first use). Ported from the anonimizzatore project without the geometric search: here the text
is the page Markdown, normalized before the search (emphasis and headings removed, table rows
turned into "label: value" pairs).
"""

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from anonymization_pipeline.detectors.base import Proposal
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.resources import read_yaml

FILE = "detectors/labels.yaml"

# value shapes the code can handle (each has its filter in _valid_value)
KINDS = ("name", "place", "org", "id", "date", "phone", "value")


@dataclass(frozen=True)
class Label:
    category: str
    regex: re.Pattern[str]
    kind: str  # name | place | org | id | date | phone | value
    need_sep: (
        bool  # requires ":" or the value on the next line (generic labels like "Nome")
    )
    stop: str  # regex: where the value ends
    max_len: int
    min_len: int
    pattern: (
        re.Pattern[str] | None
    )  # shape of the value (default for id, date and phone)


@dataclass(frozen=True)
class LabelRules:
    """Content of ``labels.yaml``, validated, with the regexes compiled."""

    labels: tuple[Label, ...]
    any_label: re.Pattern[str]  # any label (to recognize empty fields)
    separator: re.Pattern[str]
    leading_title: re.Pattern[str]  # title ("Dott.", "Sig.ra") at the start of a name
    name_token: re.Pattern[str]
    stop_values: frozenset[str]
    shapes: dict[str, dict[str, Any]]


_KEYS = (
    "fragments",
    "letter",
    "separator",
    "defaults",
    "shapes",
    "stop_values",
    "labels",
)
_DEFAULTS = ("kind", "need_sep", "stop", "max_len", "min_len")
_FRAGMENTS = ("name_token", "titles")  # also used by the code
_SHAPES = {
    "name": ("max_words", "particles"),
    "place": ("max_words", "consecutive_digits", "end"),
    "org": ("max_words", "particles", "symbols"),
    "id": ("pattern", "max_len", "max_prefix_letters", "min_chars"),
    "date": ("pattern", "max_len"),
    "phone": ("pattern", "max_len", "min_digits"),
    "value": (),
}
_LABEL_KEYS = (
    "category",
    "regex",
    "kind",
    "need_sep",
    "stop",
    "max_len",
    "min_len",
    "pattern",
)


def _keys(
    data: Any, required: Iterable[str], where: str, allowed: Iterable[str] | None = None
) -> dict:
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping")
    required = tuple(required)
    if missing := [k for k in required if k not in data]:
        raise ValueError(f"{where}: missing keys {', '.join(missing)}")
    if unknown := sorted(set(data) - set(allowed if allowed is not None else required)):
        raise ValueError(f"{where}: unknown keys {', '.join(map(str, unknown))}")
    return data


def _number(value: Any, where: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{where}: expected an integer >= {minimum}, found {value!r}")
    return value


def _words(value: Any, where: str) -> frozenset[str]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: expected a list of words")
    return frozenset(str(p).lower() for p in value)


def _substitute(text: Any, fragments: dict[str, str], where: str) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{where}: expected a regex (quoted string), found {text!r}")

    def fragment(match: re.Match[str]) -> str:
        if match.group(1) not in fragments:
            raise ValueError(
                f"{where}: unknown fragment ${{{match.group(1)}}} (defined: {', '.join(fragments) or 'none'})"
            )
        return fragments[match.group(1)]

    return re.sub(r"\$\{(\w+)\}", fragment, text)


def _regex(
    text: Any, fragments: dict[str, str], where: str, flags: int = 0
) -> re.Pattern[str]:
    source = _substitute(text, fragments, where)
    try:
        return re.compile(source, flags)
    except re.error as exc:
        raise ValueError(f"{where}: invalid regex ({exc})") from None


def _shape(
    kind: str, raw: dict, fragments: dict[str, str], where: str
) -> dict[str, Any]:
    shape: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ("pattern", "end"):
            shape[key] = _regex(
                value, fragments, f"{where}.{key}", re.I if key == "pattern" else 0
            )
        elif key == "particles":
            shape[key] = _words(value, f"{where}.{key}")
        elif key == "symbols":
            if not isinstance(value, list):
                raise ValueError(f"{where}.{key}: expected a list")
            shape[key] = tuple(str(x) for x in value)
        else:
            shape[key] = _number(value, f"{where}.{key}")
    if kind == "place":
        shape["digits_re"] = re.compile(rf"\d{{{shape['consecutive_digits']}}}")
    if kind == "id":
        shape["prefix_re"] = re.compile(rf"[A-Z]{{1,{shape['max_prefix_letters']}}}")
    return shape


@functools.cache
def rules() -> LabelRules:
    """Rules of ``labels.yaml`` (read and compiled at first use, then cached)."""
    data = _keys(read_yaml(FILE), _KEYS, f"invalid labels in {FILE}")

    # fragments, resolved in order (each one may use the previous ones)
    fragments: dict[str, str] = {}
    _keys(
        data["fragments"], _FRAGMENTS, f"{FILE}, fragments", allowed=data["fragments"]
    )
    for key, value in data["fragments"].items():
        fragments[str(key)] = _regex(
            value, fragments, f"{FILE}, fragment '{key}'"
        ).pattern

    letter = _regex(data["letter"], fragments, f"{FILE}, letter").pattern

    _keys(data["shapes"], (), f"{FILE}, shapes", allowed=data["shapes"])
    if unknown := sorted(set(map(str, data["shapes"])) - set(KINDS)):
        raise ValueError(
            f"{FILE}, shapes: unknown kinds {', '.join(unknown)} (allowed: {', '.join(KINDS)})"
        )
    shapes: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        where = f"{FILE}, shapes.{kind}"
        if kind not in data["shapes"]:
            raise ValueError(f"{where}: missing the shape of kind '{kind}'")
        raw = _keys(
            data["shapes"][kind] or {},
            _SHAPES[kind],
            where,
            (*_SHAPES[kind], "pattern", "max_len"),
        )
        shapes[kind] = _shape(kind, raw, fragments, where)

    defaults = _keys(data["defaults"], _DEFAULTS, f"{FILE}, defaults")
    labels = tuple(_labels(data["labels"], defaults, shapes, fragments, letter))
    if not labels:
        raise ValueError(f"{FILE}: no label defined")

    return LabelRules(
        labels=labels,
        any_label=re.compile("|".join(label.regex.pattern for label in labels), re.I),
        separator=_regex(data["separator"], fragments, f"{FILE}, separator"),
        leading_title=re.compile(
            r"\s*(?:" + fragments["titles"] + r")(?![A-Za-z])\s*", re.I
        ),
        name_token=re.compile(fragments["name_token"] + r"\.?"),
        stop_values=_words(data["stop_values"], f"{FILE}, stop_values"),
        shapes=shapes,
    )


def _labels(
    groups: Any,
    defaults: dict,
    shapes: dict[str, dict[str, Any]],
    fragments: dict[str, str],
    letter: str,
) -> Iterable[Label]:
    if not isinstance(groups, dict):
        raise ValueError(f"{FILE}, labels: expected a mapping group -> list of labels")
    categories = registry().descriptions
    n = 0
    for group, entries in groups.items():
        if not isinstance(entries, list):
            raise ValueError(f"{FILE}, group '{group}': expected a list of labels")
        for entry in entries:
            n += 1
            category = entry.get("category", "?") if isinstance(entry, dict) else "?"
            where = f"{FILE}, label {n} ({category}, group '{group}')"
            _keys(entry, ("category", "regex"), where, _LABEL_KEYS)
            if category not in categories:
                raise ValueError(
                    f"{where}: unknown category '{category}' (see categories.yaml)"
                )
            merged = {**defaults, **entry}
            kind = merged["kind"]
            if kind not in KINDS:
                raise ValueError(
                    f"{where}: unknown kind '{kind}' (allowed: {', '.join(KINDS)})"
                )
            if not isinstance(merged["need_sep"], bool):
                raise ValueError(f"{where}: need_sep must be true or false")
            alternatives = (
                merged["regex"]
                if isinstance(merged["regex"], list)
                else [merged["regex"]]
            )
            if not alternatives:
                raise ValueError(f"{where}: empty regex")
            inner = "|".join(_substitute(a, fragments, where) for a in alternatives)
            regex = _regex(f"(?<!{letter})(?:{inner})(?!{letter})", {}, where, re.I)
            stop = _regex(merged["stop"], fragments, f"{where}, stop").pattern
            max_len = _number(
                entry.get("max_len", shapes[kind].get("max_len", defaults["max_len"])),
                f"{where}, max_len",
            )
            min_len = _number(merged["min_len"], f"{where}, min_len")
            if "pattern" in entry:
                pattern = _regex(entry["pattern"], fragments, f"{where}, pattern", re.I)
            else:
                pattern = shapes[kind].get("pattern")
            yield Label(
                str(category),
                regex,
                kind,
                merged["need_sep"],
                stop,
                max_len,
                min_len,
                pattern,
            )


# ----------------------------------------------------------------------------- Markdown

_EMPHASIS = re.compile(r"\*\*|__|`")
_LINE_HEAD = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+\.\s+)")
_TABLE_SEPARATOR = re.compile(r"\s*:?-{2,}:?\s*")


def normalize(markdown: str) -> str:
    """Markdown -> lines the detector can read.

    Emphasis, headings and bullets removed; a table row becomes a "label: value" line for every
    cell that is a label, and the other cells one per line.
    """
    r = rules()
    out = []
    for line in _EMPHASIS.sub("", markdown).splitlines():
        line = _LINE_HEAD.sub("", line)
        if not line.lstrip().startswith("|"):
            out.append(line)
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(_TABLE_SEPARATOR.fullmatch(c) or not c for c in cells):
            continue
        i = 0
        while i < len(cells):
            cell = cells[i]
            if i + 1 < len(cells) and r.any_label.fullmatch(cell.rstrip(" :.")):
                out.append(f"{cell.rstrip(' :')}: {cells[i + 1]}")
                i += 2
            else:
                if cell:
                    out.append(cell)
                i += 1
    return "\n".join(out)


# ----------------------------------------------------------------------------- values


def _valid_value(label: Label, raw: str) -> bool:
    r = rules()
    shape = r.shapes[label.kind]
    value = raw.strip(" :.-,")
    if len(value) < label.min_len:
        return False
    tokens = value.split()
    if tokens and all(t.lower().strip(".:,") in r.stop_values for t in tokens):
        return False
    kind = label.kind
    if kind == "name":
        if re.search(r"\d", value):
            return False
        capitalized = [t for t in tokens if re.match(r"[A-ZÀ-Ü]", t)]
        return 1 <= len(tokens) <= shape["max_words"] and len(capitalized) == len(
            tokens
        )
    if kind == "place":
        return (
            not shape["digits_re"].search(value) and len(tokens) <= shape["max_words"]
        )
    if kind == "org":
        return (
            bool(re.match(r"[A-ZÀ-Ü0-9]", value)) and len(tokens) <= shape["max_words"]
        )
    if kind == "phone":
        return len(re.sub(r"\D", "", value)) >= shape["min_digits"]
    if kind == "id":
        # the first token is the code (with digits, or a short prefix as in "CA 12345 AB"):
        # "Ricetta: amoxicillina 1 g" is not a prescription number
        first = tokens[0] if tokens else ""
        code = bool(re.search(r"\d", first)) or bool(
            shape["prefix_re"].fullmatch(first) and re.search(r"\d", value)
        )
        return code and len(value.replace(" ", "")) >= shape["min_chars"]
    if kind == "date":
        return bool(re.search(r"\d", value))
    return True


def _trim_name(value: str) -> str:
    """Keep only the leading run of capitalized words."""
    r = rules()
    particles = r.shapes["name"]["particles"]
    out: list[str] = []
    for token in value.split(" "):
        if not token:
            if out:
                out.append(token)
            continue
        t = token.strip(",.;:")
        if (
            r.name_token.fullmatch(t)
            or re.fullmatch(r"[A-ZÀ-Ü]\.", t)
            or (t.lower() in particles and out)
        ):
            out.append(token)
            if token != t:  # word followed by punctuation: end of the name
                break
        else:
            break
    while out and out[-1].strip(",.;:").lower() in particles | {""}:
        out.pop()
    return " ".join(out)


def _trim_org(value: str) -> str:
    """Name of an organization or company: up to the first lowercase word that is not a particle."""
    shape = rules().shapes["org"]
    particles, symbols = shape["particles"], shape["symbols"]
    out: list[str] = []
    for token in value.split(" "):
        t = token.strip(",.;:")
        if not t:
            if out:
                out.append(token)
            continue
        if (
            t[0].isupper()
            or t[0].isdigit()
            or t.lower() in particles
            or "." in t
            or t in symbols
        ):
            out.append(token)
            if token.endswith((",", ";", ":")):
                break
        else:
            break
    while out and out[-1].strip(",.;:").lower() in particles | {""}:
        out.pop()
    return " ".join(out)


def _trim_place(value: str) -> str:
    return rules().shapes["place"]["end"].split(value, maxsplit=1)[0]


def _extract(text: str, label: Label, match: re.Match[str]) -> str | None:
    r = rules()
    position = match.end()
    separator = r.separator.match(text[position:])
    has_colon = bool(separator) and (
        ":" in separator.group(0) or "=" in separator.group(0)
    )
    position += separator.end() if separator else 0
    line_end = text.find("\n", position)
    line_end = len(text) if line_end < 0 else line_end
    rest = text[position:line_end]
    if rest.lstrip().startswith("/"):
        after = rest.lstrip()[1:]
        if after.upper() == after:
            # bilingual label ("LUOGO DI NASCITA / PLACE OF BIRTH"): the value is on the next line
            rest = ""
        else:
            # "Sig./Sig.ra Rossi": skip the second part of the label
            second = re.match(r"\s*/\s*[A-Za-z.]{1,6}\s*", rest)
            if second:
                rest = rest[second.end() :]
    if label.kind == "name" and rest.strip():
        title = r.leading_title.match(rest)
        if title:
            rest = rest[title.end() :]
    if not rest.strip(" \t,.;:-"):
        # value on the next line (typical of identity documents)
        next_start = line_end + 1
        if next_start >= len(text):
            return None
        next_end = text.find("\n", next_start)
        next_line = text[next_start : len(text) if next_end < 0 else next_end]
        if r.any_label.match(next_line.strip()):
            return None
        rest = next_line
        if label.kind == "name":
            title = r.leading_title.match(rest)
            if title:
                rest = rest[title.end() :]
    elif label.need_sep and not has_colon:
        # "Nome" followed by text without ":" -> accepted only if the value is UPPERCASE
        first = rest.split()[0] if rest.split() else ""
        if not (first.isupper() and len(first) > 1):
            return None
    # cut at the first strong separator or at the next label
    cut = re.search(label.stop, rest)
    if cut:
        rest = rest[: cut.start()]
    next_label = r.any_label.search(rest, 1)
    if next_label:
        rest = rest[: next_label.start()]
    if label.kind == "name":
        rest = _trim_name(rest)
    elif label.kind == "place":
        rest = _trim_place(rest)
    elif label.kind == "org":
        rest = _trim_org(rest)
    elif label.pattern is not None:
        # identifier: first token + following digit groups ("8038 0000 1234");
        # date, phone or fixed-shape value (sex, marital status...)
        shaped = label.pattern.match(rest)
        rest = shaped.group(0) if shaped else ("" if label.kind != "date" else rest)
    if len(rest) > label.max_len:
        # never cut in the middle of a word: the remaining piece would stay readable
        end = label.max_len
        while end < len(rest) and not rest[end].isspace():
            end += 1
        rest = rest[:end]
    value = rest.rstrip(" ,.;:-").strip()
    return value if _valid_value(label, value) else None


class LabelsDetector:
    name = "labels"

    def __init__(self) -> None:
        rules()  # YAML errors now, not at the first page

    def detect(self, text: str) -> list[Proposal]:
        r = rules()
        normalized = normalize(text)
        out: dict[Proposal, None] = {}
        for label in r.labels:
            for match in label.regex.finditer(normalized):
                value = _extract(normalized, label, match)
                if value:
                    out.setdefault(Proposal(value, label.category))
        return list(out)
