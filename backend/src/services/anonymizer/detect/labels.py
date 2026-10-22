"""Label -> value detector: the value printed after a form or report label.

"Cognome: ROSSI", "Paziente: Mario Rossi", "nato a Roma il 01/01/1980",
"N. accettazione VS-26-004817", "COGNOME / SURNAME" with "ROSSI" on the line
below. Ported from the anonimizzatore vocabulary of feat/reconstructed-architecture
(``labels.yaml``, read and validated at first use); here the value is returned
as a span of the layer text, so propagation and merging work as for the rules.
"""

import functools
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from src.services.anonymizer.layers import Span
from src.services.anonymizer.taxonomy import LABELS

# small words inside a place name: "San Giovanni in Fiore", "Reggio nell'Emilia"
PLACE_PARTICLES = frozenset(
    {
        "di",
        "de",
        "da",
        "del",
        "della",
        "dei",
        "la",
        "lo",
        "in",
        "a",
        "al",
        "sul",
        "nell'",
    }
)
KINDS = ("name", "place", "address", "org", "id", "date", "phone", "value")
_SHAPES = {
    "name": {"max_words", "particles"},
    "place": {"max_words", "consecutive_digits", "end"},
    "address": {"max_words", "street"},
    "org": {"max_words", "particles", "symbols"},
    "id": {"pattern", "max_len", "max_prefix_letters", "min_chars"},
    "date": {"pattern", "max_len"},
    "phone": {"pattern", "max_len", "min_digits"},
    "value": set(),
}
_LABEL_KEYS = {
    "label",
    "regex",
    "kind",
    "need_sep",
    "stop",
    "max_len",
    "min_len",
    "pattern",
}
_FRAGMENT = re.compile(r"\$\{(\w+)\}")


@dataclass(frozen=True)
class Label:
    label: str
    regex: re.Pattern[str]
    kind: str
    need_sep: bool
    stop: re.Pattern[str]
    max_len: int
    min_len: int
    pattern: re.Pattern[str] | None


@dataclass(frozen=True)
class Rules:
    labels: tuple[Label, ...]
    any_label: re.Pattern[str]
    separator: re.Pattern[str]
    leading_title: re.Pattern[str]
    name_token: re.Pattern[str]
    stop_values: frozenset[str]
    shapes: dict[str, dict[str, Any]]


def _expand(text: str, fragments: dict[str, str], where: str, depth: int = 0) -> str:
    if depth > 5:
        raise ValueError(f"{where}: fragments nested too deeply")

    def sub(m: re.Match[str]) -> str:
        if m.group(1) not in fragments:
            raise ValueError(f"{where}: unknown fragment ${{{m.group(1)}}}")
        return _expand(fragments[m.group(1)], fragments, where, depth + 1)

    return _FRAGMENT.sub(sub, text)


def compile_pattern(
    text: Any, fragments: dict[str, str], where: str, flags: int = 0
) -> re.Pattern[str]:
    """A YAML regex (a string, or a list read as an alternation) with its ``${name}`` fragments.

    Args:
        text: The regex, or a list of alternatives.
        fragments: Named fragments that ``${name}`` expands to.
        where: Location in the YAML file, for the error message.
        flags: ``re`` flags.

    Raises:
        ValueError: Invalid regex or unknown fragment: startup stops.
    """
    if isinstance(text, list):
        text = "|".join(f"(?:{t})" for t in text)
    try:
        return re.compile(_expand(str(text), fragments, where), flags)
    except re.error as e:
        raise ValueError(f"{where}: invalid regex ({e})") from e


