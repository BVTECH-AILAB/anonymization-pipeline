from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from anonymization_pipeline.main import create_app
from anonymization_pipeline.pipeline_config import (
    PipelineConfig,
    ProtectedCategoryError,
    load_pipeline_config,
    registry,
    request_config,
)
from anonymization_pipeline.pipeline_config.categories import _build
from anonymization_pipeline.pipeline_config.loader import base
from anonymization_pipeline.pipeline_config.resources import read_yaml
from tests.conftest import make_settings


def _user_file(tmp_path: Path, content: dict) -> str:
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(content))
    return str(path)


def test_packaged_defaults_are_valid() -> None:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=""))

    assert not cfg.categories["DATA"]  # quasi-identifiers off by default (PA-14)
    assert cfg.categories["CODICE_FISCALE"]
    assert set(cfg.protected()) <= set(cfg.active())


def test_every_registry_category_has_a_default() -> None:
    defaults = base()["categories"]

    assert list(defaults) == list(registry().descriptions)
    assert all(isinstance(on, bool) for on in defaults.values())


def test_packaged_registry_is_valid_and_the_test_registry_is_aligned() -> None:
    packaged = read_yaml(
        "categories.yaml"
    )  # not the monkeypatched reader of the registry

    assert _build(packaged).groups == registry().groups


def test_tests_run_with_every_category_active() -> None:
    cfg = load_pipeline_config(make_settings())

    assert set(cfg.active()) == set(registry().descriptions)


def test_user_file_with_groups(tmp_path: Path) -> None:
    path = _user_file(
        tmp_path, {"categories": {"groups": {"sanitari": False}, "DIAGNOSI": True}}
    )

    cfg = load_pipeline_config(make_settings(pipeline_config_file=path))

    assert cfg.categories["FARMACO"] is False
    assert cfg.categories["DIAGNOSI"] is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ({"categorie": {}}, "(?i)extra"),
        ({"categories": {"INVENTATA": True}}, "unknown categories"),
        ({"categories": {"groups": {"inventato": True}}}, "Unknown group"),
        (
            {"categories": {"CODICE_FISCALE": False}},
            "protected categories switched off",
        ),
        ({"protection": {"protected": ["inventato"]}}, "sconosciuta"),
        ({"protection": {"protected": []}}, "(?i)at least 1"),
    ],
)
def test_invalid_user_file_is_rejected(
    tmp_path: Path, content: dict, message: str
) -> None:
    path = _user_file(tmp_path, content)

    with pytest.raises(ValueError, match=message):
        load_pipeline_config(make_settings(pipeline_config_file=path))


def test_missing_user_file_is_an_error() -> None:
    with pytest.raises(ValueError, match="not readable"):
        load_pipeline_config(make_settings(pipeline_config_file="/nonexistent.yaml"))


def test_invalid_user_file_stops_the_startup(tmp_path: Path) -> None:
    path = _user_file(tmp_path, {"categories": {"INVENTATA": True}})
    app = create_app(make_settings(pipeline_config_file=path))

    with pytest.raises(ValueError, match="unknown categories"), TestClient(app):
        pass


def test_startup_stores_the_service_configuration() -> None:
    app = create_app(make_settings())

    with TestClient(app):
        assert isinstance(app.state.pipeline_config, PipelineConfig)


def _service(**categories: bool) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=""))
    return PipelineConfig.model_validate(
        {**cfg.model_dump(), "categories": {**cfg.categories, **categories}}
    )


def test_request_switches_categories_on_and_off() -> None:
    service = _service()

    cfg = request_config(
        service, enable=("DATA,organizzazioni",), disable=("ETA", "REPARTO")
    )

    assert cfg.categories["DATA"] and cfg.categories["STRUTTURA_SANITARIA"]
    assert not cfg.categories["REPARTO"]  # disable wins over enable
    assert not cfg.categories["ETA"]
    assert not service.categories["DATA"]  # the service configuration is untouched


@pytest.mark.parametrize("entry", ["CODICE_FISCALE", "identificativi", "persona"])
def test_request_cannot_switch_off_a_protected_category(entry: str) -> None:
    with pytest.raises(ProtectedCategoryError, match="protette"):
        request_config(_service(), disable=(entry,))


def test_request_with_an_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="sconosciuta"):
        request_config(_service(), enable=("NON_ESISTE",))


def test_registry_families_and_ranks() -> None:
    reg = registry()

    assert reg.family("MEDICO") == "PERSONA"
    assert reg.family("CODICE_FISCALE") == "CODICE_FISCALE"
    assert (reg.rank("MEDICO"), reg.rank("PERSONA"), reg.rank("COGNOME")) == (2, 1, 0)
    assert reg.expand(["contatti", "iban"])[:2] == ["EMAIL", "PEC"]
    assert reg.expand(["contatti", "iban"])[-1] == "IBAN"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"groups": {"a": {"X": "x"}, "b": {"X": "x"}}}, "more than one group"),
        ({"groups": {"a": {"X": "x"}}, "generic": {"X": ["Y"]}}, "unknown category"),
        ({"groups": {"a": {"X": "x"}}, "partial": ["Y"]}, "unknown categories"),
    ],
)
def test_invalid_registry_is_rejected(data: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _build(data)


def test_invalid_detector_file_stops_the_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> None:
        raise ValueError("detectors/rules.yaml: invalid regex")

    monkeypatch.setattr("anonymization_pipeline.main.load_resources", broken)
    app = create_app(make_settings())

    with pytest.raises(ValueError, match="rules.yaml"), TestClient(app):
        pass
