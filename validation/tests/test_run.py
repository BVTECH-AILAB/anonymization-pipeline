"""Client, run, metrics and report of the validation, against the real service in process."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from anonval.client import ApiError, ServiceClient
from anonval.metrics import as_dict, summarize, verdict
from anonval.report import num, pct, render_report
from anonval.run import ROOT, load_dataset, main, run_profile
from anonval.scoring import DocScore, ValueOutcome
from anonval.settings import Config, Thresholds, load_config
from anonymization_pipeline.config import Settings
from anonymization_pipeline.main import create_app

DOCS = ["referto_laboratorio_facile_01", "nota_ambulatoriale_media_01"]


@pytest.fixture
def config() -> Config:
    config = load_config(ROOT / "config.yaml")
    config.dataset.documenti = DOCS
    return config


def _settings() -> Settings:
    return Settings.model_validate(
        {"environment": "local", "pipeline_config_file": "", "max_queued_jobs": 10}
    )


def test_profile_runs_against_the_service(config: Config, tmp_path: Path) -> None:
    docs = load_dataset(config)
    with TestClient(create_app(_settings())) as http:
        client = ServiceClient("http://testserver", poll_interval_s=0.02, http=http)
        run = run_profile(client, config, config.profili[0], docs, tmp_path / "raw")

    assert [s.id for s in run.scores] == sorted(DOCS)
    assert all(s.released for s in run.scores)
    assert run.summary.values.expected > 20
    assert run.summary.text_leaks == 0
    raw = json.loads((tmp_path / "raw" / f"{DOCS[0]}.json").read_text())
    assert raw["status"] == "done" and raw["result"]["boxes"]
    report = render_report([run], config)
    assert "## Profilo default" in report and "### Per categoria" in report


def test_main_does_not_start_without_service(config: Config, tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "server: {base_url: 'http://127.0.0.1:9'}\n"
        f"dataset: {{percorso: '{ROOT / 'dataset'}'}}\n"
        "profili: [{nome: default}]\n"
    )

    assert main(["--config", str(path), "--output", str(tmp_path)]) == 2
    assert main(["--config", str(tmp_path / "missing.yaml")]) == 2


def test_dataset_filters(config: Config) -> None:
    config.dataset.documenti = []
    config.dataset.tipi = ["test_genetico"]
    config.dataset.difficolta = ["facile"]

    docs = load_dataset(config)

    assert len(docs) == 4 and {d["type"] for d in docs} == {"test_genetico"}
    config.dataset.documenti = ["inesistente"]
    with pytest.raises(ValueError, match="nessun documento"):
        load_dataset(config)


def _mock(handler: Any) -> ServiceClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://svc")
    return ServiceClient("http://svc", poll_interval_s=0.01, timeout_s=0.05, http=http)


def test_client_retries_a_full_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anonval.client.time.sleep", lambda s: None)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"retry-after": "1"}, json={"detail": "x"}
            )
        return httpx.Response(202, json={"id": "a" * 22, "status": "queued"})

    assert _mock(handler).submit("x.pdf", b"%PDF", {}) == "a" * 22
    assert calls == ["POST", "POST"]


def test_client_cancels_a_job_that_takes_too_long() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"status": "running"})

    with pytest.raises(ApiError, match="tempo massimo"):
        _mock(handler).wait("a" * 22)
    assert seen[-1] == f"POST /anonymizations/{'a' * 22}/cancel"


def test_client_errors_carry_the_service_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Categorie protette"})

    with pytest.raises(ApiError, match="400: Categorie protette"):
        _mock(handler).submit("x.pdf", b"%PDF", {"disable": "CODICE_FISCALE"})


BASE = ValueOutcome(
    value="v",
    category="PERSONA",
    family="PERSONA",
    expected=True,
    occurrences=1,
    unmarked=0,
    coverage=1.0,
    uncovered=0,
    pages=[1],
)


def test_summary_and_verdict() -> None:
    done = DocScore(
        "a",
        "t",
        "facile",
        "done",
        values=[BASE, replace(BASE, coverage=0, uncovered=1)],
    )
    done.correct_boxes = 1
    blocked = DocScore("b", "t", "media", "blocked", reason="ocr_unavailable")
    failed = DocScore("c", "t", "media", "error", error="HTTP 500")

    s = summarize([done, blocked, failed])

    assert (s.released, dict(s.blocked), s.failed) == (1, {"ocr_unavailable": 1}, 1)
    assert (s.recall, s.leak_rate, s.precision) == (0.5, 0.5, 1.0)
    reasons = verdict(s, Thresholds())
    assert (
        "1 documenti non elaborati" in reasons and "recall sotto la soglia" in reasons
    )
    metrics = as_dict(s, Thresholds())
    assert (
        metrics["esito"] == "FAIL"
        and metrics["per_difficolta"]["facile"]["attesi"] == 2
    )


def test_it_numbers() -> None:
    assert num(1234.5, 1) == "1.234,5"
    assert pct(0.9512) == "95,1%"
    assert pct(None) == "-"


def test_profiles_must_name_known_categories(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("profili: [{nome: x, enable: [INVENTATA]}]\n")

    with pytest.raises(ValueError, match="sconosciuta"):
        load_config(path)
