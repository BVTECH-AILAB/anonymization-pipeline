"""NER detector: GLiNER multilingual PII model, zero-shot labels mapped to the taxonomy.

The model is loaded by ``load()`` (app lifespan) or injected, so unit tests
never load weights.
"""

import logging
import threading
from typing import Any, Protocol

from gliner import GLiNER

from anonmyzation_pipeline.services.anonymization.layers import Span
from anonmyzation_pipeline.services.anonymization.ocr import resolve_device

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


class NerEngine(Protocol):
    """Anything that finds entity spans in a text."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def detect(self, text: str) -> list[Span]: ...


class GlinerNER:
    """GLiNER zero-shot NER over chunks of whole lines."""

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
        labels = list(LABEL_MAP)
        for off, chunk in self._chunks(text):
            if not chunk.strip():
                continue
            for e in model.predict_entities(chunk, labels, threshold=self.threshold):
                out.append(
                    Span(
                        off + e["start"],
                        off + e["end"],
                        LABEL_MAP[e["label"]],
                        float(e["score"]),
                        "gliner",
                        e["text"],
                    )
                )
        return out
