"""HTTP client of the anonymization service for the validation: catalog and asynchronous jobs."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

BUSY_RETRIES = 30
# heavy result fields the scoring does not need
DROPPED = ("encrypted_map_b64", "layout_pdf_b64", "structure_b64")
FINAL = {"completed", "rejected", "error", "cancelled"}


class ApiError(Exception):
    """Error response of the service (``{code, detail, ...}``)."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.code = str(body.get("code", status))
        self.detail = str(body.get("detail", ""))
        self.body = body
        super().__init__(f"HTTP {status} {self.code}: {self.detail}")


class ServiceClient:
    """Thin wrapper over ``httpx.AsyncClient`` pointed at the service."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def _json(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {"detail": response.text[:200]}
            raise ApiError(response.status_code, body if isinstance(body, dict) else {})
        return response.json() if response.content else None

    async def get(self, path: str) -> Any:
        return await self._json(await self.http.get(path))

    async def submit(
        self, name: str, data: bytes, form: dict[str, str], key: str | None
    ) -> dict[str, Any]:
        headers = {"X-Anon-Key": key} if key else {}
        response = await self.http.post(
            "/jobs",
            files={"file": (name, data, "application/pdf")},
            data={**form, "transcript": "true"},
            headers=headers,
        )
        return await self._json(response)

    async def delete(self, job_id: str) -> None:
        response = await self.http.delete(f"/jobs/{job_id}")
        if response.status_code not in (204, 404):
            await self._json(response)


async def run_job(
    client: ServiceClient,
    name: str,
    data: bytes,
    form: dict[str, str],
    key: str | None,
    poll_s: float,
    timeout_s: float,
    on_status: Callable[[dict[str, Any]], None] = lambda _status: None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Queue a document, follow it to the end and return what the scoring needs.

    Returns:
        ``{status, duration_s, error, reason, residuals, result}``; ``result`` (completed only) is
        the JSON result without the heavy binary fields.

    Raises:
        ApiError: The request is refused (invalid options, transcript not allowed, missing key):
            the same error would hit every document of the profile.
    """
    for attempt in range(BUSY_RETRIES + 1):
        try:
            job = await client.submit(name, data, form, key)
            break
        except ApiError as exc:
            if exc.code != "SERVER_BUSY" or attempt == BUSY_RETRIES:
                raise
            on_status({"status": "busy", "phase": "coda del server piena"})
            await sleep(max(poll_s, 5))
    job_id = job["id"]
    started = time.monotonic()
    try:
        while job["status"] not in FINAL:
            if time.monotonic() - started > timeout_s:
                return {
                    "status": "timeout",
                    "error": f"oltre {timeout_s:.0f} s",
                    "duration_s": None,
                }
            await sleep(poll_s)
            job = await client.get(f"/jobs/{job_id}")
            on_status(job)
        outcome: dict[str, Any] = {
            "status": job["status"],
            "duration_s": job.get("duration_s"),
            "error": job.get("error"),
            "reason": job.get("reason"),
            "residuals": job.get("residuals"),
        }
        if job["status"] == "completed":
            result = await client.get(f"/jobs/{job_id}/result?format=json")
            outcome["result"] = {k: v for k, v in result.items() if k not in DROPPED}
        return outcome
    finally:
        try:
            await client.delete(job_id)
        except (ApiError, httpx.HTTPError):
            pass
