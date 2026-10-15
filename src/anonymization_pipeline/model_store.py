"""Download of the models: the only step that needs the network.

Usage: ``python -m anonymization_pipeline.model_store [MODEL ...]`` (``just models``). Without
arguments it downloads the NER model of the service configuration into the Hugging Face cache
(``HF_HOME``) and the OCR weights into the docTR cache (``DOCTR_CACHE_DIR``); the service then
loads them without network (``HF_HUB_OFFLINE=1``).
"""

import argparse
import logging
import sys

from anonymization_pipeline import ocr
from anonymization_pipeline.config import get_settings
from anonymization_pipeline.detectors.ner import DetectorUnavailable, download
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.pipeline_config import load_pipeline_config

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Download the given models (Hugging Face names), or the configured NER model.

    Returns:
        0 on success, 1 when a model cannot be downloaded.
    """
    parser = argparse.ArgumentParser(
        description="Download the NER models into the cache."
    )
    parser.add_argument("models", nargs="*", help="Hugging Face model names")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings)
    configured = load_pipeline_config(settings).detection.ner
    models = args.models or [configured.model]
    failed = 0
    if not args.models:
        try:
            ocr.load_engine(load_pipeline_config(settings).ocr)
        except DetectorUnavailable as exc:
            logger.error("OCR weights not downloaded: %s", exc)
            failed += 1
        else:
            logger.info("OCR weights ready in the cache")
    for model in models:
        try:
            download(configured.model_copy(update={"model": model, "revision": None}))
        except DetectorUnavailable as exc:
            logger.error("Model not downloaded: %s", exc)
            failed += 1
        else:
            logger.info("Model ready in the cache: %s", model)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
