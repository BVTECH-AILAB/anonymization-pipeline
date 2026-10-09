from pathlib import Path

import pytest
import yaml

from anonymization_pipeline.pipeline_config import (
    PHASES,
    effective,
    load_pipeline_config,
    registry,
    render,
    request_overrides,
)
from anonymization_pipeline.pipeline_config.loader import base
from tests.conftest import make_settings
from tests.pipeline import pipeline_config


def test_defaults_are_valid_with_a_model_per_phase() -> None:
    cfg = pipeline_config()

    assert cfg.backend.kind == "ollama"
    assert cfg.backend.api_key is None
    assert cfg.generation.think is False
    for phase in PHASES:
        name, spec = cfg.model_for(phase)
        assert name in cfg.models and spec.context >= 1024


def _models_file(tmp_path: Path, data: dict) -> str:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    return str(path)


TWO_MODELS = {
    "models": [
        {"name": "big:1", "context": 16384},
        {"name": "small:1", "context": 4096},
    ],
    "phases": {
        "extraction": "big:1",
        "detection": "small:1",
        "redaction": "small:1",
        "verification": "big:1",
    },
}


def test_models_file_sets_the_catalog_and_the_model_of_each_phase(
    tmp_path: Path,
) -> None:
    cfg = pipeline_config(models_file=_models_file(tmp_path, TWO_MODELS))

    assert list(cfg.models) == ["big:1", "small:1"]
    assert cfg.model_for("extraction") == ("big:1", cfg.models["big:1"])
    assert cfg.model_for("detection")[1].context == 4096


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"phases": {**TWO_MODELS["phases"], "detection": "absent:1"}}, "absent:1"),
        ({"phases": {"extraction": "big:1"}}, "detection"),
        ({"other": 1}, "other"),
        ({"models": [{"name": "big:1", "context": 8192}] * 2}, "twice"),
        ({"models": [{"name": "big:1", "context": 100}]}, "context"),
        ({"models": []}, "models"),
    ],
)
def test_invalid_models_file_is_rejected(
    tmp_path: Path, change: dict, message: str
) -> None:
    path = _models_file(tmp_path, {**TWO_MODELS, **change})

    with pytest.raises(ValueError, match=message):
        pipeline_config(models_file=path)


def test_missing_models_file_is_an_error() -> None:
    with pytest.raises(ValueError, match="not readable"):
        pipeline_config(models_file="/nonexistent.yaml")


def test_defaults_list_every_registry_category() -> None:
    assert set(base()["categories"]) == set(registry().descriptions)


def test_settings_own_backend_and_modes() -> None:
    cfg = pipeline_config(
        vlm_backend="openai",
        vlm_api_key="secret",
        default_mode="tag",
        default_verification="block",
        default_extraction="vlm",
        docling_models_dir="/srv/docling",
    )

    assert (cfg.backend.kind, cfg.backend.api_key) == ("openai", "secret")
    assert cfg.phases.redaction.mode == "tag"
    assert cfg.phases.verification.mode == "block"
    assert cfg.phases.extraction.native_pdf.engine == "vlm"
    assert cfg.phases.extraction.native_pdf.models_dir == "/srv/docling"


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="(?i)extra"):
        pipeline_config({"phases": {"detection": {"modell": "other"}}})


def test_invalid_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        pipeline_config({"phases": {"redaction": {"mode": "erase"}}})
    with pytest.raises(ValueError, match="tag_format"):
        pipeline_config({"phases": {"redaction": {"tag_format": "[{category}]"}}})
    with pytest.raises(ValueError, match="unknown categories"):
        pipeline_config({"categories": {"INVENTATA": True}})
    with pytest.raises(ValueError, match="sconosciuta"):
        pipeline_config({"phases": {"detection": {"passes": [["gruppo_inventato"]]}}})


def test_user_file_with_groups(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "categories": {"groups": {"sanitari": False}, "DIAGNOSI": True},
                "generation": {"temperature": 0.2},
            }
        )
    )

    cfg = load_pipeline_config(make_settings(pipeline_config_file=str(path)))

    assert cfg.categories["FARMACO"] is False
    assert cfg.categories["DIAGNOSI"] is True
    assert cfg.generation.temperature == 0.2


