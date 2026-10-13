"""Web API of the validation against a fake anonymization service."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_run import FakeService, _config, _dataset

from anonval.api import ApiSettings, create_app


class SlowService(FakeService):
    """Jobs stay running until ``release`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.release = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if (
            request.method == "GET"
            and request.url.path == "/jobs/j"
            and not self.release
        ):
            return httpx.Response(
                200, json={"id": "j", "status": "running", "phase": "detection"}
            )
        return super().__call__(request)


def _app(tmp_path: Path, service: FakeService, profiles: str = "[{nome: base}]") -> Any:
    _dataset(tmp_path / "dataset")
    config = _config(tmp_path, profiles)
    settings = ApiSettings(
        api_url="http://svc",
        key="",
        config_file=str(config),
        results_dir=str(tmp_path / "out"),
    )
    return create_app(settings, httpx.MockTransport(service))


async def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://v"
    )


async def _wait(client: httpx.AsyncClient, run_id: str, state: str) -> dict[str, Any]:
    for _ in range(500):
        body = (await client.get(f"/runs/{run_id}")).json()
        if body["stato"] == state:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {state}: {body}")


async def _default_config(client: httpx.AsyncClient) -> dict[str, Any]:
    return (await client.get("/config")).json()["config"]


async def test_health_and_config(tmp_path: Path) -> None:
    async with await _client(_app(tmp_path, FakeService())) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        body = (await client.get("/config")).json()
    assert [p["nome"] for p in body["config"]["profili"]] == ["base"]
    assert body["documents"][0]["id"] == "referto_esterno_facile_01"
    assert body["key_available"] is False


async def test_run_lifecycle(tmp_path: Path) -> None:
    async with await _client(_app(tmp_path, FakeService())) as client:
        config = await _default_config(client)
        config["soglie"]["tasso_fuga_max"] = 0.6
        response = await client.post("/runs", json={"config": config})
        assert response.status_code == 202
        run_id = response.json()["id"]
        body = await _wait(client, run_id, "completata")
        assert body["profili"][0]["nome"] == "base"
        assert body["profili"][0]["esito"] is True
        assert body["live"]["completed"] == 1
        assert body["live"]["finished"][0]["document"] == "referto_esterno_facile_01"
        report = await client.get(f"/runs/{run_id}/report")
        assert "Ugo Neri" in report.text
        used = (await client.get(f"/runs/{run_id}/config")).json()
        assert used["soglie"]["tasso_fuga_max"] == 0.6
        metrics = (await client.get(f"/runs/{run_id}/metrics")).json()
        assert metrics["profili"][0]["nome"] == "base"
        runs = (await client.get("/runs")).json()
    assert runs[0]["id"] == run_id
    assert runs[0]["stato"] == "completata"
    assert runs[0]["esiti"] == {"base": True}


async def test_one_run_at_a_time_and_cancel(tmp_path: Path) -> None:
    service = SlowService()
    async with await _client(_app(tmp_path, service)) as client:
        config = await _default_config(client)
        run_id = (await client.post("/runs", json={"config": config})).json()["id"]
        for _ in range(500):
            live = (await client.get(f"/runs/{run_id}")).json()["live"]
            if live["running"] and live["running"][0]["phase"]:
                break
            await asyncio.sleep(0.01)
        assert live["running"][0]["phase"] == "detection"
        assert (await client.post("/runs", json={"config": config})).status_code == 409
        assert (await client.post(f"/runs/{run_id}/cancel")).status_code == 202
        await _wait(client, run_id, "annullata")
        assert "run annullata" in (await client.get(f"/runs/{run_id}/report")).text

        # resume completes the cancelled run
        service.release = True
        assert (await client.post(f"/runs/{run_id}/resume", json={})).status_code == 202
        await _wait(client, run_id, "completata")


async def test_invalid_requests(tmp_path: Path) -> None:
    async with await _client(_app(tmp_path, FakeService())) as client:
        config = await _default_config(client)
        bad = {**config, "profili": [{"nome": "x", "mode": "censura"}]}
        assert (await client.post("/runs", json={"config": bad})).status_code == 422
        empty = {**config, "dataset": {**config["dataset"], "tipi": ["test_genetico"]}}
        response = await client.post("/runs", json={"config": empty})
        assert response.status_code == 422
        assert "Nessun documento" in response.json()["detail"]
        pseudonym = {**config, "comune": {**config["comune"], "mode": "pseudonym"}}
        response = await client.post("/runs", json={"config": pseudonym})
        assert response.status_code == 422
        assert "pseudonym" in response.json()["detail"]
        for path in ("/runs/../../etc", "/runs/20990101-000000", "/runs/x/report"):
            assert (await client.get(path)).status_code == 404
        assert (await client.post("/runs/20990101-000000/cancel")).status_code == 404


async def test_key_from_the_request_is_never_saved(tmp_path: Path) -> None:
    async with await _client(_app(tmp_path, FakeService())) as client:
        config = await _default_config(client)
        config["comune"]["mode"] = "pseudonym"
        response = await client.post("/runs", json={"config": config, "key": "segreta"})
        assert response.status_code == 202
        run_id = response.json()["id"]
        await _wait(client, run_id, "completata")
    saved = "".join(
        p.read_text() for p in (tmp_path / "out" / run_id).rglob("*") if p.is_file()
    )
    assert "segreta" not in saved


async def test_unreachable_service(tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _dataset(tmp_path / "dataset")
    settings = ApiSettings(
        api_url="http://svc",
        key="",
        config_file=str(_config(tmp_path, "[{nome: base}]")),
        results_dir=str(tmp_path / "out"),
    )
    app = create_app(settings, httpx.MockTransport(down))
    async with await _client(app) as client:
        config = await _default_config(client)
        response = await client.post("/runs", json={"config": config})
    assert response.status_code == 503
    assert "just up" in response.json()["detail"]


@pytest.mark.parametrize("state", ["in_corso", None])
async def test_interrupted_runs_are_listed(tmp_path: Path, state: str | None) -> None:
    run_dir = tmp_path / "out" / "20260101-101010"
    run_dir.mkdir(parents=True)
    if state:
        (run_dir / "stato.json").write_text(f'{{"stato": "{state}"}}')
    async with await _client(_app(tmp_path, FakeService())) as client:
        runs = (await client.get("/runs")).json()
    assert runs[0]["stato"] == ("interrotta" if state else "sconosciuto")
