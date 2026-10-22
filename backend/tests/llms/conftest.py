"""Model client tests: every log record, unfiltered (``raw_log_stream``).

No redaction filter runs here: a value that reaches this stream would reach
any caller's logs, whatever handler it installs.
"""

import io

import pytest


@pytest.fixture
def log_stream(raw_log_stream: io.StringIO) -> io.StringIO:
    return raw_log_stream
