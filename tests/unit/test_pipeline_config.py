from pathlib import Path

import pytest
import yaml

from anonymization_pipeline.pipeline_config import (
    effective,
    load_pipeline_config,
    registry,
    render,
    request_overrides,
)
from anonymization_pipeline.pipeline_config.loader import base
from tests.conftest import make_settings
from tests.pipeline import pipeline_config


def test_defaults_are_valid_and_the_model_is_single() -> None:
    cfg = pipeline_config()

    assert cfg.model.name == "gemma4:26b-a4b-it-qat"
    assert cfg.backend.kind == "ollama"
    assert cfg.backend.api_key is None
    assert cfg.model.think is False
    for phase in ("extraction", "detection", "redaction", "verification"):
        assert "model" not in getattr(cfg.phases, phase).model_dump()


def test_defaults_list_every_registry_category() -> None:
    assert set(base()["categories"]) == set(registry().descriptions)


def test_settings_own_backend_model_and_modes() -> None:
    cfg = pipeline_config(
        vlm_model="gemma4:31b",
        vlm_context=8192,
        vlm_backend="openai",
        vlm_api_key="secret",
        default_mode="tag",
        default_verification="block",
        default_extraction="vlm",
        docling_models_dir="/srv/docling",
    )

    assert (cfg.model.name, cfg.model.context) == ("gemma4:31b", 8192)
    assert (cfg.backend.kind, cfg.backend.api_key) == ("openai", "secret")
    assert cfg.phases.redaction.mode == "tag"
    assert cfg.phases.verification.mode == "block"
    assert cfg.phases.extraction.native_pdf.engine == "vlm"
    assert cfg.phases.extraction.native_pdf.models_dir == "/srv/docling"


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="(?i)extra"):
        pipeline_config({"phases": {"detection": {"model": "other"}}})


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
                "model": {"temperature": 0.2},
            }
        )
    )

    cfg = load_pipeline_config(make_settings(pipeline_config_file=str(path)))

    assert cfg.categories["FARMACO"] is False
    assert cfg.categories["DIAGNOSI"] is True
    assert cfg.model.temperature == 0.2


def test_user_file_cannot_set_keys_owned_by_settings(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text("model: {name: from_file}\n")

    with pytest.raises(ValueError, match="model.name"):
        load_pipeline_config(make_settings(pipeline_config_file=str(path)))


def test_missing_user_file_is_an_error() -> None:
    with pytest.raises(ValueError, match="not readable"):
        load_pipeline_config(make_settings(pipeline_config_file="/nonexistent.yaml"))


def test_request_overrides_win_over_settings(tmp_path: Path) -> None:
    path = tmp_path / "mine.yaml"
    path.write_text("phases: {verification: {max_iterations: 5}}\n")
    settings = make_settings(pipeline_config_file=str(path), default_mode="tag")

    cfg = load_pipeline_config(
        settings, request_overrides(mode="redaction", model="other:latest")
    )

    assert cfg.phases.redaction.mode == "redaction"
    assert cfg.model.name == "other:latest"
    assert cfg.phases.verification.max_iterations == 5


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
