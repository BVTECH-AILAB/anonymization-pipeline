"""Combine detectors on every text layer of a document.

1. rules + NER on each layer (recall first: any detector is enough)
2. allowlist filter (eponyms, drugs, saints, clinical vocabulary)
3. document-level propagation: every name token and identifier found anywhere
   is searched in every layer ("Sig. Rossi" in the body, "ROSSI M." in a footer)
4. merge overlapping spans into one finding with the most specific label
"""

import re
from dataclasses import dataclass, field

from anonmyzation_pipeline.services.anonymization.detect import allowlist, rules
from anonmyzation_pipeline.services.anonymization.detect.ner import NerEngine
from anonmyzation_pipeline.services.anonymization.layers import Span, TextLayer

NER_LABELS = {"PERSON", "LOCATION", "ORGANIZATION", "ADDRESS"}
PROPAGATE_EXACT = {
    "FISCAL_CODE",
    "HEALTH_CARD",
    "PHONE",
    "EMAIL",
    "MEDICAL_RECORD",
    "ID_DOCUMENT",
    "PRESCRIPTION_ID",
    "PROFESSIONAL_ID",
    "IBAN",
    "DATE_OF_BIRTH",
}
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


def _name_tokens(text: str) -> set[str]:
    toks = set()
    for w in re.findall(r"[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ'-]+", text):
        lw = w.lower()
        if (
            len(w) >= 3
            and not _HON.match(w)
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
        spans = rules.detect(L.text)
        if ner is not None:
            for s in ner.detect(L.text):
                if s.label in NER_LABELS and s.score >= (
                    person_threshold if s.label == "PERSON" else ner_threshold
                ):
                    spans.append(s)
        per_layer[i] = [
            s
            for s in spans
            if s.label not in NER_LABELS
            or (s.source == "rules" and s.label != "PERSON")
            or not allowlist.is_not_pii(L.text, s.start, s.end, s.label)
        ]

    # ---- propagation across the whole document -------------------------------
    names, exact, full = set(), set(), set()
    for i, L in enumerate(layers):
        for s in per_layer[i]:
            val = L.text[s.start : s.end]
            if s.label == "PERSON":
                toks = _name_tokens(val)
                names |= toks
                words = [
                    w
                    for w in re.findall(r"[A-Za-zÀ-ÿ'][A-Za-zÀ-ÿ'-]+", val)
                    if w.lower() in toks
                ]
                if len(words) >= 2:
                    full.add(tuple(w.lower() for w in words[:3]))
            elif s.label in PROPAGATE_EXACT and len(val) >= 6:
                exact.add((val, s.label))
    for i, L in enumerate(layers):
        low = L.text.lower()
        for n in names:
            for m in re.finditer(rf"(?<![\w]){re.escape(n)}(?![\w])", low):
                a, b = m.start(), m.end()
                if not L.text[a].isupper():  # "sostanza bianca" is not Dott.ssa Bianca
                    continue
                # extend over an initial: "Rossi M." / "M. Rossi"
                tail = re.match(r"[ \t]+[A-Z]\.", L.text[b : b + 4])
                if tail:
                    b += tail.end()
                head = re.search(r"[A-Z]\.[ \t]+$", L.text[max(0, a - 4) : a])
                if head:
                    a -= len(head.group())
                if allowlist.is_eponym_use(L.text, a, b) and n in allowlist.EPONYMS:
                    continue
                if allowlist.is_saint(L.text, a):
                    continue
                per_layer[i].append(
                    Span(a, b, "PERSON", 0.8, "propagation", L.text[a:b])
                )
        for val, label in exact:
            for m in re.finditer(re.escape(val), L.text):
                per_layer[i].append(
                    Span(m.start(), m.end(), label, 0.9, "propagation", val)
                )
        # full names in any order, spaced or glued: "Corti Benvenuto", "BENVENUTO CORTI", "BOTTADANILO"
        for words in full:
            for order in (words, words[::-1]):
                rx = r"\s*".join(re.escape(w) for w in order)
                for m in re.finditer(rf"(?<![\w]){rx}(?![\w])", low):
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
