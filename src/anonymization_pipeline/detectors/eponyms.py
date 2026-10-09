"""Common filter for eponyms and homonymous drugs (``resources/detectors/eponyms.yaml``).

"Morbo di Parkinson", "manovra di Valsalva", "Coumadin" are not personal data: a value that
matches an entry of the list entirely is discarded, whichever detector proposed it (VLM
included), when proposed as a person (PERSONA family). Eponyms also when proposed as a drug
("manovra di Valsalva" is not a drug); not as a diagnosis, because the rest of the diagnosis would
stay in clear text.
"""

import functools

from anonymization_pipeline.detectors.base import Proposal
from anonymization_pipeline.pipeline_config import registry
from anonymization_pipeline.pipeline_config.resources import read_yaml

FILE = "detectors/eponyms.yaml"


def _normalize(s: str) -> str:
    return " ".join(s.split())


@functools.cache
def lists() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Eponyms, drugs and common words of ``eponyms.yaml`` (read at first use, then cached).

    Raises:
        ValueError: On an unknown key.
    """
    data = read_yaml(FILE)
    if unknown := set(data) - {"eponyms", "drugs", "common_words"}:
        raise ValueError(f"{FILE}: unknown keys {', '.join(sorted(unknown))}")

    def entries(key: str) -> frozenset[str]:
        return frozenset(_normalize(str(p)).lower() for p in data.get(key) or [])

    return entries("eponyms"), entries("drugs"), entries("common_words")


def is_excluded(value: str, category: str) -> bool:
    """True for an eponym (proposed as a person or a drug), or a drug or a common word proposed as
    a person."""
    eponyms, drugs, common = lists()
    v = _normalize(value)
    if category == "FARMACO":
        return v.lower() in eponyms
    if registry().family(category) != "PERSONA":
        return False
    return v.lower() in eponyms or v.lower() in drugs or (v.islower() and v in common)


def filter_proposals(proposals: list[Proposal]) -> list[Proposal]:
    return [p for p in proposals if not is_excluded(p.value, p.category)]
