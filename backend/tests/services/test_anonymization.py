"""A whole job through the service, on the open PDF stack."""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from src.main import create_app
from src.services.anonymization import JobManager
from src.services.anonymizer.pipeline import Pipeline
from tests.conftest import make_settings
from tests.documents import FakeNER, FakeReader, letter_pdf, scanned_pdf
from tests.sessions import BASE, run


def test_a_whole_job_runs_on_the_open_stack(tmp_path: Path) -> None:
    # upload to download and preview, a native letter and a scan, without PyMuPDF
    src = letter_pdf(
        tmp_path / "l.pdf", qr_text="https://x.example.it/r", signature=True
    )
    scan = scanned_pdf(letter_pdf(tmp_path / "n.pdf"), tmp_path / "s.pdf", dpi=150)
    settings = make_settings()
    manager = JobManager(Pipeline(FakeReader(), FakeNER()), settings, tmp_path / "work")
    app = create_app(settings)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.jobs = manager
            for path in (src, scan):
                state = run(client, path)
                assert state["status"] == "done", state
                session = f"{BASE}/{state['session_id']}"
                for fmt in ("pdf", "docx"):
                    response = client.get(f"{session}/download?format={fmt}")
                    assert response.status_code == 200
                assert client.get(f"{session}/preview").status_code == 200
                assert client.get(f"{session}/pages/anonymized/0").status_code == 200
    finally:
        manager.shutdown()
    assert "pymupdf" not in sys.modules and "fitz" not in sys.modules
