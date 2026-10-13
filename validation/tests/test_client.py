"""Job client against a fake service (httpx.MockTransport)."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from anonval.client import ApiError, ServiceClient, run_job


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> ServiceClient:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://svc"
    )
    return ServiceClient(http)


class FakeService:
    """POST /jobs, then ``statuses`` one per GET /jobs/{id}."""

    def __init__(self, statuses: list[dict[str, Any]], busy: int = 0) -> None:
        self.statuses = statuses
        self.busy = busy
        self.requests: list[httpx.Request] = []
        self.deleted = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST":
            if self.busy:
                self.busy -= 1
                return httpx.Response(
                    503, json={"code": "SERVER_BUSY", "detail": "busy"}
                )
            return httpx.Response(202, json={"id": "j1", "status": "queued"})
        if request.method == "DELETE":
            self.deleted = True
            return httpx.Response(204)
        if path == "/jobs/j1/result":
            return httpx.Response(
                200,
                json={
                    "markdown": "[PERSONA]",
                    "transcript": "Mario",
                    "layout_pdf_b64": "x",
                    "tags": [],
                },
            )
        return httpx.Response(200, json=self.statuses.pop(0))


async def test_completed_job_returns_result_without_heavy_fields() -> None:
    service = FakeService(
        [
            {"status": "running", "phase": "rilevazione"},
            {"status": "completed", "duration_s": 3.2},
        ]
    )
    seen: list[str] = []
    out = await run_job(
        _client(service),
        "doc.pdf",
        b"%PDF-1.7",
        {"mode": "tag"},
        "segreto",
        0.01,
        60,
        on_status=lambda s: seen.append(s["status"]),
        sleep=_no_sleep,
    )
    assert out["status"] == "completed"
    assert out["duration_s"] == 3.2
    assert out["result"] == {
        "markdown": "[PERSONA]",
        "transcript": "Mario",
        "tags": [],
        "layout_error": "PDF con layout non leggibile",
    }
    assert seen == ["running", "completed"]
    assert service.deleted
    post = service.requests[0]
    assert post.headers["X-Anon-Key"] == "segreto"
    body = post.content.decode(errors="ignore")
    assert 'name="transcript"' in body and 'name="mode"' in body


async def test_rejected_job_keeps_reason() -> None:
    service = FakeService(
        [
            {
                "status": "rejected",
                "reason": "residuals",
                "residuals": [{"category": "MEDICO", "page": 1}],
            }
        ]
    )
    out = await run_job(
        _client(service), "d.pdf", b"x", {}, None, 0.01, 60, sleep=_no_sleep
    )
    assert out["status"] == "rejected"
    assert out["reason"] == "residuals"
    assert "result" not in out


async def test_busy_server_is_retried() -> None:
    service = FakeService([{"status": "completed"}], busy=2)
    out = await run_job(
        _client(service), "d.pdf", b"x", {}, None, 0.01, 60, sleep=_no_sleep
    )
    assert out["status"] == "completed"


async def test_refused_request_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "code": "BAD_REQUEST",
                "detail": "Trascrizione in chiaro non consentita in questo ambiente.",
            },
        )

    with pytest.raises(ApiError) as info:
        await run_job(
            _client(handler), "d.pdf", b"x", {}, None, 0.01, 60, sleep=_no_sleep
        )
    assert info.value.code == "BAD_REQUEST"
    assert "Trascrizione" in info.value.detail


async def test_timeout_deletes_the_job() -> None:
    service = FakeService([{"status": "running"}] * 5)
    out = await run_job(
        _client(service), "d.pdf", b"x", {}, None, 0.01, 0, sleep=_no_sleep
    )
    assert out["status"] == "timeout"
    assert service.deleted
