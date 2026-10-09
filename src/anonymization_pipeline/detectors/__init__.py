"""Text detectors that work in union with the VLM: rules, label -> value, NER.

Every detector lives in its own module and does not know the others: they share only the
interface of ``base`` (Proposal, Detector, DetectorUnavailable). This is the only place that knows
them all: it builds them, only when active in the configuration, in the reliability order used by
the union.
"""

from anonymization_pipeline.detectors.base import (
    Detector,
    DetectorUnavailable,
    Proposal,
)
from anonymization_pipeline.detectors.labels import LabelsDetector
from anonymization_pipeline.detectors.ner import NerDetector
from anonymization_pipeline.detectors.rules import RulesDetector
from anonymization_pipeline.pipeline_config import PipelineConfig

# reliability order: for the same text, between categories of different families the first wins
ORDER = ("rules", "labels", "ner")


def active(cfg: PipelineConfig) -> list[Detector]:
    """Active text detectors, in ORDER."""
    d = cfg.phases.detection.detectors
    out: list[Detector] = []
    if d.rules:
        out.append(RulesDetector())
    if d.labels:
        out.append(LabelsDetector())
    if d.ner.enabled:
        out.append(NerDetector(d.ner))
    return out


__all__ = ["ORDER", "Detector", "DetectorUnavailable", "Proposal", "active"]
