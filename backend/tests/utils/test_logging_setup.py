import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.utils.logging_setup import log_event, setup_logging
from tests.conftest import FAKE_CF, make_settings
from tests.documents import EMAIL, PATIENT, PHONE, letter_pdf
from tests.sessions import BASE, anonymize, create, edit, run


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

        ours = [h for h in root.handlers if getattr(h, "_src_handler", False)]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_uvicorn_logs_go_through_redaction(log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("boom for %s", FAKE_CF)

    assert FAKE_CF not in log_stream.getvalue()
    assert "R***U" in log_stream.getvalue()


def test_anonymization_never_logs_document_content(
    client: TestClient, log_stream: io.StringIO, tmp_path: Path
) -> None:
    """Upload, edit, download and fail: the logs carry ids and counters, never PII or file names."""
    name = f"referto_{PATIENT.replace(' ', '_')}_{FAKE_CF}.pdf"
    src = letter_pdf(tmp_path / "in.pdf")

    session_id = create(client, src, name=name)["session_id"]
    anonymize(client, session_id)
    edit(client, session_id, {"search": "Garibaldi"})
    client.get(f"{BASE}/{session_id}/download")

    logs = log_stream.getvalue()
    assert "anonymization_job_created" in logs
    assert "anonymization_completed" in logs
    assert "anonymization_edited" in logs
    for secret in (FAKE_CF, PATIENT, "Rossi", PHONE, EMAIL, "Garibaldi", name, "R***U"):
        assert secret not in logs


def test_fast_anonymization_never_logs_document_content(
    client: TestClient, log_stream: io.StringIO, tmp_path: Path
) -> None:
    """Fast mode (Markdown): the logs carry ids and counters, never PII or file names."""
    name = f"referto_{PATIENT.replace(' ', '_')}_{FAKE_CF}.pdf"
    src = letter_pdf(tmp_path / "in.pdf")

    session_id = create(client, src, name=name, mode="fast")["session_id"]
    anonymize(client, session_id)
    client.get(f"{BASE}/{session_id}/download")

    logs = log_stream.getvalue()
    assert "anonymization_completed" in logs
    assert '"mode": "fast"' in logs
    for secret in (FAKE_CF, PATIENT, "Rossi", PHONE, EMAIL, "Garibaldi", name):
        assert secret not in logs


def test_kpi_events_keep_their_keys(
    client: TestClient, log_stream: io.StringIO, tmp_path: Path
) -> None:
    """Event names and keys are a contract with KPI-dashboard."""
    src = letter_pdf(tmp_path / "in.pdf")
    run(client, src, name="a.pdf")

    lines = log_stream.getvalue().splitlines()
    created_event = next(
        line for line in lines if "anonymization_job_created {" in line
    )
    completed = next(line for line in lines if "anonymization_completed {" in line)
    assert set(json.loads(created_event.split("anonymization_job_created ", 1)[1])) == {
        "job_id",
        "pages",
        "size_kb",
        "source_format",
        "mode",
    }
    assert set(json.loads(completed.split("anonymization_completed ", 1)[1])) == {
        "job_id",
        "pages",
        "scanned_pages",
        "boxes",
        "values_by_label",
        "verified",
        "edits",
        "seconds_total",
        "timings",
        "mode",
    }
