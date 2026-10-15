import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from anonymization_pipeline.detection import detect
from anonymization_pipeline.detectors import ner
from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import (
    FILE,
    NER_INSTALLED,
    GlinerEngine,
    NerDetector,
    Prediction,
    TokenClassificationEngine,
    download,
    load_engine,
    model_dir,
    vocabulary,
    windows,
)
from anonymization_pipeline.main import create_app
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.pipeline_config.resources import read_yaml
from anonymization_pipeline.pipeline_config.schema import Ner
from anonymization_pipeline.services.anonymization import anonymize
from tests.conftest import DEFAULTS_FILE, make_settings
from tests.documents import letter_pdf, text_layer


def _cfg(**ner_cfg: Any) -> PipelineConfig:
    """Defaults without models; the fake engine speaks token classification unless told."""
    cfg = load_pipeline_config(make_settings(pipeline_config_file=DEFAULTS_FILE))
    data = cfg.model_dump()
    data["detection"]["ner"].update({"engine": "token_classification", **ner_cfg})
    return PipelineConfig.model_validate(data)


def _ner(**ner_cfg: Any) -> Ner:
    return _cfg(**ner_cfg).detection.ner


class FakeEngine:
    """Finds the given strings with their label (score 0.9)."""

    def __init__(self, entities: dict[str, str]) -> None:
        self.entities = entities
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def predict(self, text: str) -> list[Prediction]:
        self.calls.append(text)
        out = []
        for value, label in self.entities.items():
            i = text.find(value)
            if i >= 0:
                out.append((i, i + len(value), label, 0.9))
        return out


def _detector(entities: dict[str, str], cfg: Ner | None = None) -> NerDetector:
    return NerDetector(cfg or _ner(enabled=True), FakeEngine(entities))


