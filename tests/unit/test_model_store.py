import asyncio
from pathlib import Path

import httpx

from anonymization_pipeline.config import Settings
from anonymization_pipeline.model_store import download_all
from tests.conftest import make_settings


def _run(settings: Settings, handler: httpx.MockTransport) -> bool:
    async def main() -> bool:
        async with httpx.AsyncClient(transport=handler) as http:
            return await download_all(settings, http)

    return asyncio.run(main())


def _ollama(pulled: list[str], fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/pull":
            pulled.append(request.read().decode())
            return httpx.Response(
                500 if fail else 200,
                json={"error": "no space"} if fail else {"status": "success"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_pulls_the_vlm_even_when_runtime_pulls_are_off(tmp_path: Path) -> None:
    folder = tmp_path / "ner"
    folder.mkdir()
    (tmp_path / "ner.yaml").write_text(
        f"phases: {{detection: {{detectors: {{ner: {{model: '{folder}'}}}}}}}}\n"
    )
    settings = make_settings(
        default_extraction="vlm",
        vlm_pull_missing=False,
        pipeline_config_file=str(tmp_path / "ner.yaml"),
    )
    pulled: list[str] = []

    assert _run(settings, _ollama(pulled)) is True
    assert len(pulled) == 1 and "gemma4:26b-a4b-it-qat" in pulled[0]


def test_reports_every_missing_model(tmp_path: Path) -> None:
    (tmp_path / "ner.yaml").write_text(
        f"phases: {{detection: {{detectors: {{ner: {{model: '{tmp_path / 'missing'}'}}}}}}}}\n"
    )
    settings = make_settings(
        docling_models_dir=str(tmp_path / "docling"),
        pipeline_config_file=str(tmp_path / "ner.yaml"),
    )

    assert _run(settings, _ollama([], fail=True)) is False
