import io
import json
import threading
import time
from collections.abc import Callable, Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from anonymization_pipeline.config import Settings
from anonymization_pipeline.main import create_app
from anonymization_pipeline.middleware import UploadLimitMiddleware
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.services.anonymization import Anonymized
from anonymization_pipeline.services.jobs import JobRegistry
from tests.conftest import FAKE_CF, make_settings
from tests.documents import PATIENT, letter_pdf, trapped_letter


def _client(
    settings: Settings, run: Callable[..., Anonymized] | None = None
) -> TestClient:
    app = create_app(settings)
    if run is not None:
        app.state.jobs = JobRegistry(settings, run=run)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with _client(settings) as c:
        yield c


def _upload(client: TestClient, data: bytes, **form: str) -> Any:
    return client.post(
        "/anonymizations", files={"file": ("referto_rossi.pdf", data)}, data=form
    )


def _wait(client: TestClient, job_id: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/anonymizations/{job_id}").json()
        if body["status"] in ("done", "blocked", "failed", "cancelled"):
            return body
        time.sleep(0.02)
    raise AssertionError("job not finished")


class Gate:
    """A runner that waits until the test opens the gate."""

    def __init__(self) -> None:
        self.open = threading.Event()
        self.started = threading.Event()

    def __call__(
        self, data: bytes, cfg: PipelineConfig, progress: Callable[[str], None]
    ) -> Anonymized:
        self.started.set()
        self.open.wait(10)
        progress("detection")  # raises when the job was cancelled
        return Anonymized("done", None, 1, "pdf", pdf=b"%PDF-1.7 test")


def test_letter_is_anonymized_and_downloaded(
    client: TestClient, tmp_path: Path
) -> None:
    created = _upload(client, trapped_letter(tmp_path / "in.pdf").read_bytes())
    assert created.status_code == 202

    body = _wait(client, created.json()["id"])

    assert body["status"] == "done"
    assert body["result"]["counts"]["CODICE_FISCALE"] == 1
    assert body["result"]["redacted_pages"] == [1]
    cf_box = next(
        b for b in body["result"]["boxes"] if b["category"] == "CODICE_FISCALE"
    )
    assert cf_box["page"] == 1 and len(cf_box["rect"]) == 4
    assert body["result"]["failures"] == []
    response = client.get(f"/anonymizations/{body['id']}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "documento_anonimizzato.pdf" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert FAKE_CF.encode() not in response.content


def test_status_never_holds_values(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client, letter_pdf(tmp_path / "in.pdf").read_bytes()).json()["id"]

    text = json.dumps(_wait(client, job_id))

    for value in (FAKE_CF, PATIENT, "Laura Bianchi", "referto_rossi"):
        assert value not in text


def test_events_follow_the_job_until_it_is_finished(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _upload(client, letter_pdf(tmp_path / "in.pdf").read_bytes()).json()["id"]

    with client.stream("GET", f"/anonymizations/{job_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert events[-1]["status"] == "done"
    assert len({e["status"] for e in events}) >= 1


def test_scanned_document_is_blocked_and_not_downloadable(client: TestClient) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (800, 1100), "white").save(buf, "PNG")

    body = _wait(client, _upload(client, buf.getvalue()).json()["id"])

    assert (body["status"], body["reason"]) == ("blocked", "ocr_unavailable")
    assert body["result"]["scanned_pages"] == [1]
    response = client.get(f"/anonymizations/{body['id']}/download")
    assert (response.status_code, response.json()["code"]) == (409, "DOCUMENT_BLOCKED")


def test_download_before_the_end_is_refused(settings: Settings) -> None:
    gate = Gate()
    with _client(settings, gate) as client:
        job_id = _upload(client, b"%PDF-1.7").json()["id"]
        gate.started.wait(5)

        response = client.get(f"/anonymizations/{job_id}/download")
        gate.open.set()

    assert (response.status_code, response.json()["code"]) == (409, "JOB_NOT_READY")


def test_running_job_is_cancelled_and_a_finished_one_is_left_alone(
    settings: Settings,
) -> None:
    gate = Gate()
    with _client(settings, gate) as client:
        running = _upload(client, b"%PDF-1.7").json()["id"]
        gate.started.wait(5)
        assert client.post(f"/anonymizations/{running}/cancel").json() == {
            "cancelled": True
        }
        gate.open.set()
        assert _wait(client, running)["status"] == "cancelled"

        done = _upload(client, b"%PDF-1.7").json()["id"]
        assert _wait(client, done)["status"] == "done"
        assert client.post(f"/anonymizations/{done}/cancel").json() == {
            "cancelled": False
        }
        assert client.get(f"/anonymizations/{done}/download").status_code == 200


def test_queued_job_is_cancelled_before_it_runs(settings: Settings) -> None:
    gate = Gate()
    with _client(settings, gate) as client:
        first = _upload(client, b"%PDF-1.7").json()["id"]
        gate.started.wait(5)
        second = _upload(client, b"%PDF-1.7").json()["id"]

        client.post(f"/anonymizations/{second}/cancel")
        gate.open.set()

        assert _wait(client, first)["status"] == "done"
        assert _wait(client, second)["status"] == "cancelled"


def test_delete_removes_the_job(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client, letter_pdf(tmp_path / "in.pdf").read_bytes()).json()["id"]
    _wait(client, job_id)

    assert client.delete(f"/anonymizations/{job_id}").status_code == 204
    assert client.get(f"/anonymizations/{job_id}").status_code == 404
    assert client.delete(f"/anonymizations/{job_id}").status_code == 204  # idempotent


def test_unknown_and_malformed_ids(client: TestClient) -> None:
    unknown = client.get("/anonymizations/" + "a" * 22)

    assert (unknown.status_code, unknown.json()["detail"]) == (
        404,
        "Risorsa non trovata.",
    )
    assert client.get("/anonymizations/not-an-id").status_code == 422


def test_unsupported_file_is_rejected_at_upload(client: TestClient) -> None:
    response = _upload(client, b"PK\x03\x04 a docx")

    assert (response.status_code, response.json()["code"]) == (422, "INVALID_DOCUMENT")
    assert "non supportato" in response.json()["detail"]


def test_invalid_pdf_makes_the_job_fail_with_a_generic_message(
    client: TestClient,
) -> None:
    body = _wait(client, _upload(client, b"%PDF-1.7 garbage").json()["id"])

    assert body["status"] == "failed"
    assert body["error"] == "PDF non leggibile o danneggiato"


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"disable": "CODICE_FISCALE"}, "protette"),
        ({"enable": "INVENTATA"}, "sconosciuta"),
    ],
)
def test_category_choices_are_checked(
    client: TestClient, tmp_path: Path, form: dict[str, str], message: str
) -> None:
    response = _upload(client, letter_pdf(tmp_path / "in.pdf").read_bytes(), **form)

    assert (response.status_code, response.json()["code"]) == (400, "BAD_REQUEST")
    assert message in response.json()["detail"]


