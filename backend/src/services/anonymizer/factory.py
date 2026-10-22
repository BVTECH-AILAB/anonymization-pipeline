"""Build the pipeline from a ``PipelineConfig`` and load its engines."""

import logging
import ssl

import httpx

from src.llms.ner import AilabNER
from src.services.anonymizer.config import PipelineConfig
from src.services.anonymizer.pipeline import Pipeline
from src.services.anonymizer.reader import (
    LiteparseReader,
    TextReader,
)

logger = logging.getLogger(__name__)


def build_reader(config: PipelineConfig) -> TextReader:
    """The text reader: LiteParse plus the OCR server. Nothing is loaded here."""
    return LiteparseReader(config.ocr_server_url, dpi=config.ocr_dpi)


def _tls_context(ca_bundle: str) -> ssl.SSLContext:
    """TLS context trusting ``ca_bundle`` (PEM), or the system CAs.

    An unreadable bundle never raises: the system CAs are used, the handshake
    with an internal certificate fails and the engine stays not ready.
    """
    context = ssl.create_default_context()
    if ca_bundle:
        try:
            context.load_verify_locations(cafile=ca_bundle)
        except (OSError, ssl.SSLError) as exc:
            logger.error("NER CA bundle not loaded: error=%s", type(exc).__name__)
    return context


def build_pipeline(config: PipelineConfig) -> Pipeline:
    """Text reader and NER engine from ``config``. No request is sent here."""
    ner: AilabNER | None = None
    if config.ner_enabled:
        client = httpx.Client(
            base_url=config.ner_url,
            timeout=config.ner_timeout_seconds,
            verify=_tls_context(config.ner_ca_bundle),
        )
        ner = AilabNER(
            client,
            retries=config.ner_retries,
            backoff_seconds=config.ner_retry_backoff_seconds,
        )
    return Pipeline(
        build_reader(config),
        ner,
        policy=config.redaction_policy,
        ner_threshold=config.ner_threshold,
        person_threshold=config.ner_person_threshold,
    )


def load_models(pipeline: Pipeline) -> bool:
    """Load every engine that is not loaded yet; each one independently.

    A failure is logged (type only) and leaves the pipeline not ready, so a
    caller can retry: a model server that starts later is picked up.

    Returns:
        True when every engine is ready.
    """
    engines = [("reader", pipeline.reader), ("ner", pipeline.ner)]
    for name, engine in engines:
        if engine is None or engine.ready:
            continue
        try:
            engine.load()
        except Exception as exc:
            logger.error(
                "Model loading failed: engine=%s error=%s", name, type(exc).__name__
            )
    return pipeline.ready
