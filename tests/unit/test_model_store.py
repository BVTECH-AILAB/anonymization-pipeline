import asyncio
import json
from pathlib import Path

import httpx
import pytest

from anonymization_pipeline import model_store
from anonymization_pipeline.config import Settings
from anonymization_pipeline.model_store import download_all
from tests.conftest import make_settings
from tests.pipeline import models_file


def _run(settings: Settings, handler: httpx.MockTransport) -> bool:
    async def main() -> bool:
        async with httpx.AsyncClient(transport=handler) as http:
            return await download_all(settings, http)

    return asyncio.run(main())


def _ollama(pulled: list[str], fail: set[str] | None = None) -> httpx.MockTransport:
    """Ollama without models: records the pulls, ``fail`` lists the models whose pull fails
    (``None``: every model succeeds)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/pull":
            name = json.loads(request.read())["model"]
            pulled.append(name)
            failed = fail is not None and (not fail or name in fail)
            return httpx.Response(
                500 if failed else 200,
                json={"error": "no space"} if failed else {"status": "success"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _ner_folder(tmp_path: Path) -> str:
    folder = tmp_path / "ner"
    folder.mkdir()
    (tmp_path / "ner.yaml").write_text(
        f"phases: {{detection: {{detectors: {{ner: {{model: '{folder}'}}}}}}}}\n"
    )
    return str(tmp_path / "ner.yaml")


def test_pulls_the_vlm_even_when_runtime_pulls_are_off(tmp_path: Path) -> None:
    settings = make_settings(
        default_extraction="vlm",
        vlm_pull_missing=False,
        pipeline_config_file=_ner_folder(tmp_path),
    )
    pulled: list[str] = []

    assert _run(settings, _ollama(pulled)) is True
    assert pulled == ["gemma4:26b-a4b-it-qat"]


def test_pulls_every_model_of_the_catalog(tmp_path: Path) -> None:
    catalog = {"big:1": 16384, "small:1": 4096, "spare:1": 8192}
    settings = make_settings(
        default_extraction="vlm",
        pipeline_config_file=_ner_folder(tmp_path),
        models_file=models_file(tmp_path, catalog, {"detection": "small:1"}),
    )
    pulled: list[str] = []

    assert _run(settings, _ollama(pulled, fail={"small:1"})) is False
    assert pulled == list(catalog)  # a failure does not stop the others


def test_downloads_ner_also_when_off_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a request may switch NER on: with the 'ner' extra installed it is always downloaded
    (tmp_path / "ner.yaml").write_text(
        "phases: {detection: {detectors: {ner: "
        f"{{enabled: false, model: '{tmp_path / 'missing'}'}}}}}}}}\n"
    )
    settings = make_settings(
        default_extraction="vlm", pipeline_config_file=str(tmp_path / "ner.yaml")
    )

    attempts: list[str] = []
    real = model_store.download_ner
    monkeypatch.setattr(
        model_store, "download_ner", lambda cfg: attempts.append(cfg.model) or real(cfg)
    )

    monkeypatch.setattr(model_store, "NER_INSTALLED", False)
    assert _run(settings, _ollama([])) is True and attempts == []  # nothing to do
    monkeypatch.setattr(model_store, "NER_INSTALLED", True)
    # attempted, but NER is optional when off: a failure does not fail the download
    assert _run(settings, _ollama([])) is True and len(attempts) == 1


def test_reports_every_missing_model(tmp_path: Path) -> None:
    (tmp_path / "ner.yaml").write_text(
        f"phases: {{detection: {{detectors: {{ner: {{model: '{tmp_path / 'missing'}'}}}}}}}}\n"
    )
    settings = make_settings(
        docling_models_dir=str(tmp_path / "docling"),
        pipeline_config_file=str(tmp_path / "ner.yaml"),
    )

    assert _run(settings, _ollama([], fail=set())) is False
