"""NER through the AILAB NER service (``POST /batch_ner``), labels mapped to the taxonomy.

The service only finds entities in text: the pipeline keeps the link between
text and page (``TextLayer``), so the spans are redacted on the original PDF.
Its ``obfuscated_text`` is ignored. The service preprocesses the text before
the model ("Ospedale" becomes "EPONYM0", an email becomes "email@email.com"),
so its offsets can drift after a substitution: every entity is checked against
the original text and moved to the nearest occurrence of its word, or dropped.

Fail-closed: a service that is down or answers badly raises
``NerUnavailableError``, the job fails and nothing is released. The endpoints
that change the service configuration (``/medical_orgs``, ``/medical_labels``)
are shared with ai-platform and never called.
"""

import logging
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from anonmyzation_pipeline.errors import NerUnavailableError
from anonmyzation_pipeline.services.anonymization.detect import labels
from anonmyzation_pipeline.services.anonymization.layers import Span

logger = logging.getLogger(__name__)


# AILAB entity group -> taxonomy label; the other groups (MALATTIA, MEDICINA ...) are ignored
LABEL_MAP = {
    "NOME": "PERSON",
    "COGNOME": "PERSON",
    "LUOGO": "LOCATION",
    "INDIRIZZO": "ADDRESS",
    "RAGIONE_SOCIALE": "ORGANIZATION",
    "NUMERO_TELEFONO": "PHONE",
    "EMAIL": "EMAIL",
    "DATA_NASCITA": "DATE_OF_BIRTH",
    "NUMERO_DOCUMENTO": "ID_DOCUMENT",
    "CODICE_FISCALE": "FISCAL_CODE",
}
# answers of an overloaded or restarting service: the same request can succeed later
RETRY_STATUS = frozenset({502, 503, 504})


class NerEngine(Protocol):
    """Anything that finds entity spans in a text."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def detect(self, text: str) -> list[Span]: ...


def _compact(value: str) -> str:
    """A word without spaces and WordPiece markers ("01 / 08 / 1985" -> "01/08/1985")."""
    return re.sub(r"\s+|##", "", value).lower()


def _join_pieces(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join WordPiece continuations into the word they split ("Mo" + "##cenigo").

    A continuation is joined only when it starts where the previous entity ends:
    the word and offsets are then the ones of the whole word, which can be aligned.
    """
    out: list[dict[str, Any]] = []
    for e in sorted(entities, key=lambda e: int(e["start"])):
        word, start, end = str(e["word"]), int(e["start"]), int(e["end"])
        prev = out[-1] if out else None
        if word.startswith("##") and prev is not None and start == prev["end"]:
            prev["word"] += word[2:]
            prev["end"] = end
            prev["score"] = min(prev["score"], float(e["score"]))
        else:
            out.append(
                {
                    "entity_group": str(e.get("entity_group", "")),
                    "word": word,
                    "start": start,
                    "end": end,
                    "score": float(e["score"]),
                }
            )
    return out


def _starts_a_field(piece: str, after: str) -> bool:
    """A line that begins another field: "Visto:", "Nato il", "(centro ...)"."""
    if not re.search(r"\w", piece):
        return True
    first = piece.split()[0].strip(".,;:()").lower()
    return first in labels.rules().stop_values or after.lstrip(" \t").startswith(":")


def _cut_at_field(text: str, start: int, end: int) -> int:
    """End of a span that runs into the next line only while that line continues it.

    The service reads line breaks as spaces, so its entities can swallow the label
    of the next field ("Grippo" + "Visto:" on the next line). A value wrapped on two
    lines stays whole.
    """
    lines = text[start:end].split("\n")
    cut = pos = start + len(lines[0])
    for line in lines[1:]:
        pos += 1
        line_end = text.find("\n", pos + len(line))
        after = text[pos + len(line) : line_end if line_end >= 0 else len(text)]
        if line.strip() and _starts_a_field(line.strip(), after):
            break
        pos += len(line)
        cut = pos
    return cut


def _align(chunk: str, start: int, end: int, word: str) -> tuple[int, int] | None:
    """Offsets of ``word`` in ``chunk``: the service ones if they hold, else the nearest match."""
    key = _compact(word)
    if not key:
        return None
    if 0 <= start < end <= len(chunk) and _compact(chunk[start:end]) == key:
        span = (start, end)
    else:
        pattern = r"\s*".join(map(re.escape, key))
        if key[0].isalnum():
            pattern = r"(?<!\w)" + pattern
        if key[-1].isalnum():
            pattern += r"(?!\w)"
        matches = [m.span() for m in re.finditer(pattern, chunk, re.I)]
        if not matches:
            return None
        span = min(matches, key=lambda m: abs(m[0] - start))
    a, b = span
    while a < b and chunk[a].isspace():
        a += 1
    while b > a and chunk[b - 1].isspace():
        b -= 1
    return (a, b) if a < b else None


