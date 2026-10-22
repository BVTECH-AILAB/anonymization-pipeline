"""Combine detectors on every text layer of a document.

1. rules, label -> value and NER on each layer (recall first: any detector is enough)
2. allowlist filter (eponyms, drugs, saints, clinical vocabulary)
3. document-level propagation: every name token and identifier found anywhere
   is searched in every layer ("Sig. Rossi" in the body, "ROSSI M." in a footer)
4. merge overlapping spans into one finding with the most specific label
"""

import functools
import re
from dataclasses import dataclass, field

from src.llms.ner import NerEngine
from src.services.anonymizer.detect import (
    allowlist,
    eponyms,
    labels,
    rules,
)
from src.services.anonymizer.layers import Span, TextLayer
from src.services.anonymizer.taxonomy import STRONG_LABELS

NER_LABELS = {"PERSON", "LOCATION", "ORGANIZATION", "ADDRESS"}
# a title before a name is not part of it ("Dott.ssa\nAnna Rossi": the title line stays visible)
_LEADING_TITLE = re.compile(
    r"(?:(?:sig\.?(?:ra|na)?|signor[ae]?|dott\.?(?:ssa)?|dr\.?(?:ssa)?|prof\.?(?:ssa)?|avv\.?|ing\.?)\s+)+",
    re.I,
)
# when spans overlap, the more specific label wins
# URLs and emails contain other identifiers (a tax code in a query string): they label the whole span.
PRIORITY = [
    "URL",
    "EMAIL",
    "FISCAL_CODE",
    "HEALTH_CARD",
    "IBAN",
    "PRESCRIPTION_ID",
    "PROFESSIONAL_ID",
    "ID_DOCUMENT",
    "MEDICAL_RECORD",
    "PHONE",
    "DATE_OF_BIRTH",
    "ADDRESS",
    "PERSON",
    "LOCATION",
    "AGE",
    "DATE",
    "ORGANIZATION",
]
_HON = re.compile(r"^(?:sig\.?(?:ra|na)?|dott\.?(?:ssa)?|prof\.?(?:ssa)?|dr\.?)$", re.I)


@dataclass
class Finding:
    layer: TextLayer
    start: int
    end: int
    label: str
    score: float
    sources: set[str] = field(default_factory=set)

    @property
    def text(self) -> str:
        return self.layer.text[self.start : self.end]


def _merge(layer: TextLayer, spans: list[Span]) -> list[Finding]:
    spans = sorted(spans, key=lambda s: (s.start, -s.end))
    out: list[Finding] = []
    for s in spans:
        if out and s.start < out[-1].end:
            f = out[-1]
            f.end = max(f.end, s.end)
            if PRIORITY.index(s.label) < PRIORITY.index(f.label):
                f.label = s.label
            f.score = max(f.score, s.score)
            f.sources.add(s.source)
        else:
            out.append(Finding(layer, s.start, s.end, s.label, s.score, {s.source}))
    return out


def _lower(text: str) -> str:
    """Lower case with the same length ("İ".lower() is two characters): offsets stay valid."""
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


# OCR confusions, folded the same way in the value and in the text (one character for one,
# offsets stay valid): "Adebay0" is "Adebayo", "389 555 773l" is "389 555 7731"
_AS_LETTERS = str.maketrans("015|!", "olsll")
_AS_DIGITS = str.maketrans("oOlI|", "00111")


def _fold_letters(text: str) -> str:
    """Lower case with digits read as the letters OCR confuses them with."""
    return _lower(text).translate(_AS_LETTERS)


def _id_shape(value: str) -> re.Pattern[str] | None:
    """The numbering scheme of an identifier: "RX-550-2913" -> RX-ddd-dddd, or None.

    Other codes of the same scheme in the document are identifiers too.
    """
    m = re.fullmatch(r"([A-Z]{2,5})((?:[-/.]\d{2,}){2,})", value)
    if not m or sum(c.isdigit() for c in value) < 6:
        return None
    groups = re.findall(r"([-/.])(\d+)", m.group(2))
    body = "".join(re.escape(sep) + rf"\d{{{len(d)}}}" for sep, d in groups)
    return re.compile(rf"(?<![\w-]){re.escape(m.group(1))}{body}(?![\w-])")


