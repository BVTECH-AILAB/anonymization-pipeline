"""Detection: page Markdown (+ image) -> sensitive values, with several detectors in union.

Detectors (``phases.detection.detectors``), run in parallel on every page:
  - vlm: one call per page and per category pass, answer constrained by a JSON schema with only
    the categories of the pass;
  - rules, labels, ner (``detectors`` package): functions of the text only, run in a thread.
One is enough to flag a value. Every proposed value goes through the same rules (eponym filter,
inactive category -> generic one) and is anchored to the text (``text.find``): those not found
are discarded as hallucinations. The union follows the reliability order rules > labels > ner >
vlm: for the same text, between categories of different families redaction uses the first. The
same VLM search serves the verification, on the redacted text.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from anonymization_pipeline import detectors, metrics
from anonymization_pipeline.detectors import (
    Detector,
    DetectorUnavailable,
    Proposal,
    eponyms,
)
from anonymization_pipeline.domain import Document, Entity, Page
from anonymization_pipeline.pipeline_config import PipelineConfig, registry, render
from anonymization_pipeline.text import Span, find
from anonymization_pipeline.vlm import VLMClient, gather_or_cancel
from anonymization_pipeline.vlm.responses import entity_schema, parse_entities

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    """Entities found, plus counters for the report (no values)."""

    entities: list[Entity] = field(default_factory=list)
    hallucinations_discarded: int = 0
    inactive_categories: int = 0
    eponyms_discarded: int = 0
    sources: dict[str, int] = field(default_factory=dict)  # detector -> values found
    warnings: list[str] = field(default_factory=list)

    def add(self, other: "Outcome") -> None:
        seen = {(e.text, e.category) for e in self.entities}
        for entity in other.entities:
            if (entity.text, entity.category) not in seen:
                seen.add((entity.text, entity.category))
                self.entities.append(entity)
        self.hallucinations_discarded += other.hallucinations_discarded
        self.inactive_categories += other.inactive_categories
        self.eponyms_discarded += other.eponyms_discarded


def _anchor(
    cfg: PipelineConfig,
    page: Page,
    text: str,
    proposals: list[Proposal],
    source: str,
    exclude: list[Span] | None = None,
) -> Outcome:
    """Proposed values -> entities anchored to the text.

    Eponyms discarded (when the filter is on); an inactive category falls back to its generic one
    if active, otherwise the value is discarded; a value not found in the text is a hallucination.
    """
    reg = registry()
    active = set(cfg.active())
    anchoring = cfg.phases.detection.anchoring
    outcome = Outcome()
    if cfg.phases.detection.detectors.eponyms:
        kept = eponyms.filter_proposals(proposals)
        outcome.eponyms_discarded = len(proposals) - len(kept)
        proposals = kept
    forms: dict[tuple[str, str], None] = {}
    filtered = cfg.phases.detection.detectors.eponyms
    for proposal in proposals:
        category = proposal.category
        if category not in active:
            generic = reg.generic.get(category)
            if generic not in active:
                outcome.inactive_categories += 1
                continue
            category = generic
        spans = find(text, proposal.value, anchoring, exclude)
        if not spans:
            outcome.hallucinations_discarded += 1
            continue
        if filtered:
            spans = [
                (s, e) for s, e in spans if not eponyms.is_excluded(text[s:e], category)
            ]
            if not spans:
                outcome.eponyms_discarded += 1
                continue
        for s, e in spans:
            forms.setdefault((text[s:e], category))
    outcome.entities = [
        Entity(text=t, category=c, page=page.index, source=source) for t, c in forms
    ]
    return outcome


async def _ask_vlm(
    client: VLMClient,
    cfg: PipelineConfig,
    phase: str,
    page: Page,
    text: str,
    categories: list[str],
) -> list[Proposal]:
    """Ask the model for the values of ``categories`` in ``text`` (calls assigned to the page)."""
    phase_cfg = (
        cfg.phases.verification if phase == "verification" else cfg.phases.detection
    )
    images = [page.encoded_image] if phase_cfg.use_image and page.encoded_image else []
    system = render(cfg, phase, "system")
    user = render(
        cfg,
        phase,
        "user",
        categories=registry().lines(categories),
        text=text,
        page=page.index + 1,
    )
    with metrics.on_page(page.index):
        found, _ = await client.call_json(
            phase, system, user, images, entity_schema(categories), parse_entities
        )
    return [Proposal(v["text"], v["category"]) for v in found]


async def find_values(
    client: VLMClient,
    cfg: PipelineConfig,
    phase: str,
    page: Page,
    text: str,
    categories: list[str],
    exclude: list[Span] | None = None,
) -> Outcome:
    """Ask the model for the values of ``categories`` in ``text`` and anchor them to the text."""
    proposals = await _ask_vlm(client, cfg, phase, page, text, categories)
    return _anchor(cfg, page, text, proposals, phase, exclude)


async def detect(doc: Document, client: VLMClient, cfg: PipelineConfig) -> Outcome:
    """Every active detector on every page, in parallel (the VLM within the backend concurrency
    limit, the others in threads), then the union in reliability order."""
    pages = [p for p in doc.pages if p.text.strip()]
    text_detectors = detectors.active(cfg)
    warnings: dict[str, None] = {}

    async def run_text_detector(detector: Detector, page: Page) -> list[Proposal]:
        try:
            with metrics.measure(f"detection.{detector.name}", page.index):
                return await asyncio.to_thread(detector.detect, page.text)
        except DetectorUnavailable as exc:
            if f"{detector.name}_unavailable" not in warnings:
                logger.warning("Detector %s unavailable: %s", detector.name, exc)
            warnings[f"{detector.name}_unavailable"] = None
            return []

    passes = cfg.passes("detection") if cfg.phases.detection.detectors.vlm else []
    jobs = [run_text_detector(d, p) for d in text_detectors for p in pages]
    jobs += [
        _ask_vlm(client, cfg, "detection", p, p.text, cats)
        for cats in passes
        for p in pages
    ]
    results = iter(await gather_or_cancel(jobs))
    proposals: dict[str, dict[int, list[Proposal]]] = {}
    for name in [d.name for d in text_detectors] + ["vlm"] * len(passes):
        for page in pages:
            proposals.setdefault(name, {}).setdefault(page.index, []).extend(
                next(results)
            )

    with metrics.measure("detection.union"):
        return _union(cfg, pages, proposals, warnings)


def _union(
    cfg: PipelineConfig,
    pages: list[Page],
    proposals: dict[str, dict[int, list[Proposal]]],
    warnings: dict[str, None],
) -> Outcome:
    """Anchoring and union in reliability order."""
    outcome = Outcome(warnings=list(warnings))
    for name in (*detectors.ORDER, "vlm"):
        if name not in proposals:
            continue
        source = "detection" if name == "vlm" else name
        found = 0
        for page in pages:
            partial = _anchor(cfg, page, page.text, proposals[name][page.index], source)
            found += len(partial.entities)
            outcome.add(partial)
        outcome.sources[name] = found
    return outcome
