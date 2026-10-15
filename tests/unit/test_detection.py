import pytest

from anonymization_pipeline.detection import Found, Outcome, _merge, detect
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.pipeline_config import (
    PipelineConfig,
    load_pipeline_config,
    request_config,
)
from tests.conftest import FAKE_CF, make_settings
from tests.documents import EMAIL, LETTER_LINES, PATIENT, PHONE, text_layer


def _cfg(**detection: object) -> PipelineConfig:
    cfg = load_pipeline_config(make_settings(pipeline_config_file=""))
    if not detection:
        return cfg
    data = cfg.model_dump()
    for key, value in detection.items():
        section, name = key.split("__")
        data["detection"][section][name] = value
    return PipelineConfig.model_validate(data)


def _texts(outcome: Outcome, layers: list[TextLayer], layer: int = 0) -> list[str]:
    return [
        layers[f.layer].text[f.start : f.end] for f in outcome.found if f.layer == layer
    ]


def test_letter_identifiers_are_found_and_eponyms_are_not() -> None:
    layers = [text_layer(0, "\n".join(LETTER_LINES))]

    found = _texts(detect(layers, _cfg()), layers)

    for value in (PATIENT, FAKE_CF, PHONE, EMAIL, "Laura Bianchi", "2026123456"):
        assert value in found
    assert not any("Crohn" in v or "Babinski" in v for v in found)


def test_values_are_redacted_in_every_layer() -> None:
    # the record number is recognised only after its label, on page 1
    layers = [
        text_layer(0, "Nosologico 2026123456"),
        text_layer(1, "riferimento 2026123456 in fondo alla pagina", source="ocr"),
    ]

    outcome = detect(layers, _cfg())

    assert _texts(outcome, layers, 1) == ["2026123456"]
    assert [f.source for f in outcome.found if f.layer == 1] == ["propagation"]


def test_names_are_found_in_their_other_forms() -> None:
    layers = [
        text_layer(0, f"Paziente: {PATIENT}"),
        text_layer(
            1, "Firma ROSSI M. - Rossi Mario - ROSSIMARIO\nIl Sig. Rossi e M. Rossi"
        ),
    ]

    found = _texts(detect(layers, _cfg()), layers, 1)

    assert {"ROSSI M.", "Rossi Mario", "ROSSIMARIO", "M. Rossi"} <= set(found)


def test_name_tokens_need_a_capital_letter_and_no_saint_before() -> None:
    layers = [
        text_layer(0, "Paziente: Bianca Camillo"),
        text_layer(1, "sostanza bianca\nOspedale San Camillo\nla Sig.ra Bianca"),
    ]

    found = _texts(detect(layers, _cfg()), layers, 1)

    assert found == ["Bianca"]


def test_a_patient_named_like_an_eponym_is_redacted_only_as_a_person() -> None:
    layers = [
        text_layer(0, "Paziente: Mario Parkinson"),
        text_layer(1, "morbo di Parkinson in trattamento\nil Sig. Parkinson riferisce"),
    ]

    found = _texts(detect(layers, _cfg()), layers, 1)

    assert found == ["Parkinson"]
    assert "riferisce" in layers[1].text[layers[1].text.index("Parkinson ") :]


def test_name_propagation_can_be_switched_off() -> None:
    layers = [
        text_layer(0, f"Paziente: {PATIENT}"),
        text_layer(1, "Firma ROSSIMARIO"),
    ]

    found = _texts(detect(layers, _cfg(propagation__names=False)), layers, 1)

    assert found == []


def test_inactive_category_falls_back_to_its_generic_one() -> None:
    layers = [text_layer(0, "Medico curante: Dott. Luca Verdi")]
    cfg = request_config(_cfg(), disable=("persone_correlate",))

    outcome = detect(layers, cfg)

    assert [(layers[0].text[f.start : f.end], f.category) for f in outcome.found] == [
        ("Luca Verdi", "PERSONA")
    ]


def test_each_detector_can_be_switched_off() -> None:
    layers = [text_layer(0, f"Paziente: {PATIENT}  Codice fiscale: {FAKE_CF}")]

    rules_only = _texts(detect(layers, _cfg(detectors__labels=False)), layers)
    labels_only = _texts(detect(layers, _cfg(detectors__rules=False)), layers)

    assert FAKE_CF in rules_only and PATIENT not in rules_only
    assert PATIENT in labels_only


def test_at_least_one_detector_must_be_on() -> None:
    with pytest.raises(ValueError, match="at least one detector"):
        _cfg(detectors__rules=False, detectors__labels=False)


def test_overlapping_spans_become_the_longest_one() -> None:
    found = [
        Found(0, 10, 26, "CODICE_FISCALE", "rules"),
        Found(0, 0, 40, "URL_PERSONALE", "rules"),
        Found(0, 50, 55, "COGNOME", "labels"),
        Found(0, 50, 55, "PERSONA", "propagation"),
        Found(0, 50, 55, "MEDICO", "propagation"),
    ]

    merged = _merge(0, found)

    assert merged == [
        Found(0, 0, 40, "URL_PERSONALE", "rules"),
        # same length: the most reliable source wins, labels before propagation
        Found(0, 50, 55, "COGNOME", "labels"),
    ]


def test_outcome_values_and_counters() -> None:
    layers = [text_layer(0, f"Paziente: {PATIENT}\nmorbo di Crohn")]

    outcome = detect(layers, _cfg())

    assert (PATIENT, "PERSONA") in outcome.values(layers)
    assert outcome.hallucinations_discarded == 0
