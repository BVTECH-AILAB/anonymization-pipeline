"""Logging of the evaluation tools: plain lines on a stream (stderr by default)."""

import logging
import sys
from typing import TextIO

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Send every log record at ``level`` or above to ``stream``.

    Idempotent: a second call replaces the handler it installed before.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_anonymization_eval_handler", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler._anonymization_eval_handler = True  # ty: ignore[unresolved-attribute]
    root.addHandler(handler)
    root.setLevel(level)
