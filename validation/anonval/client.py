"""Client of the anonymization API (``/anonymizations``): submit, follow, read the outcome."""

import time
from dataclasses import dataclass
from typing import Any

import httpx

TERMINAL = {"done", "blocked", "failed", "cancelled"}
BUSY_RETRIES = 30


class ApiError(RuntimeError):
    """The service refused a request or did not answer."""


@dataclass
class JobResult:
    """Final status of a job, and the redacted PDF when it was released."""

    status: dict[str, Any]
    pdf: bytes | None


class ServiceClient:
    """Synchronous client of one service.

    Args:
        base_url: Root of the service (``http://localhost:8000``).
        poll_interval_s: Seconds between two status requests.
        timeout_s: Seconds a document may take before it is cancelled.
        http: An ``httpx.Client`` (tests pass one bound to the app).
    """

    def __init__(
        self,
        base_url: str,
        poll_interval_s: float = 2,
        timeout_s: float = 1800,
        http: httpx.Client | None = None,
    ) -> None:
        self.http = http or httpx.Client(base_url=base_url, timeout=60)
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                detail = response.text[:200]
            raise ApiError(f"HTTP {response.status_code}: {detail}")
        return response

    def ready(self) -> bool:
        try:
            return self.http.get("/readyz").status_code == 200
        except httpx.HTTPError:
            return False

    def categories(self) -> list[dict[str, Any]]:
        """``GET /categories``: defaults and protected categories of the service."""
        return self._check(self.http.get("/categories")).json()

    def submit(self, name: str, pdf: bytes, form: dict[str, str]) -> str:
        """Queue a document, retrying while the service queue is full; returns the job id."""
        for _ in range(BUSY_RETRIES):
            response = self.http.post(
                "/anonymizations",
                files={"file": (name, pdf, "application/pdf")},
                data=form,
            )
            if response.status_code != 429:
                return self._check(response).json()["id"]
            time.sleep(float(response.headers.get("retry-after", "5")))
        raise ApiError("HTTP 429: coda del servizio piena")

    def wait(self, job_id: str) -> dict[str, Any]:
        """Status of the job once finished; cancelled after ``timeout_s``."""
        deadline = time.monotonic() + self.timeout_s
        while True:
            status = self._check(self.http.get(f"/anonymizations/{job_id}")).json()
            if status["status"] in TERMINAL:
                return status
            if time.monotonic() > deadline:
                self.http.post(f"/anonymizations/{job_id}/cancel")
                raise ApiError("tempo massimo per il documento superato")
            time.sleep(self.poll_interval_s)

    def run(self, name: str, pdf: bytes, form: dict[str, str]) -> JobResult:
        """Submit, wait, download the released PDF, delete the job."""
        job_id = self.submit(name, pdf, form)
        try:
            status = self.wait(job_id)
            data = None
            if status["status"] == "done":
                data = self._check(
                    self.http.get(f"/anonymizations/{job_id}/download")
                ).content
            return JobResult(status, data)
        finally:
            self.http.delete(f"/anonymizations/{job_id}")
