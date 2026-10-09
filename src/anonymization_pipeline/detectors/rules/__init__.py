"""Rule detector: regular expressions + validation (checksums) + local context.

Expressions, contexts, keywords and OCR tables are in ``resources/detectors/rules.yaml``, read at
the first search (the expressions are compiled once); here are the algorithms: the validators
(referenced by name from the YAML), the choice of the category from the context, the split of
the addresses. Ported from the anonimizzatore project.
"""

import functools
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from anonymization_pipeline.detectors.base import Proposal
from anonymization_pipeline.detectors.patterns import compile_regex, fragments
from anonymization_pipeline.detectors.rules.validators import (
    luhn,
    valid_card,
    valid_cf,
    valid_iban,
    valid_ipv4,
    valid_vat,
    valid_vin,
)
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.resources import read_yaml

FILE = "detectors/rules.yaml"


@dataclass(frozen=True)
class Spec:
    """One pattern.

    Attributes:
        category: Category of the value.
        regex: Compiled expression.
        validator: Check on the matched value.
        context: Text that must precede the value (with ``context_required``) or that confirms it.
        context_required: Without the context the value is discarded.
        context_categories: (keyword, category) pairs that change the category; the keyword
            closest to the value wins.
        parts: {named group: category} to emit the named groups as separate values (addresses).
        page: Context that must appear anywhere in the page.
    """

    category: str
    regex: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    context: re.Pattern[str] | None = None
    context_required: bool = False
    context_categories: list[tuple[re.Pattern[str], str]] = field(default_factory=list)
    parts: dict[str, str] | None = None
    page: re.Pattern[str] | None = None


@dataclass(frozen=True)
class Rules:
    """Content of ``rules.yaml``, with the expressions already compiled."""

    specs: tuple[Spec, ...]
    contexts: dict[str, re.Pattern[str]]
    date_keywords: tuple[tuple[re.Pattern[str], str], ...]
    pec: re.Pattern[str]
    province: re.Pattern[str]
    window: int
    distance: int
    cf_letters: dict[str, str]
    cf_digits: dict[str, str]
    cf_digit_positions: frozenset[int]
    cf_omocodia: str
    ocr_date: dict[int, str]


# ----------------------------------------------------------------------------- validators


def fix_cf_ocr(s: str) -> str | None:
    """Try to fix the typical OCR errors (0/O, 1/I, 5/S...) in a codice fiscale."""
    s = s.upper()
    if len(s) != 16:
        return None
    if valid_cf(s):
        return s
    r = rules()
    out = []
    for i, c in enumerate(s):
        if i in r.cf_digit_positions:
            out.append(
                c if c.isdigit() or c in r.cf_omocodia else r.cf_digits.get(c, c)
            )
        else:
            out.append(r.cf_letters.get(c, c) if c.isdigit() else c)
    candidate = "".join(out)
    return candidate if valid_cf(candidate) else None


def _valid_ocr_date(s: str) -> bool:
    """Plausible date after fixing the OCR swaps; at least one character must be fixed."""
    raw = re.sub(r"\s", "", s)
    fixed = raw.translate(rules().ocr_date)
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", fixed)
    return (
        bool(match)
        and fixed != raw
        and 1 <= int(match.group(1)) <= 31
        and 1 <= int(match.group(2)) <= 12
    )


def _digits_and_letters(s: str) -> bool:
    return bool(re.search(r"\d", s) and re.search(r"[A-Z]", s))


def _landline(s: str) -> bool:
    return (
        9 <= len(re.sub(r"\D", "", s.replace("+39", "").replace("0039", "", 1))) <= 12
    )


# name used in rules.yaml (key 'validator') -> function on the matched value
VALIDATORS: dict[str, Callable[[str], bool]] = {
    "cf_ocr": lambda s: (
        fix_cf_ocr(s) is not None
    ),  # codice fiscale, also with OCR errors
    "vat": valid_vat,
    "vat_it": lambda s: valid_vat(s[2:]),  # VAT number preceded by "IT"
    "iban": valid_iban,
    "card": valid_card,
    "luhn": luhn,
    "ipv4": valid_ipv4,
    "vin": valid_vin,  # chassis number with check digit
    "digits_and_letters": _digits_and_letters,  # at least one digit and one capital letter
    "twenty_digits": lambda s: len(re.sub(r"\D", "", s)) == 20,  # health card
    "nine_chars": lambda s: (
        len(re.sub(r"\s", "", s)) == 9
    ),  # CIE read with stray spaces
    "landline": _landline,  # 9-12 digits without the international prefix
    "ocr_date": _valid_ocr_date,
}