def test_label_map_and_threshold() -> None:
    detector = _detector(
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
    strict = _detector({"polmonite": "MALATTIA"}, _ner(enabled=True, threshold=0.95))
    assert strict.detect("ha la polmonite") == []


def test_gliner_labels_are_mapped() -> None:
    detector = _detector(
        {"Ospedale Maggiore": "hospital", "Laura Bianchi": "person", "x": "unknown"},
        _ner(enabled=True, engine="gliner"),
    )

    found = {
        (p.category, p.value)
        for p in detector.detect("Laura Bianchi, Ospedale Maggiore")
    }

    assert found == {
        ("PERSONA", "Laura Bianchi"),
        ("STRUTTURA_SANITARIA", "Ospedale Maggiore"),
    }


def test_adjacent_first_and_last_name_become_a_person() -> None:
    detector = _detector({"Marco": "NOME", "BELLANDI": "COGNOME"})

    found = [
        (p.category, p.value)
        for p in detector.detect("Paziente: BELLANDI Marco, nato a Bergamo")
    ]

    assert found == [("PERSONA", "BELLANDI Marco")]


def test_filters() -> None:
    detector = _detector(
        {
            "Paziente": "NOME",
            "DIAGNOSI": "COGNOME",
            "roma": "LUOGO",
            "Indirizzo": "LUOGO",
        }
    )

    assert detector.detect("Paziente\nDIAGNOSI finale\nvia roma\nIndirizzo: ...") == []


def test_vocabulary_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = read_yaml(FILE)
    broken = [
        ({**valid, "label_map": {"MALATTIA": "INESISTENTE"}}, "unknown categories"),
        ({**valid, "gliner_labels": {"person": "INESISTENTE"}}, "unknown categories"),
        ({**valid, "require_capital": ["INESISTENTE"]}, "unknown categories"),
        ({**valid, "threshold": 0.5}, "unknown keys threshold"),
        (
            {k: v for k, v in valid.items() if k != "label_map"},
            "missing keys label_map",
        ),
        ({**valid, "min_length": 0}, "min_length"),
        ({**valid, "false_positives": "paziente"}, "false_positives"),
    ]
    for data, message in broken:
        monkeypatch.setattr(ner, "read_yaml", lambda name, data=data: data)
        vocabulary.cache_clear()
        with pytest.raises(ValueError, match=message):
            vocabulary()
    vocabulary.cache_clear()


def test_overlapping_windows_and_deduplication() -> None:
    text = " ".join(f"parola{i}" for i in range(700)) + " Mario Rossi"
    engine = FakeEngine({"Mario Rossi": "NOME"})
    detector = NerDetector(
        _ner(enabled=True, window_words=300, overlap_words=50), engine
    )

    assert len(windows(text, 300, 50)) == 3
    assert [p.value for p in detector.detect(text)] == ["Mario Rossi"]
    assert len(engine.calls) == 3


def test_engines_translate_their_model_output() -> None:
    def pipeline(text: str) -> list[dict[str, Any]]:
        return [{"start": 0, "end": 5, "entity_group": "NOME", "score": 0.8}]

    class Gliner:
        def predict_entities(
            self, text: str, labels: list[str], threshold: float
        ) -> list:
            return [{"start": 0, "end": 5, "label": labels[0], "score": 0.7}]

    assert TokenClassificationEngine(pipeline).predict("Mario") == [(0, 5, "NOME", 0.8)]
    assert GlinerEngine(Gliner(), ["person"], 0.5).predict("Mario") == [
        (0, 5, "person", 0.7)
    ]


def test_missing_model_is_unavailable(tmp_path: Path) -> None:
    cfg = _ner(enabled=True, model=str(tmp_path / "missing"))

    for call in (model_dir, download, load_engine):
        with pytest.raises(DetectorUnavailable):
            call(cfg)


def test_local_model_folder(tmp_path: Path) -> None:
    assert model_dir(_ner(model=str(tmp_path))) == str(tmp_path)


def test_ner_proposals_are_anchored_like_the_others() -> None:
    layers = [text_layer(0, "Visita del dottor Gianni Fumagalli in ambulatorio")]
    engine = FakeEngine({"Gianni Fumagalli": "NOME"})

    outcome = detect(layers, _cfg(enabled=True), engine)

    (found,) = outcome.found
    assert layers[0].text[found.start : found.end] == "Gianni Fumagalli"
    assert outcome.sources == {"ner": 1}


def test_enabled_ner_without_a_model_never_runs_weaker(tmp_path: Path) -> None:
    cfg = _cfg(enabled=True)

    with pytest.raises(DetectorUnavailable):
        detect([text_layer(0, "Mario Rossi")], cfg)
    with pytest.raises(DetectorUnavailable):
        anonymize(letter_pdf(tmp_path / "in.pdf").read_bytes(), cfg)


def test_service_is_not_ready_and_refuses_documents_without_its_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        yaml.safe_dump(
            {"detection": {"ner": {"enabled": True, "model": str(tmp_path / "no")}}}
        )
    )
    app = create_app(make_settings(pipeline_config_file=str(path)))

    with TestClient(app) as client:
        for _ in range(100):  # the models load in the background
            if not app.state.models_ready and client.get("/readyz").status_code == 503:
                break
        assert client.get("/readyz").json() == {
            "status": "not_ready",
            "reason": "models",
        }
        job = client.post(
            "/anonymizations",
            files={"file": ("x.pdf", letter_pdf(tmp_path / "x.pdf").read_bytes())},
        ).json()["id"]
        for _ in range(500):
            status = client.get(f"/anonymizations/{job}").json()
            if status["status"] == "failed":
                break
        assert status["error"].startswith("Servizio non ancora pronto")


@pytest.mark.models
@pytest.mark.parametrize(
    ("engine", "model"),
    [
        ("token_classification", "DeepMount00/Italian_NER_XXL_v2"),
        ("gliner", "urchade/gliner_multi_pii-v1"),
    ],
)
def test_real_model(engine: str, model: str) -> None:
    """With the 'ner' extra installed and the model in the cache (`just models`)."""
    if not NER_INSTALLED:
        pytest.skip("the 'ner' extra is not installed")
    cfg = _ner(enabled=True, engine=engine, model=model, threshold=0.5)
    try:
        loaded = load_engine(cfg)
    except DetectorUnavailable:
        pytest.skip("NER model missing in the cache")

    found = {
        (p.category, p.value)
        for p in NerDetector(cfg, loaded).detect(
            "Il paziente Marco Bellandi, nato a Bergamo, assume metformina per il diabete mellito."
        )
    }

    assert any(
        c in ("PERSONA", "NOME", "COGNOME") and "Bellandi" in v for c, v in found
    ), found