@functools.cache
def rules() -> Rules:
    """``labels.yaml``, validated and compiled (cached)."""
    data = yaml.safe_load(
        resources.files(__package__).joinpath("labels.yaml").read_text(encoding="utf-8")
    )
    frags = {k: str(v) for k, v in data["fragments"].items()}
    letter = _expand(data["letter"], frags, "letter")
    shapes: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        raw = data["shapes"].get(kind) or {}
        if unknown := set(raw) - _SHAPES[kind] - {"pattern", "max_len"}:
            raise ValueError(
                f"labels.yaml, shapes.{kind}: unknown keys {sorted(unknown)}"
            )
        shape: dict[str, Any] = {}
        for key, value in raw.items():
            if key in ("pattern", "end", "street"):
                shape[key] = compile_pattern(
                    value,
                    frags,
                    f"shapes.{kind}.{key}",
                    re.I if key in ("pattern", "street") else 0,
                )
            elif key == "particles":
                shape[key] = frozenset(str(p).lower() for p in value)
            elif key == "symbols":
                shape[key] = tuple(str(x) for x in value)
            else:
                shape[key] = int(value)
        shapes[kind] = shape
    shapes["place"]["digits_re"] = re.compile(
        rf"\d{{{shapes['place']['consecutive_digits']}}}"
    )
    shapes["id"]["prefix_re"] = re.compile(
        rf"[A-Z]{{1,{shapes['id']['max_prefix_letters']}}}"
    )
    defaults = data["defaults"]
    labels = []
    for group, entries in data["labels"].items():
        for n, entry in enumerate(entries, 1):
            where = f"labels.yaml, {group} #{n}"
            if unknown := set(entry) - _LABEL_KEYS:
                raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
            merged = {**defaults, **entry}
            if merged["label"] not in LABELS or LABELS[merged["label"]].kind != "text":
                raise ValueError(f"{where}: unknown label {merged['label']}")
            if merged["kind"] not in KINDS:
                raise ValueError(f"{where}: unknown kind {merged['kind']}")
            inner = compile_pattern(merged["regex"], frags, where).pattern
            pattern = (
                compile_pattern(entry["pattern"], frags, f"{where}, pattern", re.I)
                if "pattern" in entry
                else shapes[merged["kind"]].get("pattern")
            )
            labels.append(
                Label(
                    merged["label"],
                    re.compile(f"(?<!{letter})(?:{inner})(?!{letter})", re.I),
                    merged["kind"],
                    bool(merged["need_sep"]),
                    compile_pattern(merged["stop"], frags, f"{where}, stop"),
                    int(
                        entry.get(
                            "max_len",
                            shapes[merged["kind"]].get("max_len", defaults["max_len"]),
                        )
                    ),
                    int(merged["min_len"]),
                    pattern,
                )
            )
    return Rules(
        labels=tuple(labels),
        any_label=re.compile("|".join(lab.regex.pattern for lab in labels), re.I),
        separator=compile_pattern(data["separator"], frags, "separator"),
        leading_title=re.compile(
            r"\s*(?:" + _expand("${titles}", frags, "titles") + r")(?![A-Za-z])\s*",
            re.I,
        ),
        name_token=re.compile(frags["name_token"] + r"\.?"),
        stop_values=frozenset(str(w).lower() for w in data["stop_values"]),
        shapes=shapes,
    )


def _valid(label: Label, value: str) -> bool:
    r = rules()
    shape = r.shapes[label.kind]
    if len(value) < label.min_len:
        return False
    tokens = value.split()
    if not tokens or all(t.lower().strip(".:,") in r.stop_values for t in tokens):
        return False
    if label.kind == "name":
        return (
            not re.search(r"\d", value)
            and len(tokens) <= shape["max_words"]
            and all(
                re.match(r"[A-ZÀ-Ü]", t) or t.lower() in shape["particles"]
                for t in tokens
            )
        )
    if label.kind == "place":
        return (
            value[0].isupper()
            and not shape["digits_re"].search(value)
            and len(tokens) <= shape["max_words"]
        )
    if label.kind == "address":
        # a street address (a number or a street type) or a place name, not prose:
        # "Via Roma 12", "Bergamo", "San Giovanni in Fiore" but not "da prenotare"
        if len(tokens) > shape["max_words"]:
            return False
        if shape["street"].match(value):
            return True  # "Via Roma 12", "via Roma 12"
        if value[0].islower():
            return False  # "da prenotare entro 7 giorni": prose
        if re.search(r"\d", value):
            return True
        return all(
            re.match(r"[A-ZÀ-Ü]", t) or t.lower() in PLACE_PARTICLES for t in tokens
        )
    if label.kind == "org":
        return (
            bool(re.match(r"[A-ZÀ-Ü0-9]", value)) and len(tokens) <= shape["max_words"]
        )
    if label.kind == "phone":
        return len(re.sub(r"\D", "", value)) >= shape["min_digits"]
    if label.kind == "id":
        first = tokens[0]
        code = bool(re.search(r"\d", first)) or bool(
            shape["prefix_re"].fullmatch(first) and re.search(r"\d", value)
        )
        return (
            code
            and len(value.replace(" ", "")) >= shape["min_chars"]
            and sum(c.isdigit() for c in value) >= 3
        )
    if label.kind == "date":
        return bool(re.search(r"\d", value))
    return True


