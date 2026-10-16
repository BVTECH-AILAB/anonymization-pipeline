"""NER detectors: GLiNER and GLiNER2 multilingual PII models, labels mapped to the taxonomy.

Two models run together by default (``CombinedNER``): their spans are joined,
which finds more personal data than either alone (docs/ANONYMIZATION.md, "NER
choice"). Models are loaded by ``load()`` (app lifespan) or injected, so unit
tests never load weights. Both run locally: no request leaves the pod.
"""

import logging
import threading
from typing import Any, Protocol

from gliner import GLiNER
from gliner2 import GLiNER2

from anonmyzation_pipeline.services.anonymization.device import resolve_device
from anonmyzation_pipeline.services.anonymization.layers import Span

logger = logging.getLogger(__name__)


# GLiNER prompt label -> taxonomy label
LABEL_MAP = {
    "person": "PERSON",
    "city": "LOCATION",
    "street address": "ADDRESS",
    "organization": "ORGANIZATION",
    "phone number": "PHONE",
    "email": "EMAIL",
    "date of birth": "DATE_OF_BIRTH",
    "identity document number": "ID_DOCUMENT",
}
# GLiNER2 PII label -> taxonomy label (fastino/gliner2-privacy-filter-PII-multi vocabulary)
LABEL_MAP_V2 = {
    "person": "PERSON",
    "city": "LOCATION",
    "street_address": "ADDRESS",
    "phone_number": "PHONE",
    "email": "EMAIL",
    "date_of_birth": "DATE_OF_BIRTH",
    "passport_number": "ID_DOCUMENT",
    "national_id_number": "ID_DOCUMENT",
    "drivers_license_number": "ID_DOCUMENT",
}


class NerEngine(Protocol):
    """Anything that finds entity spans in a text."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def detect(self, text: str) -> list[Span]: ...


class GlinerNER:
    """GLiNER zero-shot NER over chunks of whole lines."""

    source = "gliner"  # shown with each finding

    def __init__(
        self,
        model_id: str = "urchade/gliner_multi_pii-v1",
        threshold: float = 0.4,
        max_words: int = 200,
        device: str = "auto",
        model: Any | None = None,
    ) -> None:
        self.model_id, self.threshold, self.max_words = model_id, threshold, max_words
        self.device = device
        self._model = model
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        """True once the model is available."""
        return self._model is not None

    def load(self) -> None:
        """Load the GLiNER model (downloads weights on first use)."""
        with self._lock:
            if self._model is not None:
                return
            model = GLiNER.from_pretrained(self.model_id)
            device = resolve_device(self.device)
            self._model = model.to(device)
            logger.info("NER model loaded: model=%s device=%s", self.model_id, device)

    def _chunks(self, text: str) -> list[tuple[int, str]]:
        """Split on line boundaries into chunks of at most ``max_words`` words."""
        out: list[tuple[int, str]] = []
        start, words, pos = 0, 0, 0
        for line in text.splitlines(keepends=True):
            n = len(line.split())
            if words and words + n > self.max_words:
                out.append((start, text[start:pos]))
                start, words = pos, 0
            words += n
            pos += len(line)
        if start < len(text):
            out.append((start, text[start:]))
        return out

    def detect(self, text: str) -> list[Span]:
        """Entity spans of ``text`` with offsets into ``text``.

        Args:
            text: Page text.

        Returns:
            Spans labelled with the taxonomy.
        """
        if self._model is None:
            self.load()
        model = self._model
        if model is None:
            raise RuntimeError("NER model not available")
        out = []
        for off, chunk in self._chunks(text):
            if chunk.strip():
                out += [
                    Span(off + a, off + b, label, score, self.source, value)
                    for a, b, label, score, value in self._entities(model, chunk)
                ]
        return out

    def _entities(
        self, model: Any, chunk: str
    ) -> list[tuple[int, int, str, float, str]]:
        """``(start, end, taxonomy label, score, text)`` of the entities of one chunk."""
        return [
            (e["start"], e["end"], LABEL_MAP[e["label"]], float(e["score"]), e["text"])
            for e in model.predict_entities(
                chunk, list(LABEL_MAP), threshold=self.threshold
            )
        ]


class Gliner2NER(GlinerNER):
    """GLiNER2 PII model (``fastino/gliner2-privacy-filter-PII-multi``), same chunking as GLiNER."""

    source = "gliner2"

    def __init__(
        self,
        model_id: str = "fastino/gliner2-privacy-filter-PII-multi",
        threshold: float = 0.4,
        max_words: int = 200,
        device: str = "auto",
        model: Any | None = None,
    ) -> None:
        super().__init__(model_id, threshold, max_words, device, model)

    def load(self) -> None:
        """Load the GLiNER2 model (downloads weights on first use)."""
        with self._lock:
            if self._model is not None:
                return
            device = resolve_device(self.device)
            self._model = GLiNER2.from_pretrained(self.model_id).to(device)
            logger.info("NER model loaded: model=%s device=%s", self.model_id, device)

    def _entities(
        self, model: Any, chunk: str
    ) -> list[tuple[int, int, str, float, str]]:
        res = model.extract_entities(
            chunk,
            list(LABEL_MAP_V2),
            threshold=self.threshold,
            include_confidence=True,
            include_spans=True,
        )
        return [
            (
                e["start"],
                e["end"],
                LABEL_MAP_V2[label],
                float(e["confidence"]),
                e["text"],
            )
            for label, found in res.get("entities", {}).items()
            if label in LABEL_MAP_V2
            for e in found
        ]


class CombinedNER:
    """Several NER engines on the same text: the spans of all of them.

    Each engine loads on its own; the combination is ready when all are.
    """

    def __init__(self, *engines: NerEngine) -> None:
        self.engines = engines

    @property
    def ready(self) -> bool:
        """True once every engine is loaded."""
        return all(e.ready for e in self.engines)

    def load(self) -> None:
        """Load the engines not loaded yet; the first failure is raised."""
        for engine in self.engines:
            if not engine.ready:
                engine.load()

    def detect(self, text: str) -> list[Span]:
        """The spans of every engine on ``text``."""
        return [s for engine in self.engines for s in engine.detect(text)]
