import ast
import asyncio
import json
from pathlib import Path

import pytest

from anonymization_pipeline import detectors
from anonymization_pipeline.detection import Outcome, detect
from anonymization_pipeline.detectors.eponyms import is_excluded
from anonymization_pipeline.domain import Document, Page
from anonymization_pipeline.pipeline_config import PipelineConfig
from tests.pipeline import FakeVLM, IdealModel, pipeline_config

TEXT = "Paziente: Mario Rossi\nCF: RSSMRA80A01H501U\nManovra di Valsalva positiva.\n"
NER_OFF = {"ner": {"enabled": False}}


def _cfg(detector_overrides: dict | None = None) -> PipelineConfig:
    return pipeline_config(
        {
            "phases": {
                "detection": {"detectors": {**NER_OFF, **(detector_overrides or {})}}
            }
        }
    )


def _detect(
    model: IdealModel, cfg: PipelineConfig, text: str = TEXT
) -> tuple[Outcome, FakeVLM]:
    client = FakeVLM(cfg, model)
    doc = Document(kind="text", pages=[Page(index=0, text=text)])
    return asyncio.run(detect(doc, client, cfg)), client


def _found(outcome: Outcome) -> dict[tuple[str, str], str]:
    return {(e.text, e.category): e.source for e in outcome.entities}


def test_every_detector_can_be_switched_off() -> None:
    assert [d.name for d in detectors.active(_cfg())] == ["rules", "labels"]
    assert [d.name for d in detectors.active(_cfg({"rules": False}))] == ["labels"]
    assert [d.name for d in detectors.active(pipeline_config())] == [
        "rules",
        "labels",
        "ner",
    ]
    with pytest.raises(ValueError, match="at least one detector"):
        _cfg({"vlm": False, "rules": False, "labels": False})


def test_without_the_vlm_no_detection_call() -> None:
    model = IdealModel({"Mario Rossi": "PERSONA", "RSSMRA80A01H501U": "CODICE_FISCALE"})

    outcome, client = _detect(model, _cfg({"vlm": False}))

    assert client.calls == []
    assert outcome.sources == {"rules": 1, "labels": 1}
    assert ("Mario Rossi", "PERSONA") in _found(outcome)


def test_one_detector_is_enough() -> None:
    # the VLM sees neither the name nor the tax code: labels and rules find them
    model = IdealModel(
        {"Mario Rossi": "PERSONA", "RSSMRA80A01H501U": "CODICE_FISCALE"},
        missed=("Mario Rossi", "RSSMRA80A01H501U"),
    )

    outcome, _ = _detect(model, _cfg())

    assert _found(outcome) == {
        ("Mario Rossi", "PERSONA"): "labels",
        ("RSSMRA80A01H501U", "CODICE_FISCALE"): "rules",
    }
    assert outcome.sources["vlm"] == 0


def test_most_reliable_detector_comes_first() -> None:
    # the VLM gives the tax code another category (different family): the rules one comes first
    outcome, _ = _detect(
        IdealModel({"RSSMRA80A01H501U": "NUMERO_CARTA_IDENTITA"}), _cfg()
    )

    categories = [e.category for e in outcome.entities if e.text == "RSSMRA80A01H501U"]
    assert categories[0] == "CODICE_FISCALE"


def test_eponyms_are_discarded_for_every_detector() -> None:
    model = IdealModel({"Mario Rossi": "PERSONA", "Valsalva": "COGNOME"})

    outcome, _ = _detect(model, _cfg())

    assert ("Valsalva", "COGNOME") not in _found(outcome)
    assert outcome.eponyms_discarded >= 1
    outcome, _ = _detect(model, _cfg({"eponyms": False}))
    assert ("Valsalva", "COGNOME") in _found(outcome)


def test_hallucinations_and_inactive_categories() -> None:
    cfg = pipeline_config(
        {
            "phases": {
                "detection": {"detectors": {**NER_OFF, "rules": False, "labels": False}}
            },
            "categories": {"MEDICO": False, "CODICE_FISCALE": False},
        }
    )
    answer = json.dumps(
        {
            "entities": [
                {"text": "Mario Rossi", "category": "MEDICO"},
                {"text": "RSSMRA80A01H501U", "category": "CODICE_FISCALE"},
                {"text": "Luca Bianchi", "category": "PERSONA"},
            ]
        }
    )
    client = FakeVLM(cfg, lambda *args: answer)
    doc = Document(kind="text", pages=[Page(index=0, text=TEXT)])

    outcome = asyncio.run(detect(doc, client, cfg))

    # MEDICO off, PERSONA on: the generic category; CODICE_FISCALE has no generic one
    assert _found(outcome) == {("Mario Rossi", "PERSONA"): "detection"}
    assert outcome.inactive_categories == len(cfg.passes("detection"))
    assert outcome.hallucinations_discarded == len(cfg.passes("detection"))


def test_unavailable_ner_gives_a_warning(tmp_path: Path) -> None:
    cfg = _cfg({"ner": {"enabled": True, "model": str(tmp_path / "missing")}})

    outcome, _ = _detect(IdealModel({"Mario Rossi": "PERSONA"}), cfg)

    assert "ner_unavailable" in outcome.warnings
    assert outcome.sources["ner"] == 0
    assert ("Mario Rossi", "PERSONA") in _found(outcome)


def test_eponym_proposed_as_a_drug() -> None:
    assert is_excluded("Valsalva", "FARMACO") and is_excluded("Valsalva", "MEDICO")
    assert is_excluded("Coumadin", "PERSONA")
    assert not is_excluded(
        "Coumadin", "FARMACO"
    )  # a real drug stays a drug (health data)
    assert not is_excluded(
        "Parkinson", "DIAGNOSI"
    )  # a diagnosis is never left in clear text
    assert is_excluded("rossi", "COGNOME") and not is_excluded("Rossi", "COGNOME")


def test_detectors_are_independent() -> None:
    """Every detector imports only base, patterns, the configuration and external libraries: never
    another one."""
    root = Path(detectors.__file__).parent
    allowed = (
        "anonymization_pipeline.detectors.base",
        "anonymization_pipeline.detectors.patterns",
        "anonymization_pipeline.pipeline_config",
    )
    for name in ("rules", "labels", "ner"):
        own = f"anonymization_pipeline.detectors.{name}"
        for path in (root / name).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "anonymization_pipeline"
                ):
                    module = node.module or ""
                    assert module.startswith((*allowed, own)), (
                        f"{path.name} imports {module}"
                    )


def test_common_words_are_discarded_where_found_in_lowercase() -> None:
    text = "Paziente: ROSSI MARIO\nEritrociti (globuli rossi) 5,11\n"
    model = IdealModel({"ROSSI MARIO": "PERSONA", "ROSSI": "COGNOME"})

    outcome, _ = _detect(model, _cfg(), text)

    assert ("ROSSI", "COGNOME") in _found(outcome)
    assert ("rossi", "COGNOME") not in _found(outcome)
    outcome, _ = _detect(model, _cfg({"eponyms": False}), text)
    assert ("rossi", "COGNOME") in _found(outcome)
