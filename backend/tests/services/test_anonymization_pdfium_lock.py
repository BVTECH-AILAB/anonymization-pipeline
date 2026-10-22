"""PDFium is not thread-safe: the job manager uses it only under PDFIUM_LOCK.

Two threads inside PDFium at once crash the process (segmentation fault): the
worker analyses a document while request threads validate uploads and render
previews.
"""

import io
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from src.services.anonymization import Job, JobManager
from tests.documents import record_unlocked_pdfium


def _wait(job: Job, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while job.status in ("queued", "running") and time.monotonic() < end:
        time.sleep(0.05)
    assert job.status == "done", job.error_code


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def lock_checked(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return record_unlocked_pdfium(monkeypatch)


def test_service_uses_pdfium_only_under_the_lock(
    letter: Path, jobs: JobManager, lock_checked: list[str]
) -> None:
    job = jobs.start(jobs.create(letter.read_bytes(), "lettera.pdf").id)  # validation
    _wait(job)  # analysis, redaction, verification
    jobs.render_page(job.id, "original", 0, 80)
    jobs.render_page(job.id, "anonymized", 0, 80)
    photo = jobs.start(jobs.create(_png(), "foto.png").id)  # image -> PDF
    _wait(photo)
    fast = jobs.start(jobs.create(letter.read_bytes(), "lettera.pdf", "fast").id)
    _wait(fast)
    jobs.render_page(fast.id, "original", 0, 80)
    assert lock_checked == []


def test_previews_and_uploads_while_the_worker_runs(
    letter: Path, jobs: JobManager
) -> None:
    # the scenario that crashed the service in a few seconds
    done = jobs.start(jobs.create(letter.read_bytes(), "a.pdf").id)
    _wait(done)
    data = letter.read_bytes()
    errors: list[BaseException] = []
    stop = time.monotonic() + 3

    def previews(n: int) -> None:
        try:
            while time.monotonic() < stop:
                for which in ("original", "anonymized"):
                    for dpi in range(60 + n, 140, 7):
                        jobs.render_page(done.id, which, 0, dpi)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=previews, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    queued = [jobs.start(jobs.create(data, f"{i}.pdf").id) for i in range(3)]
    for t in threads:
        t.join()
    for job in queued:
        _wait(job)
    assert errors == []