def _trim_name(value: str) -> str:
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
            if token != t and not re.fullmatch(r"[A-ZÀ-Ü]\.", token):
                break
        else:
            break
    while out and out[-1].strip(",.;:").lower() in particles | {""}:
        out.pop()
    return " ".join(out)


def _trim_org(value: str) -> str:
    shape = rules().shapes["org"]
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
            or t.lower() in shape["particles"]
            or "." in t
            or t in shape["symbols"]
        ):
            out.append(token)
            if token.endswith((",", ";", ":")):
                break
        else:
            break
    while out and out[-1].strip(",.;:").lower() in shape["particles"] | {""}:
        out.pop()
    return " ".join(out)


def _extract(text: str, label: Label, m: re.Match[str]) -> tuple[int, str] | None:
    """Start offset and value after the label, or None."""
    r = rules()
    pos = m.end()
    sep = r.separator.match(text, pos)
    has_colon = bool(sep) and any(c in sep.group(0) for c in ":=#")
    pos = sep.end() if sep else pos
    line_end = text.find("\n", pos)
    line_end = len(text) if line_end < 0 else line_end
    start, rest = pos, text[pos:line_end]
    stripped = rest.lstrip()
    if stripped.startswith("/"):
        after = stripped[1:]
        if after.upper() == after:
            rest = ""  # bilingual label: the value is on the next line
        else:
            second = re.match(r"\s*/\s*[A-Za-z.]{1,6}\s*", rest)
            if second:
                start, rest = start + second.end(), rest[second.end() :]
    if label.kind == "name" and rest.strip():
        title = r.leading_title.match(rest)
        if title:
            start, rest = start + title.end(), rest[title.end() :]
    line_end_value = line_end
    if not rest.strip(" \t,.;:-"):
        nxt = line_end + 1
        if nxt >= len(text):
            return None
        nxt_end = text.find("\n", nxt)
        line = text[nxt : len(text) if nxt_end < 0 else nxt_end]
        if r.any_label.match(line.strip()):
            return None
        start, rest = nxt, line
        line_end_value = nxt + len(line)
        if label.kind == "name":
            title = r.leading_title.match(rest)
            if title:
                start, rest = start + title.end(), rest[title.end() :]
    elif label.need_sep and not has_colon:
        first = rest.split()[0] if rest.split() else ""
        if not (first.isupper() and len(first) > 1):
            return None
    cut = label.stop.search(rest)
    if cut:
        rest = rest[: cut.start()]
    nxt_label = r.any_label.search(rest, 1)
    if nxt_label:
        rest = rest[: nxt_label.start()]
    if label.kind == "name":
        rest = _trim_name(rest)
    elif label.kind == "place":
        rest = r.shapes["place"]["end"].split(rest, maxsplit=1)[0]
    elif label.kind == "org":
        rest = _trim_org(rest)
    elif label.pattern is not None:
        shaped = label.pattern.match(rest)
        rest = shaped.group(0) if shaped else ("" if label.kind != "date" else rest)
    if len(rest) > label.max_len:
        end = label.max_len
        while end < len(rest) and not rest[end].isspace():
            end += 1
        rest = rest[:end]
    lead = len(rest) - len(rest.lstrip(" \t,.;:-"))
    value = rest.rstrip(" ,.;:-").strip(" \t,.;:-")
    if not _valid(label, value):
        return None
    tail = text[start + lead + len(value) : line_end_value]
    if line_end_value != line_end and re.match(r"\s+[a-zà-ù]", tail):
        # the next line is a sentence ("Acconsento al test HIV"), not the value
        return None
    if (
        label.kind == "name"
        and len(value.split()) == 1
        and not has_colon
        and not value.isupper()
        and not r.leading_title.fullmatch(m.group(0) + " ")
    ):
        return None  # "Medico Ostetrico", "Paziente\nPuerperio": a role or a heading
    return start + lead, value


def detect(text: str) -> list[Span]:
    """Values after labels, as spans of ``text``."""
    out: dict[tuple[int, int, str], Span] = {}
    for label in rules().labels:
        for m in label.regex.finditer(text):
            found = _extract(text, label, m)
            if not found:
                continue
            start, value = found
            if text[start : start + len(value)] != value:
                continue
            key = (start, start + len(value), label.label)
            out.setdefault(
                key, Span(start, start + len(value), label.label, 0.9, "labels", value)
            )
    return list(out.values())
