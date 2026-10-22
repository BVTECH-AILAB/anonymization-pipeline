"""Shared fixtures of the engine tests.

Test data is synthetic: the only codice fiscale is the textbook example in
``anonymization_core.testing``, never a real person's.
"""

import io
import logging
from collections.abc import Iterator

import pytest


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Everything the engine logs, at every level and unfiltered.

    No redaction filter runs here: a value that reaches this stream would reach
    any caller's logs, whatever handler it installs.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(level)