def test_categories_can_be_switched_on_per_request(
    client: TestClient, tmp_path: Path
) -> None:
    data = letter_pdf(tmp_path / "in.pdf").read_bytes()

    off = _wait(client, _upload(client, data).json()["id"])["result"]["counts"]
    on = _wait(client, _upload(client, data, enable="date").json()["id"])["result"][
        "counts"
    ]

    assert "DATA" not in off and "DATA_RICOVERO" in on


def test_too_large_upload_is_refused_before_reading(settings: Settings) -> None:
    with _client(make_settings(max_upload_mb=0.01)) as client:
        response = _upload(client, b"%PDF-1.7" + b"0" * 400_000)

    assert (response.status_code, response.json()["code"]) == (413, "PAYLOAD_TOO_LARGE")


async def test_body_without_length_is_refused() -> None:
    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise AssertionError("the app must not run")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    scope = {"type": "http", "method": "POST", "headers": [], "path": "/anonymizations"}
    await UploadLimitMiddleware(app, max_bytes=10)(scope, receive, send)

    assert sent[0]["status"] == 411


def test_too_many_waiting_jobs(settings: Settings) -> None:
    gate = Gate()
    with _client(make_settings(max_queued_jobs=1), gate) as client:
        _upload(client, b"%PDF-1.7")
        gate.started.wait(5)

        response = _upload(client, b"%PDF-1.7")
        gate.open.set()

    assert (response.status_code, response.json()["code"]) == (429, "TOO_MANY_JOBS")
    assert response.headers["retry-after"] == "30"


