"""A whole job through the service, on the open PDF stack."""

import sys
import time
from pathlib import Path

from anonymization_core.pipeline import Pipeline
from anonymization_core.testing import FakeNER, FakeReader, letter_pdf, scanned_pdf
from fastapi.testclient import TestClient

from anonymization_pipeline.main import create_app
from anonymization_pipeline.services.jobs import JobManager, get_job_manager
from tests.conftest import make_settings


def test_a_whole_job_runs_on_the_open_stack(tmp_path: Path) -> None:
    # upload to download and preview, a native letter and a scan, without PyMuPDF
    src = letter_pdf(
        tmp_path / "l.pdf", qr_text="https://x.example.it/r", signature=True
    )
    scan = scanned_pdf(letter_pdf(tmp_path / "n.pdf"), tmp_path / "s.pdf", dpi=150)
    settings = make_settings()
    manager = JobManager(Pipeline(FakeReader(), FakeNER()), settings, tmp_path / "work")
    app = create_app(settings)
    app.dependency_overrides[get_job_manager] = lambda: manager
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for path in (src, scan):
                with path.open("rb") as fh:
                    created = client.post(
                        "/anonymizations",
                        files={"file": (path.name, fh, "application/pdf")},
                    ).json()
                deadline = time.monotonic() + 60
                state = {}
                while time.monotonic() < deadline:
                    state = client.get(f"/anonymizations/{created['id']}").json()
                    if state["status"] in ("done", "error"):
                        break
                    time.sleep(0.05)
                assert state["status"] == "done", state
                for fmt in ("pdf", "docx"):
                    response = client.get(
                        f"/anonymizations/{created['id']}/download/{fmt}"
                    )
                    assert response.status_code == 200
                preview = client.get(
                    f"/anonymizations/{created['id']}/pages/anonymized/0"
                )
                assert preview.status_code == 200
    finally:
        manager.shutdown()
    assert "pymupdf" not in sys.modules and "fitz" not in sys.modules
