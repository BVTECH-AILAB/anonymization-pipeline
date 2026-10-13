"""Validation configuration: profiles, effective parameters, checks."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from anonval.settings import Config, Parameters, load_config

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def test_shipped_config_is_valid() -> None:
    config = load_config(CONFIG)
    assert config.profili
    assert Path(config.dataset.percorso).is_absolute()


def test_profile_overrides_common_parameters() -> None:
    config = Config.model_validate(
        {
            "comune": {
                "mode": "tag",
                "verification": "report",
                "detectors": ["rules", "vlm"],
            },
            "profili": [{"nome": "solo-regole", "detectors": ["rules", "labels"]}],
        }
    )
    params = config.effective(config.profili[0])
    assert params.mode == "tag"
    assert params.detectors == ["rules", "labels"]
    assert params.form() == {
        "mode": "tag",
        "verification": "report",
        "detectors": "rules,labels",
    }


def test_form_encodes_lists_and_booleans() -> None:
    params = Parameters(
        enable=["anagrafica", "SESSO"], layout=False, model_detection="m:1"
    )
    assert params.form() == {
        "model_detection": "m:1",
        "enable": "anagrafica,SESSO",
        "layout": "false",
    }
    assert params.models() == ["m:1"]


@pytest.mark.parametrize(
    "profile",
    [
        {"nome": "x", "mode": "censura"},
        {"nome": "x", "enable": ["gatti"]},
        {"nome": "x", "detectors": ["eponyms"]},
        {"nome": "x", "modalita": "tag"},
        {"nome": "Nome Con Spazi"},
    ],
)
def test_invalid_profiles_are_rejected(profile: dict) -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"profili": [profile]})


def test_duplicate_profile_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"profili": [{"nome": "a"}, {"nome": "a"}]})


def test_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_config(tmp_path / "manca.yaml")