def _spread(value: str, ignore_case: bool = False) -> re.Pattern[str]:
    """``value`` with any spacing between its parts, line breaks included.

    An identifier can wrap at a space or after "/", "-", "." ("+39 392 115\\n4870",
    "RD/\\n26/0093317"): every occurrence must be redacted, not only the one found.
    """
    parts = [
        "".join(re.escape(c) + (r"\s*" if c in "/-." else "") for c in tok)
        for tok in value.split()
    ]
    flags = re.I if ignore_case else 0
    return re.compile(r"(?<![\w])" + r"\s*".join(parts) + r"(?![\w])", flags)


# words that make a "person" span a reference to someone, not a name ("La paziente", "Sua figlia")
_NOT_NAME = {
    "il",
    "lo",
    "la",
    "le",
    "i",
    "gli",
    "un",
    "una",
    "sua",
    "suo",
    "mia",
    "mio",
    "e",
    "ed",
    "paziente",
    "pz",
    "assistito",
    "assistita",
    "utente",
    "figlia",
    "figlio",
    "madre",
    "padre",
    "moglie",
    "marito",
    "sorella",
    "fratello",
    "nipote",
    "nonna",
    "nonno",
    "zia",
    "zio",
    "partner",
    "coniuge",
    "compagna",
    "compagno",
    "signora",
    "signore",
    "medico",
    "dottore",
    "dottoressa",
    "infermiere",
    "infermiera",
    "collega",
    "minore",
}
# institution words: a "person" span containing one is an organisation name
_INSTITUTION = re.compile(
    r"\b(?:ospedal[ei]|casa\s+di\s+cura|clinica|policlinico|azienda|asl|asst|ausl|ulss|irccs|istituto|"
    r"laboratorio|presidio|dipartimento|reparto|servizio|centro|fondazione|universit[aà]|ambulatorio|"
    r"poliambulatorio|sistema\s+(?:socio\s+)?sanitario|hospital|clinic|center|centre|trust|surgery)\b",
    re.I,
)


def _clean_person(text: str, s: Span) -> Span | None:
    """Trim a leading title from a person span; drop a reference; relabel an institution.

    "la paziente", "La figlia", "pz." refer to a person but are not names;
    "CASA DI CURA VILLA AURORA" is an organisation.
    """
    if s.label == "LOCATION" and _INSTITUTION.match(text, s.start, s.end):
        # "Sistema Socio Sanitario Regionale": a facility, not a place
        return Span(s.start, s.end, "ORGANIZATION", s.score, s.source, s.text)
    if s.label != "PERSON":
        return s
    t = _LEADING_TITLE.match(text, s.start, s.end)
    start = t.end() if t else s.start
    value = text[start : s.end]
    words = re.findall(r"[A-Za-zÀ-ÿ'’]+", value)
    if not any(w[0].isupper() and w.lower() not in _NOT_NAME for w in words):
        return None
    # only a name that starts with an institution word is one ("Casa di Cura Villa Aurora"):
    # "Mario Rossi Centro" stays a person
    label = "ORGANIZATION" if _INSTITUTION.match(value) else s.label
    if start == s.start and label == s.label:
        return s
    return Span(start, s.end, label, s.score, s.source, value)


def is_acronym(word: str, in_capitals: bool = False) -> bool:
    """An abbreviation ("TSRM", "NYHA", "SerD", "SiMT"), never a name to spread.

    Args:
        word: One word of a person span.
        in_capitals: The whole span is in capitals ("ROSA NERI"): a short word in
            capitals is then part of the name, not an acronym.
    """
    if re.search(r"[a-z][A-Z]", word):
        return True
    return word.isupper() and len(word) <= 4 and not in_capitals


