from pathlib import Path
from typing import Any

import pytest

from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import (
    NER_INSTALLED,
    NerDetector,
    download,
    model_dir,
    windows,
)
from anonymization_pipeline.pipeline_config.schema import Ner
from tests.pipeline import pipeline_config


def _ner_cfg(overrides: dict | None = None) -> Ner:
    cfg = pipeline_config(
        {"phases": {"detection": {"detectors": {"ner": overrides or {}}}}}
    )
    return cfg.phases.detection.detectors.ner


def _detector(
    entities: dict[str, str], cfg: Ner | None = None
) -> tuple[NerDetector, list[str]]:
    """Detector with a fake pipeline: ``entities`` text -> model label (score 0.9)."""
    calls: list[str] = []

    def pipeline(text: str) -> list[dict[str, Any]]:
        calls.append(text)
        out = []
        for value, label in entities.items():
            i = text.find(value)
            if i >= 0:
                out.append(
                    {
                        "start": i,
                        "end": i + len(value),
                        "entity_group": label,
                        "score": 0.9,
                    }
                )
        return out

    return NerDetector(cfg or _ner_cfg(), pipeline=pipeline), calls


def test_label_map_and_threshold() -> None:
    detector, _ = _detector(
        {
            "Mario Rossi": "NOME",
            "polmonite": "MALATTIA",
            "Tachipirina": "MEDICINA",
            "500 euro": "IMPORTO",
        }
    )

    found = {
        (p.category, p.value)
        for p in detector.detect(
            "Il sig. Mario Rossi ha la polmonite, prende Tachipirina, 500 euro"
        )
    }

    # IMPORTO is not mapped
    assert found == {
        ("NOME", "Mario Rossi"),
        ("DIAGNOSI", "polmonite"),
        ("FARMACO", "Tachipirina"),
    }
    detector, _ = _detector({"polmonite": "MALATTIA"}, _ner_cfg({"threshold": 0.95}))
    assert detector.detect("ha la polmonite") == []


def test_adjacent_first_and_last_name_become_a_person() -> None:
    detector, _ = _detector({"Marco": "NOME", "BELLANDI": "COGNOME"})

    found = [
        (p.category, p.value)
        for p in detector.detect("Paziente: BELLANDI Marco, nato a Bergamo")
    ]

    assert found == [("PERSONA", "BELLANDI Marco")]


def test_filters() -> None:
    detector, _ = _detector(
        {
            "Paziente": "NOME",
            "DIAGNOSI": "COGNOME",
            "roma": "LUOGO",
            "Indirizzo": "LUOGO",
        }
    )

    assert detector.detect("Paziente\nDIAGNOSI finale\nvia roma\nIndirizzo: ...") == []


def test_overlapping_windows_and_deduplication() -> None:
    text = " ".join(f"parola{i}" for i in range(700)) + " Mario Rossi"
    detector, calls = _detector({"Mario Rossi": "NOME"})

    assert len(windows(text, 300, 50)) == 3
    assert [p.value for p in detector.detect(text)] == ["Mario Rossi"]
    assert len(calls) == 3


def test_missing_model_is_unavailable(tmp_path: Path) -> None:
    cfg = _ner_cfg({"model": str(tmp_path / "missing")})

    with pytest.raises(DetectorUnavailable):
        NerDetector(cfg).detect("Mario Rossi")
    with pytest.raises(DetectorUnavailable):
        model_dir(cfg)
    with pytest.raises(DetectorUnavailable):
        download(cfg)


def test_local_model_folder(tmp_path: Path) -> None:
    assert model_dir(_ner_cfg({"model": str(tmp_path)})) == str(tmp_path)


@pytest.mark.ner
def test_real_model() -> None:
    """With the 'ner' extra installed and the model in the cache (`just models`)."""
    if not NER_INSTALLED:
        pytest.skip("the 'ner' extra is not installed")
    try:
        model_dir(_ner_cfg())
    except DetectorUnavailable:
        pytest.skip("NER model missing in the cache")
    detector = NerDetector(_ner_cfg())

    found = {
        (p.category, p.value)
        for p in detector.detect(
            "Il paziente Marco Bellandi, nato a Bergamo, assume metformina per il diabete mellito."
        )
    }

    assert any(
        c in ("PERSONA", "NOME", "COGNOME") and "Bellandi" in v for c, v in found
    ), found