class AilabNER:
    """Client of the AILAB NER service over chunks of whole lines.

    The ``httpx.Client`` is synchronous on purpose: detection runs in the job
    worker threads, not on the event loop. The engine owns it (``close()``).
    Transient failures (timeouts, connection errors, 502/503/504) are retried
    ``retries`` times, waiting ``backoff_seconds`` doubled at each attempt: the
    request only reads, so repeating it is safe.
    """

    source = "ailab-ner"  # shown with each finding

    def __init__(
        self,
        client: httpx.Client,
        max_words: int = 200,
        retries: int = 2,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_words = max_words
        self.retries, self.backoff_seconds = retries, backoff_seconds
        self._client = client
        self._sleep = sleep
        self._ready = False

    @property
    def ready(self) -> bool:
        """True once the service has answered its health check."""
        return self._ready

    def load(self) -> None:
        """Check that the service is reachable and healthy.

        Raises:
            NerUnavailableError: No URL configured, or the service is down.
        """
        if not self._client.base_url.host:
            raise NerUnavailableError()
        try:
            response = self._client.get("/health")
            response.raise_for_status()
            healthy = response.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            logger.warning("NER health check failed: error=%s", type(exc).__name__)
            raise NerUnavailableError() from exc
        if not healthy:
            logger.warning("NER health check failed: status not ok")
            raise NerUnavailableError()
        self._ready = True
        logger.info("NER service available: host=%s", self._client.base_url.host)

    def close(self) -> None:
        """Release the HTTP connections."""
        self._client.close()

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

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """POST ``body``, retrying transient failures; the last failure is returned or raised."""
        for attempt in range(self.retries + 1):
            try:
                response = self._client.post(path, json=body)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise
                error = type(exc).__name__
            else:
                if response.status_code not in RETRY_STATUS or attempt == self.retries:
                    return response
                error = f"HTTP {response.status_code}"
            wait = self.backoff_seconds * 2**attempt
            logger.warning(
                "NER request retried: attempt=%d error=%s wait_s=%.1f",
                attempt + 1,
                error,
                wait,
            )
            self._sleep(wait)
        raise AssertionError("unreachable")

    def _batch(self, queries: list[str]) -> list[list[dict[str, Any]]]:
        """Entities of each query, in order.

        Raises:
            NerUnavailableError: The request failed or the answer has another shape.
        """
        try:
            response = self._post("/batch_ner", {"queries": queries})
            response.raise_for_status()
            results = response.json()["results"]
            if len(results) != len(queries):
                raise ValueError("results do not match the queries")
            return [list(r["entities"]) for r in results]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("NER request failed: error=%s", type(exc).__name__)
            raise NerUnavailableError() from exc

    def detect(self, text: str) -> list[Span]:
        """Entity spans of ``text`` with offsets into ``text``.

        Args:
            text: Page text.

        Returns:
            Spans labelled with the taxonomy; adjacent name and surname are one person.

        Raises:
            NerUnavailableError: The service could not analyse the text.
        """
        chunks = [(off, chunk) for off, chunk in self._chunks(text) if chunk.strip()]
        if not chunks:
            return []
        out: list[Span] = []
        dropped = 0
        for (off, chunk), entities in zip(
            chunks, self._batch([c for _, c in chunks]), strict=True
        ):
            spans: dict[tuple[int, int, str], Span] = {}
            for e in _join_pieces(entities):
                label = LABEL_MAP.get(e["entity_group"])
                if label is None:
                    continue
                pos = _align(chunk, e["start"], e["end"], e["word"])
                if pos is None:
                    dropped += 1
                    continue
                a, b = pos
                spans.setdefault(
                    (a, b, label),
                    Span(
                        off + a,
                        off + b,
                        label,
                        float(e["score"]),
                        self.source,
                        chunk[a:b],
                    ),
                )
            joined = self._join_names(
                text, sorted(spans.values(), key=lambda s: s.start)
            )
            out += [t for s in joined if (t := self._trim(text, s)) is not None]
        if dropped:
            logger.warning("NER entities not aligned to the text: count=%d", dropped)
        return out

    def _trim(self, text: str, s: Span) -> Span | None:
        """The span cut where the next line starts a field; None when nothing is left."""
        end = _cut_at_field(text, s.start, s.end)
        while end > s.start and text[end - 1].isspace():
            end -= 1
        if not re.search(r"\w", text[s.start : end]):
            return None
        if end == s.end:
            return s
        return Span(s.start, end, s.label, s.score, s.source, text[s.start : end])

    def _join_names(self, text: str, spans: list[Span]) -> list[Span]:
        """Join person spans separated only by spaces ("Mario" + "Rossi")."""
        out: list[Span] = []
        for s in spans:
            prev = out[-1] if out else None
            if (
                prev is not None
                and prev.label == s.label == "PERSON"
                and prev.end <= s.start
                and not text[prev.end : s.start].strip()
            ):
                out[-1] = Span(
                    prev.start,
                    s.end,
                    "PERSON",
                    min(prev.score, s.score),
                    self.source,
                    text[prev.start : s.end],
                )
            else:
                out.append(s)
        return out