# ----------------------------------------------------------------------------- loading

_SPEC_KEYS = {
    "category",
    "regex",
    "flags",
    "validator",
    "context",
    "context_required",
    "context_categories",
    "parts",
    "page",
}


def _error(where: str, message: str) -> ValueError:
    return ValueError(f"invalid {FILE} ({where}): {message}")


def _compile(
    text: Any, raw: dict[str, str], where: str, flags: int = 0
) -> re.Pattern[str]:
    return compile_regex(text, raw, f"invalid {FILE} ({where})", flags)


def _category(value: Any, where: str) -> str:
    category = str(value)
    if category not in registry().descriptions:
        raise _error(where, f"unknown category {category!r} (see categories.yaml)")
    return category


def _spec(
    i: int,
    entry: Any,
    raw: dict[str, str],
    contexts: dict[str, re.Pattern[str]],
    keywords: dict[str, re.Pattern[str]],
) -> Spec:
    if not isinstance(entry, dict) or "category" not in entry or "regex" not in entry:
        raise _error(f"pattern {i}", "'category' and 'regex' are required")
    where = f"pattern {i} ({entry['category']})"
    category = _category(entry["category"], where)
    if extra := set(entry) - _SPEC_KEYS:
        raise _error(where, f"unknown keys: {', '.join(sorted(extra))}")
    flags = 0
    for flag in entry.get("flags") or []:
        try:
            flags |= re.RegexFlag[str(flag).upper()]
        except KeyError:
            raise _error(where, f"unknown flag {flag!r}") from None
    validator = None
    if (name := entry.get("validator")) is not None:
        if name not in VALIDATORS:
            raise _error(
                where,
                f"unknown validator {name!r} (available: {', '.join(sorted(VALIDATORS))})",
            )
        validator = VALIDATORS[name]

    def context(key: str) -> re.Pattern[str] | None:
        if (name := entry.get(key)) is None:
            return None
        if name not in contexts:
            raise _error(where, f"unknown context {name!r}")
        return contexts[name]

    ctx = context("context")
    required = bool(entry.get("context_required", False))
    if required and ctx is None:
        raise _error(where, "context_required without context")
    context_categories = []
    for keyword, value in (entry.get("context_categories") or {}).items():
        if keyword not in keywords:
            raise _error(where, f"unknown keyword {keyword!r}")
        context_categories.append((keywords[keyword], _category(value, where)))
    regex = _compile(entry["regex"], raw, where, flags)
    parts = None
    if entry.get("parts"):
        parts = {}
        for group, value in entry["parts"].items():
            if group not in regex.groupindex:
                raise _error(
                    where, f"group {group!r} of 'parts' missing in the expression"
                )
            parts[str(group)] = _category(value, where)
    return Spec(
        category,
        regex,
        validator,
        ctx,
        required,
        context_categories,
        parts,
        context("page"),
    )


@functools.cache
def rules() -> Rules:
    """Rules of ``rules.yaml`` (read and compiled at first use, then cached)."""
    data = read_yaml(FILE)
    raw = fragments(data.get("fragments") or {}, f"invalid {FILE} (fragments)")
    contexts = {
        str(k): _compile(v, raw, f"contexts.{k}", re.I)
        for k, v in (data.get("contexts") or {}).items()
    }
    if "birth" not in contexts or contexts["birth"].groups < 1:
        raise _error("contexts.birth", "required, with the keyword in the first group")
    keywords = {
        str(k): _compile(v, raw, f"keywords.{k}", re.I)
        for k, v in (data.get("keywords") or {}).items()
    }
    date_keywords = tuple(
        (
            _compile(d.get("regex"), raw, f"dates {i}", re.I),
            _category(d.get("category"), f"dates {i}"),
        )
        for i, d in enumerate(data.get("dates") or [], 1)
    )
    context_cfg = data.get("context") or {}
    ocr = data.get("ocr") or {}
    specs = tuple(
        _spec(i, d, raw, contexts, keywords)
        for i, d in enumerate(data.get("patterns") or [], 1)
    )
    if not specs:
        raise ValueError(f"{FILE} defines no pattern")
    return Rules(
        specs=specs,
        contexts=contexts,
        date_keywords=date_keywords,
        pec=_compile(data.get("pec"), raw, "pec", re.I),
        province=_compile(data.get("province"), raw, "province"),
        window=int(context_cfg.get("window", 60)),
        distance=int(context_cfg.get("keyword_distance", 40)),
        cf_letters={str(k): str(v) for k, v in (ocr.get("cf_letters") or {}).items()},
        cf_digits={str(k): str(v) for k, v in (ocr.get("cf_digits") or {}).items()},
        cf_digit_positions=frozenset(
            int(p) for p in ocr.get("cf_digit_positions") or ()
        ),
        cf_omocodia=str(ocr.get("cf_omocodia") or ""),
        ocr_date=str.maketrans(
            {str(k): str(v) for k, v in (ocr.get("date") or {}).items()}
        ),
    )