@functools.cache
def _street() -> re.Pattern[str]:
    """A street type followed by a name or a number: "Via Roma", "Corso 22 Marzo"."""
    street = labels.rules().shapes["address"]["street"].pattern
    return re.compile(rf"(?i:{street})\s+[A-ZÀ-Ü0-9]")


_NUMBER_FIRST = re.compile(
    r"\s*(?:\d{5}(?:\s+[A-ZÀ-Ü]|\s*$)|\d{1,5}[A-Za-z]?\s+[A-ZÀ-Ü][a-zà-ÿ])"
)


def _address_shape(value: str) -> bool:
    """An address proposed by NER: a street, a postal code, or a place name without digits.

    Accepted: "Via Roma 12", "Vico Lungo Gelso 9", "3150 Calle Del Sol" (number first),
    "35028", "20121 Milano". Lab table rows ("ACC dx 68 16") and OCR noise
    ("via fischio CAP sig. 776") are not.
    """
    if _street().search(value) or _NUMBER_FIRST.match(value):
        return True
    particles = labels.PLACE_PARTICLES
    words = value.split()
    return (
        bool(words)
        and not re.search(r"\d", value)
        and all(w[0].isupper() or w.lower() in particles for w in words)
    )


@functools.cache
def _ambiguous_words() -> frozenset[str]:
    """Eponyms, scales, eponymous nouns and Italian municipalities, lower-case."""
    ep = eponyms.lists()
    return ep.eponyms | ep.scales | ep.nouns | rules.comuni()


def _name_tokens(text: str) -> set[str]:
    """Words of a person span worth finding alone everywhere in the document.

    Words that are ambiguous on their own are left out: short words ("Ugo", "DEL",
    "HIV"), acronyms, field labels ("Recapito"), eponyms and scales ("Masson",
    "Ishak") and Italian municipalities ("Napoli"). A span that swallowed one would
    otherwise spread it to every occurrence; the full name is still searched (in
    any order) by the caller.
    """
    any_label = labels.rules().any_label
    ambiguous = _ambiguous_words()
    words = re.findall(r"[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ'-]+", text)
    in_capitals = all(w.isupper() for w in words)
    toks = set()
    for w in words:
        lw = w.lower()
        if (
            len(w) >= 4
            and not _HON.match(w)
            and not is_acronym(w, in_capitals)
            and not any_label.fullmatch(w)
            and lw not in ambiguous
            and lw not in allowlist.STOP
            and lw not in allowlist.DRUGS
        ):
            toks.add(lw)
    return toks


