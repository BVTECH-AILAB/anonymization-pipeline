"""Detection: text layers of a document -> spans of personal data on every layer.

1. Every active text detector (``detectors.ORDER``) runs on every layer and proposes values with
   their category. Each value goes through the same rules: an inactive category falls back to its
   generic one or the value is dropped; the value is anchored to the text (``text.find``), and a
   value that is not there is a hallucination; occurrences that are eponyms in their context are
   dropped, and so are lowercase occurrences of a capitalised name ("sostanza bianca").
2. Propagation (``propagation``): every value found anywhere is searched in every layer, and the
   names of persons also in their other forms.
3. Overlapping spans of a layer become one span, with the category of the longest one.

The result names spans, never values: the values are in the layer texts.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from anonymization_pipeline.detection.propagation import (
    name_variants,
    propagate,
    same_case,
)
from anonymization_pipeline.detectors import (
    ORDER,
    Detector,
    DetectorUnavailable,
    LabelsDetector,
    NerDetector,
    Proposal,
    RulesDetector,
    eponyms,
)
from anonymization_pipeline.detectors.ner import Engine
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.text import Span, find

SOURCES = (*ORDER, "propagation")


@dataclass(frozen=True)
class Found:
    """A span of personal data in ``layers[layer].text``."""

    layer: int
    start: int
    end: int
    category: str
    source: str  # rules | labels | propagation


@dataclass
class Outcome:
    """Spans found, plus counters for the report (no values)."""

    found: list[Found] = field(default_factory=list)
    hallucinations_discarded: int = 0
    inactive_categories: int = 0
    eponyms_discarded: int = 0
    sources: dict[str, int] = field(default_factory=dict)  # detector -> spans found

    def values(self, layers: list[TextLayer]) -> set[tuple[str, str]]:
        """The distinct ``(text, category)`` of the spans (IN CLEAR TEXT: never log them)."""
        return {(layers[f.layer].text[f.start : f.end], f.category) for f in self.found}


def active(cfg: PipelineConfig, ner: Engine | None = None) -> list[Detector]:
    """Active text detectors, in reliability order.

    Raises:
        DetectorUnavailable: NER is enabled and no model was loaded.
    """
    d = cfg.detection.detectors
    out: list[Detector] = []
    if d.rules:
        out.append(RulesDetector())
    if d.labels:
        out.append(LabelsDetector())
    if cfg.detection.ner.enabled:
        if ner is None:
            raise DetectorUnavailable("NER enabled but no model loaded")
        out.append(NerDetector(cfg.detection.ner, ner))
    return out


def _anchor(
    cfg: PipelineConfig, text: str, proposals: list[Proposal], outcome: Outcome
) -> list[tuple[Span, str]]:
    """Proposed values -> anchored spans with their (possibly generic) category."""
    reg = registry()
    on = set(cfg.active())
    filtered = cfg.detection.detectors.eponyms
    out: list[tuple[Span, str]] = []
    for proposal in proposals:
        category = proposal.category
        if category not in on:
            generic = reg.generic.get(category)
            if generic not in on:
                outcome.inactive_categories += 1
                continue
            category = generic
        spans = [
            s
            for s in find(text, proposal.value, cfg.detection.anchoring)
            if same_case(proposal.value, category, text, s[0])
        ]
        if not spans:
            outcome.hallucinations_discarded += 1
            continue
        if filtered:
            spans = [s for s in spans if not eponyms.is_excluded(text, *s, category)]
            if not spans:
                outcome.eponyms_discarded += 1
                continue
        out += [(s, category) for s in spans]
    return out


def _merge(layer: int, found: list[Found]) -> list[Found]:
    """Overlapping spans -> one span with the category of the longest (then the most reliable
    source, then the most specific category of the registry)."""
    reg = registry()
    order = {name: i for i, name in enumerate(SOURCES)}

    def strength(f: Found) -> tuple[int, int, int]:
        return (
            -(f.end - f.start),
            order.get(f.source, len(order)),
            -reg.rank(f.category),
        )

    out: list[Found] = []
    group: list[Found] = []
    for f in sorted(found, key=lambda f: (f.start, -f.end)):
        if group and f.start < max(g.end for g in group):
            group.append(f)
            continue
        if group:
            out.append(_joined(layer, group, strength))
        group = [f]
    if group:
        out.append(_joined(layer, group, strength))
    return out


def _joined(
    layer: int, group: list[Found], strength: Callable[[Found], tuple]
) -> Found:
    winner = min(group, key=strength)
    start, end = min(g.start for g in group), max(g.end for g in group)
    return Found(layer, start, end, winner.category, winner.source)


def detect(
    layers: list[TextLayer], cfg: PipelineConfig, ner: Engine | None = None
) -> Outcome:
    """Personal data on every text layer of a document.

    Args:
        layers: Text layers of all pages (native and OCR).
        cfg: Configuration of the run.
        ner: The NER model loaded at startup, when NER is enabled.

    Returns:
        Merged spans per layer and the counters of the report.
    """
    outcome = Outcome()
    per_layer: list[list[Found]] = [[] for _ in layers]
    for detector in active(cfg, ner):
        for i, layer in enumerate(layers):
            if not layer.text.strip():
                continue
            spans = _anchor(cfg, layer.text, detector.detect(layer.text), outcome)
            per_layer[i] += [Found(i, s, e, c, detector.name) for (s, e), c in spans]

    values = {
        (layers[f.layer].text[f.start : f.end], f.category)
        for spans in per_layer
        for f in spans
    }
    extra = propagate(layers, values, cfg)
    if cfg.detection.propagation.names:
        extra += name_variants(layers, values, cfg)
    for i, s, e, category in extra:
        per_layer[i].append(Found(i, s, e, category, "propagation"))

    for i in range(len(layers)):
        outcome.found += _merge(i, per_layer[i])
    outcome.sources = dict(Counter(f.source for f in outcome.found))
    return outcome
