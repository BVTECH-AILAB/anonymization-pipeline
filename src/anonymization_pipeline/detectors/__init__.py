"""Text detectors: rules, label -> value, and the eponym filter they share.

Every detector lives in its own module and does not know the others: they share only the
interface of ``base`` (Proposal, Detector, DetectorUnavailable) and the regex syntax of their
files (``patterns``). This is the only place that knows them all.
"""

from anonymization_pipeline.detectors import eponyms, labels, rules
from anonymization_pipeline.detectors.base import (
    Detector,
    DetectorUnavailable,
    Proposal,
)
from anonymization_pipeline.detectors.labels import LabelsDetector
from anonymization_pipeline.detectors.rules import RulesDetector

# reliability order: for the same text, between categories of different families the first wins
ORDER = ("rules", "labels")


def load_resources() -> None:
    """Read and validate every detector file (then cached), whether the detector is on or not.

    Called at startup: a wrong file stops the service instead of failing the first request.

    Raises:
        ValueError: If a file is invalid (the message names the file).
    """
    rules.rules()
    labels.rules()
    eponyms.lists()


__all__ = [
    "ORDER",
    "Detector",
    "DetectorUnavailable",
    "LabelsDetector",
    "Proposal",
    "RulesDetector",
    "load_resources",
]
