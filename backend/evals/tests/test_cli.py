from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import evals.__main__ as cli
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


def _report(variant: str) -> dict[str, Any]:
    return {
        "variant": variant,
        "box": {"recall": 1.0, "precision": 1.0, "f1": 1.0},
        "ap_pii_50": 1.0,
        "map_50": 1.0,
        "verified_documents": "1/1",
        "seconds_per_page": 0.1,
    }


def test_evaluate_scores_every_variant_with_an_ocr_server(
    no_ocr_url: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def evaluate(
        dataset: Path, variant: str, out: Path, limit: int | None, **kw: Any
    ) -> dict:
        calls.append((variant, kw["split"], kw["settings"]))
        out.mkdir(parents=True, exist_ok=True)
        return _report(variant)

    monkeypatch.setattr(cli.evaluation, "evaluate", evaluate)
    monkeypatch.setattr(
        cli.evaluation, "to_markdown", lambda reports: f"{len(reports)} variants"
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "evaluate",
            "--variants",
            "native,scan",
            "--split",
            "test",
            "--out",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert [(v, s) for v, s, _ in calls] == [("native", "test"), ("scan", "test")]
    assert all(st.ocr_server_url == cli.LOCAL_OCR_SERVER for _, _, st in calls)
    assert (tmp_path / "evaluation.md").read_text() == "2 variants"


def test_evaluate_rejects_an_unknown_split(no_ocr_url: None, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["evaluate", "--split", "train", "--out", str(tmp_path)]
    )

    assert result.exit_code != 0
