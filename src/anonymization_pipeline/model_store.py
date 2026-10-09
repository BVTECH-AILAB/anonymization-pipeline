"""Download of the models before the service starts: every VLM of the models file catalog (in the
backend), NER and docling.

This is the only step that needs internet access. In compose it runs in the ``models-init``
service (``just models``), the only one attached to a network with egress; at runtime the service
and the VLM backend work offline.

Usage: ``python -m anonymization_pipeline.model_store`` (exit code 1 if a model is missing).
"""

import asyncio
import logging

import httpx

from anonymization_pipeline.config import Settings, get_settings
from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import NER_INSTALLED
from anonymization_pipeline.detectors.ner import download as download_ner
from anonymization_pipeline.extraction.native_pdf import (
    DoclingUnavailable,
    models_present,
)
from anonymization_pipeline.extraction.native_pdf import download as download_docling
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.pipeline_config import load_pipeline_config
from anonymization_pipeline.vlm import VLMError, create_client

logger = logging.getLogger(__name__)


async def download_all(settings: Settings, http: httpx.AsyncClient) -> bool:
    """Download every missing model of the configuration; return True if all are available."""
    cfg = load_pipeline_config(settings)
    ok = True
    client = create_client(cfg, http)
    for name in cfg.models:
        try:
            await client.pull(name, force=True)
            logger.info("VLM model %s available", name)
        except (httpx.HTTPError, VLMError) as exc:
            logger.error("VLM model %s not available: %s", name, exc)
            ok = False
    ner = cfg.phases.detection.detectors.ner
    # a request may switch NER on: download it whenever it can run
    if ner.enabled or NER_INSTALLED:
        try:
            await asyncio.to_thread(download_ner, ner)
            logger.info("NER model %s available", ner.model)
        except (
            DetectorUnavailable,
            OSError,
        ) as exc:  # OSError: network, Hugging Face hub
            if ner.enabled:
                logger.error("NER model not available: %s", exc)
                ok = False
            else:  # optional: only requests that switch it on miss it
                logger.warning(
                    "NER model not available (off by default, requests cannot use it): %s",
                    exc,
                )
    native = cfg.phases.extraction.native_pdf
    if native.engine == "docling":
        try:
            if not models_present(native):
                await asyncio.to_thread(download_docling, native)
            logger.info("docling models available in %s", native.models_dir)
        except DoclingUnavailable as exc:
            logger.error("docling models not available: %s", exc)
            ok = False
    return ok


async def _main(settings: Settings) -> bool:
    async with httpx.AsyncClient() as http:
        return await download_all(settings, http)


def main() -> int:
    settings = get_settings()
    setup_logging(settings)
    return 0 if asyncio.run(_main(settings)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
