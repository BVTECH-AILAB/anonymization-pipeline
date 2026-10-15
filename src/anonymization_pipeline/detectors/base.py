"""Common interface of the text detectors: the only thing they share.

A detector receives the Markdown of a page and returns the values it considers personal data,
with their category. It returns no offsets: anchoring to the text (``text.find``) and the common
rules (inactive categories, eponyms) are applied afterwards, the same for every detector.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Proposal:
    value: str
    category: str


class Detector(Protocol):
    name: str

    def detect(self, text: str) -> list[Proposal]:
        """Values proposed in the text of a page (synchronous: the pipeline runs it in a thread)."""
        ...


class DetectorUnavailable(RuntimeError):
    """The detector cannot work (library or model missing): the pipeline skips it with a warning."""
