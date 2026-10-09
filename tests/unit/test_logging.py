import io
import logging

import pytest

from anonymization_pipeline.logging_setup import log_event, setup_logging
from tests.conftest import FAKE_CF, make_settings


def test_log_event_format(log_stream: io.StringIO) -> None:
    log_event(logging.getLogger("svc"), "thing_done", {"count": 2, "kind": "a"})

    line = log_stream.getvalue().strip()
    assert line.endswith('INFO svc [-]: thing_done {"count": 2, "kind": "a"}')


def test_log_event_rejects_bad_names() -> None:
    with pytest.raises(ValueError):
        log_event(logging.getLogger("svc"), "Bad Name", {})


def test_setup_logging_is_idempotent_and_keeps_foreign_handlers() -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        settings = make_settings()
        setup_logging(settings, stream=io.StringIO())
        setup_logging(settings, stream=io.StringIO())

        ours = [
            h
            for h in root.handlers
            if getattr(h, "_anonymization_pipeline_handler", False)
        ]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_uvicorn_logs_go_through_redaction(log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("boom for %s", FAKE_CF)

    assert FAKE_CF not in log_stream.getvalue()
    assert "R***U" in log_stream.getvalue()