def test_user_file_cannot_set_keys_owned_by_settings(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text("backend: {url: http://elsewhere}\n")

    with pytest.raises(ValueError, match="backend.url"):
        load_pipeline_config(make_settings(pipeline_config_file=str(path)))


@pytest.mark.parametrize(
    "content", ["models: {x: {context: 4096}}\n", "phases: {detection: {model: x}}\n"]
)
def test_user_file_cannot_set_keys_owned_by_the_models_file(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text(content)

    with pytest.raises(ValueError, match="models file"):
        load_pipeline_config(make_settings(pipeline_config_file=str(path)))


def test_models_file_is_read_once(tmp_path: Path) -> None:
    settings = make_settings(models_file=_models_file(tmp_path, TWO_MODELS))
    load_pipeline_config(settings)
    Path(settings.models_file).unlink()  # e.g. a ConfigMap changed at runtime

    assert load_pipeline_config(settings).model_for("detection")[0] == "small:1"


def test_user_file_is_read_once(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text("phases: {verification: {max_iterations: 5}}\n")
    settings = make_settings(pipeline_config_file=str(path))
    load_pipeline_config(settings)
    path.unlink()  # e.g. a ConfigMap changed at runtime

    assert load_pipeline_config(settings).phases.verification.max_iterations == 5


def test_missing_user_file_is_an_error() -> None:
    with pytest.raises(ValueError, match="not readable"):
        load_pipeline_config(make_settings(pipeline_config_file="/nonexistent.yaml"))


def test_request_overrides_win_over_settings(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text("phases: {verification: {max_iterations: 5}}\n")
    settings = make_settings(
        pipeline_config_file=str(path),
        default_mode="tag",
        models_file=_models_file(tmp_path, TWO_MODELS),
    )

    cfg = load_pipeline_config(
        settings,
        request_overrides(mode="redaction", models={"detection": "big:1"}),
    )

    assert cfg.phases.redaction.mode == "redaction"
    assert cfg.model_for("detection")[0] == "big:1"
    assert cfg.model_for("redaction")[0] == "small:1"
    assert cfg.phases.verification.max_iterations == 5


def test_request_chooses_the_detectors() -> None:
    cfg = pipeline_config(request_overrides(detectors=("ner",)))

    d = cfg.phases.detection.detectors
    assert d.ner.enabled and not (d.vlm or d.rules or d.labels or d.eponyms)
    d = pipeline_config(
        request_overrides(detectors=("rules", "eponyms"))
    ).phases.detection.detectors
    assert d.rules and d.eponyms and not (d.vlm or d.labels or d.ner.enabled)
    with pytest.raises(ValueError, match="unknown detector"):
        request_overrides(detectors=("ocr",))
    with pytest.raises(ValueError, match="at least one detector"):
        pipeline_config(
            request_overrides(detectors=("eponyms",))
        )  # a filter finds nothing


def test_request_override_outside_the_catalog_is_rejected() -> None:
    with pytest.raises(ValueError, match="not in the models file"):
        pipeline_config(request_overrides(models={"detection": "other:latest"}))


def test_prompts_are_validated() -> None:
    with pytest.raises(ValueError, match="unknown placeholders"):
        pipeline_config(
            {"prompts": {"detection": {"user": "${categories} ${text} ${unknown}"}}}
        )
    with pytest.raises(ValueError, match="missing placeholders"):
        pipeline_config({"prompts": {"verification": {"user": "${categories}"}}})


def test_render_prompt() -> None:
    prompt = render(
        pipeline_config(), "detection", "user", categories="- X: y", text="ciao", page=2
    )

    assert "<<<\nciao\n>>>" in prompt
    assert "page 2" in prompt


def test_passes_hold_only_active_categories_and_the_rest_last() -> None:
    cfg = pipeline_config(
        {
            "phases": {"detection": {"passes": [["anagrafica"]]}},
            "categories": {"SESSO": False},
        }
    )

    passes = cfg.passes("detection")

    assert "SESSO" not in passes[0]
    assert "PERSONA" in passes[0]
    assert "CODICE_FISCALE" in passes[-1]
    assert sorted(c for p in passes for c in p) == sorted(cfg.active())


def test_request_overrides_enable_and_disable() -> None:
    cfg = pipeline_config(
        request_overrides(enable=("DATA,organizzazioni",), disable=("sanitari",))
    )

    assert cfg.categories["DATA"] and cfg.categories["REPARTO"]
    assert not cfg.categories["DIAGNOSI"]
    with pytest.raises(ValueError, match="sconosciuta"):
        request_overrides(enable=("NON_ESISTE",))


def test_transcript_override() -> None:
    assert (
        pipeline_config(request_overrides(transcript=True)).output.save_transcript
        is True
    )
    assert pipeline_config(request_overrides()).output.save_transcript is False


def test_effective_configuration_hides_the_api_key() -> None:
    assert (
        effective(pipeline_config(vlm_api_key="secret"))["backend"]["api_key"] == "***"
    )
