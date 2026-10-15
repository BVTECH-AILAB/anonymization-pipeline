"""Aggregation of the document scores of a profile and its verdict against the thresholds."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from anonval.scoring import DocScore, ValueOutcome
from anonval.settings import Thresholds


def ratio(num: float, den: float) -> float | None:
    """``num / den``, None when there is nothing to divide."""
    return num / den if den else None


@dataclass
class Counts:
    expected: int = 0
    detected: int = 0
    leaked: int = 0

    def add(self, v: ValueOutcome) -> None:
        self.expected += 1
        self.detected += v.detected
        self.leaked += v.leaked

    def as_dict(self) -> dict[str, Any]:
        return {
            "attesi": self.expected,
            "rilevati": self.detected,
            "sfuggiti": self.leaked,
            "recall": ratio(self.detected, self.expected),
            "tasso_fuga": ratio(self.leaked, self.expected),
        }


@dataclass
class Summary:
    documents: int = 0
    released: int = 0
    blocked: Counter[str] = field(default_factory=Counter)  # reason -> documents
    failed: int = 0
    values: Counts = field(default_factory=Counts)
    not_located: int = (
        0  # expected values the scoring could not find in the original PDF
    )
    boxes: int = 0
    correct_boxes: int = 0
    disabled_boxes: int = 0
    false_positives: int = 0
    negatives_violated: int = 0
    text_leaks: int = 0
    markdown_documents: int = 0
    strategies: Counter[str] = field(
        default_factory=Counter
    )  # redacted pages per strategy
    markdown_expected: int = 0
    markdown_leaked: int = 0
    by_category: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    by_difficulty: dict[str, Counts] = field(
        default_factory=lambda: defaultdict(Counts)
    )
    by_type: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    seconds: list[float] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        return ratio(self.values.detected, self.values.expected)

    @property
    def leak_rate(self) -> float | None:
        return ratio(self.values.leaked, self.values.expected)

    @property
    def markdown_leak_rate(self) -> float | None:
        return ratio(self.markdown_leaked, self.markdown_expected)

    @property
    def precision(self) -> float | None:
        return ratio(self.correct_boxes, self.correct_boxes + self.false_positives)


def summarize(scores: list[DocScore]) -> Summary:
    """Totals of a profile over its documents (expected values of released documents only)."""
    s = Summary(documents=len(scores))
    for doc in scores:
        if doc.status == "blocked":
            s.blocked[doc.reason or "?"] += 1
        elif doc.status != "done":
            s.failed += 1
        if not doc.released:
            continue
        s.released += 1
        if doc.pipeline_s is not None:
            s.seconds.append(doc.pipeline_s)
        s.boxes += doc.boxes
        s.correct_boxes += doc.correct_boxes
        s.disabled_boxes += doc.disabled_boxes
        s.false_positives += len(doc.false_positives)
        s.negatives_violated += len(doc.negatives_violated)
        s.text_leaks += len(doc.text_leaks)
        s.strategies.update(doc.strategies)
        if doc.markdown:
            s.markdown_documents += 1
            s.markdown_expected += sum(
                1 for v in doc.values if v.expected and v.located
            )
            s.markdown_leaked += len(doc.markdown_leaks)
        for v in doc.values:
            if not v.expected:
                continue
            if not v.located:
                s.not_located += 1
                continue
            s.values.add(v)
            s.by_category[v.category].add(v)
            s.by_difficulty[doc.difficulty].add(v)
            s.by_type[doc.type].add(v)
    return s


def verdict(s: Summary, t: Thresholds) -> list[str]:
    """Thresholds not met (empty: the profile passes)."""
    out = []
    if s.failed:
        out.append(f"{s.failed} documenti non elaborati")
    if s.released == 0:
        out.append("nessun documento rilasciato")
    if s.recall is not None and s.recall < t.recall_min:
        out.append("recall sotto la soglia")
    if s.leak_rate is not None and s.leak_rate > t.tasso_fuga_max:
        out.append("tasso di fuga sopra la soglia")
    if s.precision is not None and s.precision < t.precision_min:
        out.append("precisione sotto la soglia")
    if s.markdown_leak_rate is not None and s.markdown_leak_rate > t.tasso_fuga_max:
        out.append("tasso di fuga nel Markdown sopra la soglia")
    if s.text_leaks:
        out.append("valori nel testo dei PDF rilasciati")
    return out


def as_dict(s: Summary, t: Thresholds) -> dict[str, Any]:
    """Metrics of a profile for ``metriche.json``."""
    seconds = sorted(s.seconds)
    return {
        "documenti": s.documents,
        "rilasciati": s.released,
        "bloccati": dict(s.blocked),
        "non_elaborati": s.failed,
        "valori": s.values.as_dict(),
        "non_localizzati": s.not_located,
        "riquadri": s.boxes,
        "riquadri_corretti": s.correct_boxes,
        "riquadri_categorie_disattivate": s.disabled_boxes,
        "falsi_positivi": s.false_positives,
        "precisione": s.precision,
        "negativi_violati": s.negatives_violated,
        "valori_nel_testo_rilasciato": s.text_leaks,
        "pagine_per_strategia": dict(s.strategies),
        "markdown": {
            "documenti": s.markdown_documents,
            "attesi": s.markdown_expected,
            "sfuggiti": s.markdown_leaked,
            "tasso_fuga": s.markdown_leak_rate,
        },
        "per_categoria": {k: v.as_dict() for k, v in sorted(s.by_category.items())},
        "per_difficolta": {k: v.as_dict() for k, v in sorted(s.by_difficulty.items())},
        "per_tipo": {k: v.as_dict() for k, v in sorted(s.by_type.items())},
        "secondi_documento": {
            "medio": ratio(sum(seconds), len(seconds)),
            "p95": seconds[int(0.95 * (len(seconds) - 1))] if seconds else None,
        },
        "esito": "PASS" if not verdict(s, t) else "FAIL",
        "motivi": verdict(s, t),
    }