def test_finished_jobs_never_block_uploads_and_the_oldest_go_first(
    tmp_path: Path,
) -> None:
    data = letter_pdf(tmp_path / "in.pdf").read_bytes()
    with _client(make_settings(max_queued_jobs=1, max_kept_jobs=1)) as client:
        first = _upload(client, data).json()["id"]
        _wait(client, first)
        second = _upload(client, data)
        assert second.status_code == 202
        _wait(client, second.json()["id"])

        assert client.get(f"/anonymizations/{first}").status_code == 404


def test_expired_jobs_are_deleted(client: TestClient, tmp_path: Path) -> None:
    job_id = _upload(client, letter_pdf(tmp_path / "in.pdf").read_bytes()).json()["id"]
    _wait(client, job_id)
    jobs: JobRegistry = client.app.state.jobs  # type: ignore[attr-defined]
    job = jobs.get(job_id)
    assert job is not None
    job.expires = time.time() - 1

    assert client.get(f"/anonymizations/{job_id}").status_code == 404


def test_catalog_lists_categories_with_defaults(client: TestClient) -> None:
    categories = {c["category"]: c for c in client.get("/categories").json()}

    assert categories["CODICE_FISCALE"]["protected"] is True
    assert categories["MEDICO"]["generic"] == "PERSONA"
    assert categories["DIAGNOSI"]["protected"] is False


def _events(log: str) -> dict[str, dict[str, Any]]:
    out = {}
    for line in log.splitlines():
        message = line.split(": ", 1)[-1]
        name, _, payload = message.partition(" ")
        if payload.startswith("{"):
            out[name] = json.loads(payload)
    return out


def test_logs_hold_events_and_never_values(
    client: TestClient, tmp_path: Path, log_stream: io.StringIO
) -> None:
    job_id = _upload(client, trapped_letter(tmp_path / "in.pdf").read_bytes()).json()[
        "id"
    ]
    _wait(client, job_id)
    client.get(f"/anonymizations/{job_id}/download")
    client.delete(f"/anonymizations/{job_id}")

    log = log_stream.getvalue()
    for value in (FAKE_CF, PATIENT, "Laura Bianchi", "referto_rossi", job_id):
        assert value not in log
    events = _events(log)
    assert set(events["job_queued"]) == {"job", "size_kb"}
    assert set(events["job_finished"]) == {"job", "status", "duration_s"}
    assert set(events["document_anonymized"]) == {
        "job",
        "kind",
        "pages",
        "redacted_pages",
        "boxes",
        "counts",
        "total_s",
    }
    assert set(events["job_deleted"]) == {"job", "reason"}
    assert events["job_queued"]["job"] == job_id[:8]


def test_rejected_and_cancelled_events_keep_their_keys(
    settings: Settings, log_stream: io.StringIO
) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (800, 1100), "white").save(buf, "PNG")
    with _client(settings) as client:
        _wait(client, _upload(client, buf.getvalue()).json()["id"])
    gate = Gate()
    with _client(settings, gate) as client:
        job_id = _upload(client, b"%PDF-1.7").json()["id"]
        gate.started.wait(5)
        client.post(f"/anonymizations/{job_id}/cancel")
        gate.open.set()
        _wait(client, job_id)

    events = _events(log_stream.getvalue())
    assert set(events["document_rejected"]) == {
        "job",
        "reason",
        "checks",
        "scanned_pages",
    }
    assert set(events["job_cancelled"]) == {"job"}