# ----------------------------------------------------------------------------- detection


def _last(keyword: re.Pattern[str], before: str, n: int) -> int | None:
    """End of the last keyword in ``before``, if it is at most ``n`` characters from the value on
    the same line; None otherwise."""
    last = None
    for last in keyword.finditer(before):  # noqa: B007 - only the last match is needed
        pass
    if last is None:
        return None
    tail = before[last.end() :]
    return last.end() if len(tail) <= n and "\n" not in tail else None


def _closest(
    before: str, candidates: Iterable[tuple[re.Pattern[str], str]], default: str, n: int
) -> str:
    best, category = -1, default
    for keyword, value in candidates:
        position = _last(keyword, before, n)
        if position is not None and position > best:
            best, category = position, value
    return category


def _date_category(before: str, r: Rules) -> str:
    category = _closest(before, r.date_keywords, "DATA", r.distance)
    birth = r.contexts["birth"].search(before)
    if birth and (
        category == "DATA"
        or birth.end(1)
        > max(_last(k, before, r.distance) or -1 for k, _ in r.date_keywords)
    ):
        return "DATA_NASCITA"
    return category


def _trim(text: str, s: int, e: int) -> int:
    """Do not include trailing spaces or punctuation."""
    while e > s and text[e - 1] in " ,.;:":
        e -= 1
    return e


def _values(text: str, r: Rules) -> Iterable[tuple[str, str]]:
    """(value, category) found in the text."""
    for spec in r.specs:
        if spec.page is not None and not spec.page.search(text):
            continue
        for match in spec.regex.finditer(text):
            s, e = match.span()
            value = match.group(0)
            if spec.validator and not spec.validator(value):
                continue
            before = text[max(0, s - r.window) : s]
            if (
                spec.context_required
                and spec.context is not None
                and not spec.context.search(before)
            ):
                continue
            if spec.parts:
                yield from _parts(text, spec.parts, match, r)
                continue
            category = spec.category
            if category == "DATA":
                category = _date_category(before, r)
            else:
                category = _closest(
                    before, spec.context_categories, category, r.distance
                )
            if category == "EMAIL" and r.pec.search(value):
                category = "PEC"
            e = _trim(text, s, e)
            if category == "INDIRIZZO":
                province = r.province.match(text, e)
                if province and province.end() > e:
                    e = province.end()
            yield text[s:e], category


def _parts(
    text: str, parts: dict[str, str], match: re.Match[str], r: Rules
) -> Iterable[tuple[str, str]]:
    """Split address: street and number (INDIRIZZO), postal code and city (LUOGO) as separate values."""
    spans = sorted(
        (match.start(g), match.end(g), c) for g, c in parts.items() if match.group(g)
    )
    for i, (s, e, category) in enumerate(spans):
        e = _trim(text, s, e)
        if i == len(spans) - 1:
            # province code right after ("TORINO TO", "Torino (TO)")
            province = r.province.match(text, e)
            if province and province.end() > e:
                e = province.end()
        if e > s:
            yield text[s:e], category


class RulesDetector:
    name = "rules"

    def __init__(self) -> None:
        rules()  # YAML errors now, not at the first page

    def detect(self, text: str) -> list[Proposal]:
        return [Proposal(v, c) for v, c in dict.fromkeys(_values(text, rules()))]
