from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import anonymization_eval.__main__ as cli
from tests.conftest import make_settings


@pytest.fixture
def no_ocr_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example leaves ANONPIP_OCR_SERVER_URL empty
    monkeypatch.setattr(cli, "get_settings", lambda: make_settings(ocr_server_url=""))


def test_evaluation_uses_the_local_ocr_server_when_none_is_set(
    no_ocr_url: None,
) -> None:
    assert cli.eval_settings(None).ocr_server_url == cli.LOCAL_OCR_SERVER


def test_evaluation_ocr_server_from_option_then_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli, "get_settings", lambda: make_settings(ocr_server_url="http://ocr.env:1")
    )
    assert cli.eval_settings(None).ocr_server_url == "http://ocr.env:1"
    assert cli.eval_settings("http://ocr.opt:2").ocr_server_url == "http://ocr.opt:2"


def test_viewer_runs_evaluations_with_an_ocr_server(
    no_ocr_url: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # the viewer called evaluate() without settings: with the empty URL of .env
    # every run started from the page failed with "no OCR server configured"
    captured: dict[str, Any] = {}

    def create_app(dataset: Path, out: Path, run: Any) -> object:
        captured["run"] = run
        return object()

    def evaluate(dataset: Path, variant: str, out: Path, **kw: Any) -> dict:
        captured["settings"] = kw["settings"]
        return {"variant": variant}

    monkeypatch.setattr(cli.eval_viewer, "create_app", create_app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: None)
    monkeypatch.setattr(cli.evaluation, "evaluate", evaluate)
    monkeypatch.setattr(cli.evaluation, "to_markdown", lambda reports: "")

    result = CliRunner().invoke(cli.app, ["viewer", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    captured["run"]("native")
    assert captured["settings"].ocr_server_url == cli.LOCAL_OCR_SERVER
