"""Logging of the OCR server: plain lines on stdout, at ``OCRSRV_LOG_LEVEL``."""

import logging
import sys
from typing import TextIO


def setup_logging(level: str, stream: TextIO | None = None) -> None:
    """One handler on the root logger, uvicorn included; OCR text is never logged."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