def analyze_layers(
    layers: list[TextLayer],
    ner: NerEngine | None = None,
    ner_threshold: float = 0.5,
    person_threshold: float = 0.4,
) -> list[Finding]:
    """Detect PII on every text layer of a document.

    Args:
        layers: Text layers of all pages (native and OCR).
        ner: NER engine, or None for rules only.
        ner_threshold: Minimum NER score for places, addresses, organizations.
        person_threshold: Minimum NER score for persons.

    Returns:
        Merged findings, one per contiguous span of each layer.
    """
    per_layer: dict[int, list[Span]] = {}
    for i, L in enumerate(layers):
        spans = rules.detect(L.text) + labels.detect(L.text)
        if ner is not None:
            for s in ner.detect(L.text):
                if s.label in NER_LABELS and s.score >= (
                    person_threshold if s.label == "PERSON" else ner_threshold
                ):
                    spans.append(s)
        cleaned = [c for s in spans if (c := _clean_person(L.text, s)) is not None]
        per_layer[i] = [
            s
            for s in cleaned
            if s.label not in NER_LABELS
            or (s.source == "rules" and s.label != "PERSON")
            or (
                not allowlist.is_not_pii(L.text, s.start, s.end, s.label)
                and (s.label != "ADDRESS" or _address_shape(L.text[s.start : s.end]))
            )
        ]

    # ---- propagation across the whole document -------------------------------
    names, exact, full = set(), set(), set()
    for i, L in enumerate(layers):
        for s in per_layer[i]:
            val = L.text[s.start : s.end]
            if s.label == "PERSON":
                names |= _name_tokens(val)
                # the full name keeps its short and ambiguous words: "Ugo Napoli"
                words = [
                    w
                    for w in re.findall(r"[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ'-]+", val)
                    if not _HON.match(w)
                    and w.lower() not in allowlist.STOP
                    and not is_acronym(w, val.isupper())
                ]
                if len(words) >= 2:
                    full.add(tuple(w.lower() for w in words[:3]))
            elif s.label in STRONG_LABELS and len(val) >= 6:
                exact.add((val, s.label))
            elif s.label == "ADDRESS" and len(val) >= 10 and re.search(r"\d", val):
                # a full street address; case-sensitive, a place alone is never propagated
                exact.add((val, s.label))
    shapes = {(rx, label) for val, label in exact if (rx := _id_shape(val))}
    for i, L in enumerate(layers):
        low = _fold_letters(L.text)
        digits = L.text.translate(_AS_DIGITS)
        for n in names:
            folded = n.translate(_AS_LETTERS)
            # a surname glued to an initial in a word in capitals: "TOKAFOR"
            for m in re.finditer(rf"(?<![\w])([a-z])?{re.escape(folded)}(?![\w])", low):
                a, b = m.start(), m.end()
                if m.group(1) and not (len(n) >= 5 and L.text[a:b].isupper()):
                    continue
                if not L.text[a].isupper():  # "sostanza bianca" is not Dott.ssa Bianca
                    continue
                # extend over an initial: "Rossi M." / "M. Rossi"
                tail = re.match(r"[ \t]+[A-Z]\.", L.text[b : b + 4])
                if tail:
                    b += tail.end()
                head = re.search(r"[A-Z]\.[ \t]+$", L.text[max(0, a - 4) : a])
                if head:
                    a -= len(head.group())
                if allowlist.is_eponym_use(L.text, a, b):
                    continue
                if allowlist.is_saint(L.text, a):
                    continue
                per_layer[i].append(
                    Span(a, b, "PERSON", 0.8, "propagation", L.text[a:b])
                )
        for val, label in exact:
            # identifiers in any case ("ab-123" = "AB-123"); addresses as written; a
            # number also as OCR misreads it ("773l" for "7731")
            rx = _spread(val, ignore_case=label in STRONG_LABELS)
            numeric = sum(c.isdigit() for c in val) >= 6 and not re.search(
                r"[A-Za-z]", val
            )
            for text in (L.text, digits) if numeric else (L.text,):
                for m in rx.finditer(text):
                    per_layer[i].append(
                        Span(
                            m.start(),
                            m.end(),
                            label,
                            0.9,
                            "propagation",
                            L.text[m.start() : m.end()],
                        )
                    )
        for rx, label in shapes:
            for m in rx.finditer(L.text):
                per_layer[i].append(
                    Span(m.start(), m.end(), label, 0.85, "propagation", m.group())
                )
        # full names in any order, spaced or glued: "Corti Benvenuto", "BENVENUTO CORTI", "BOTTADANILO"
        for words in full:
            for order in (words, words[::-1]):
                rx = r"\s*".join(re.escape(w.translate(_AS_LETTERS)) for w in order)
                # glued text ends at a capital: "prevpt:LindaMarshRX-550-2877"
                for m in re.finditer(rf"(?<![\w]){rx}", low):
                    if m.end() < len(L.text) and L.text[m.end()].islower():
                        continue
                    per_layer[i].append(
                        Span(
                            m.start(),
                            m.end(),
                            "PERSON",
                            0.9,
                            "propagation",
                            L.text[m.start() : m.end()],
                        )
                    )

    findings = []
    for i, L in enumerate(layers):
        findings += _merge(L, per_layer[i])
    return findings
