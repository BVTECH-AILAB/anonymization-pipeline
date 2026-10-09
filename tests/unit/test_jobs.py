import asyncio
import io
import json
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from anonymization_pipeline.domain import Result
from anonymization_pipeline.errors import UnredactableContentError, VLMUnavailableError
from anonymization_pipeline.pipeline import Progress
from anonymization_pipeline.redaction.vault import generate_key
from anonymization_pipeline.services.jobs import Job, JobRegistry, QueueFull
from anonymization_pipeline.vlm import VLMError
from tests.conftest import FAKE_CF
from tests.pipeline import pipeline_config
from tests.unit.conftest import TEXT, VALUES, FakeBackend

KEY = generate_key()

ClientFactory = Callable[..., TestClient]


def _upload(name: str = "nota.txt") -> dict:
    return {"file": (name, TEXT.encode())}


def _wait(client: TestClient, job_id: str, timeout: float = 20) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.02)
    raise AssertionError("job not finished")


def test_completed_job_json_and_zip(client: TestClient) -> None:
    response = client.post("/jobs", files=_upload(), data={"key": KEY})
    assert response.status_code == 202, response.text

    job = _wait(client, response.json()["id"])

    assert (
        job["status"] == "completed"
        and job["phase"] == "completed"
        and job["duration_s"] is not None
    )
    assert job["vlm_calls"] == 0  # FakeVLM does not go through VLMClient.call
    body = client.get(f"/jobs/{job['id']}/result").json()
    assert body["markdown"] == "Paziente: [PERSONA_1]\nCF: [CODICE_FISCALE_1]\n"
    assert body["metrics"] and body["report"] and body["encrypted_map_b64"]
    assert "transcript" not in body and "occurrences" not in body
    assert not any(value in json.dumps(body["tags"]) for value in VALUES)
    archive = zipfile.ZipFile(
        io.BytesIO(client.get(f"/jobs/{job['id']}/result?format=zip").content)
    )
    assert "nota.metriche.json" in archive.namelist()
    assert job["id"] in [j["id"] for j in client.get("/jobs").json()]
    assert client.delete(f"/jobs/{job['id']}").status_code == 204
    assert client.get(f"/jobs/{job['id']}").status_code == 404
    assert client.delete(f"/jobs/{job['id']}").status_code == 404


def test_job_with_transcript(make_client: ClientFactory) -> None:
    client = make_client(allow_transcript=True)
    response = client.post(
        "/jobs", files=_upload(), data={"mode": "tag", "transcript": "true"}
    )

    job = _wait(client, response.json()["id"])

    body = client.get(f"/jobs/{job['id']}/result").json()
    assert body["transcript"] == TEXT
    assert {o["original"] for o in body["occurrences"]} == set(VALUES)


def test_transcript_not_allowed_by_default(client: TestClient) -> None:
    response = client.post(
        "/jobs", files=_upload(), data={"mode": "tag", "transcript": "true"}
    )

    assert response.status_code == 400


def test_rejected_job_and_missing_key(
    backend: FakeBackend, make_client: ClientFactory, tmp_path: Path
) -> None:
    client = make_client()
    assert (
        client.post("/jobs", files=_upload()).status_code == 400
    )  # pseudonym without key: at once

    path = tmp_path / "vlm_only.yaml"
    path.write_text(
        "phases: {detection: {detectors: {rules: false, labels: false, ner: {enabled: false}}}}\n"
    )
    client = make_client(pipeline_config_file=str(path))
    backend.model.missed = set(VALUES)  # detection misses them, verification sees them
    response = client.post(
        "/jobs", files=_upload(), data={"mode": "tag", "verification": "block"}
    )

    job = _wait(client, response.json()["id"])

    assert (
        job["status"] == "rejected"
        and job["reason"] == "residuals"
        and job["residuals"]
    )
    result = client.get(f"/jobs/{job['id']}/result")
    assert result.status_code == 422 and FAKE_CF not in result.text


def test_job_in_error(backend: FakeBackend, client: TestClient) -> None:
    backend.model.errors = {"detection": VLMError("down")}
    response = client.post("/jobs", files=_upload(), data={"mode": "tag"})

    job = _wait(client, response.json()["id"])

    assert job["status"] == "error" and "non è disponibile" in job["error"]
    assert client.get(f"/jobs/{job['id']}/result").status_code == 409


def test_full_queue_answers_503(make_client: ClientFactory) -> None:
    client = make_client(max_jobs=1)
    client.app.state.anonymization._slots._value = 0

    assert (
        client.post("/jobs", files=_upload(), data={"mode": "tag"}).status_code == 202
    )
    response = client.post("/jobs", files=_upload(), data={"mode": "tag"})

    assert response.status_code == 503 and response.headers["retry-after"] == "30"


def test_jobs_never_log_document_content(
    client: TestClient, log_stream: io.StringIO
) -> None:
    response = client.post("/jobs", files=_upload("Rossi_Mario.txt"), data={"key": KEY})
    _wait(client, response.json()["id"])

    logs = log_stream.getvalue()
    assert "job_queued" in logs and "job_finished" in logs
    for value in [*VALUES, "Rossi_Mario", KEY]:
        assert value not in logs


async def test_queue_progress_cancellation_and_expiry() -> None:
    release = asyncio.Event()

    async def run(job: Job, progress: Progress) -> Result:
        progress("detection", {"metrics": None, "iteration": 1})
        await release.wait()
        if job.name == "reject":
            raise UnredactableContentError(
                extra={
                    "reason": "residuals",
                    "residuals": [{"category": "PERSONA", "page": 1}],
                }
            )
        if job.name == "fail":
            raise VLMUnavailableError()
        return Result(name=job.name, markdown="", report={})

    registry = JobRegistry(run, workers=1, max_jobs=10, max_queued=3, ttl_s=60)
    await registry.start()
    cfg = pipeline_config()
    try:
        first = registry.add("a", b"x", cfg, None)
        for _ in range(100):  # the first one runs, stopped on `release`
            if first.status == "running" and first.phase == "detection":
                break
            await asyncio.sleep(0.01)
        second = registry.add("b", b"x", cfg, None)
        third = registry.add("reject", b"x", cfg, None)
        fourth = registry.add("fail", b"x", cfg, None)
        assert registry.position(second) == 1 and registry.position(third) == 2
        with pytest.raises(QueueFull):
            registry.add("d", b"x", cfg, None)
        assert (
            registry.delete(second.id) and second.status == "cancelled"
        )  # queued: it never runs
        release.set()
        for _ in range(200):
            if fourth.status not in ("queued", "running"):
                break
            await asyncio.sleep(0.01)
        assert (
            first.status == "completed" and first.data is None and first.iteration == 1
        )
        assert third.status == "rejected" and third.residuals == [
            {"category": "PERSONA", "page": 1}
        ]
        assert fourth.status == "error" and fourth.error
        assert second.finished is None  # never run
        assert [j.name for j in registry.list()] == ["a", "reject", "fail"]
        third.expires = time.time() - 1
        assert registry.get(third.id) is None  # expired
    finally:
        await registry.stop()
