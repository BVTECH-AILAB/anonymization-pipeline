from anonymization_pipeline.pipeline_config.schema import Anchoring
from anonymization_pipeline.text import (
    find,
    replacement_regex,
    replacement_spans,
    tag_category,
)
from tests.pipeline import pipeline_config


def _anchoring(**changes: object) -> Anchoring:
    return pipeline_config().phases.detection.anchoring.model_copy(update=changes)


def test_literal_with_spaces_and_markdown() -> None:
    a = _anchoring()
    text = "Paziente: **Mario**\n  Rossi, nato a Roma"

    assert [text[s:e] for s, e in find(text, "Mario Rossi", a)] == ["Mario**\n  Rossi"]
    assert find("MARIO ROSSI", "Mario Rossi", a)  # over 3 characters: case-insensitive
    assert not find("Pro", "pro", a) and find(
        "pro", "pro", a
    )  # short values: exact case
    assert not find("Rossini", "Rossi", a)  # never inside a word


def test_min_length_and_hallucinations() -> None:
    a = _anchoring()

    assert find("Sesso: M", "M", a) == []
    assert find("Mario Rossi", "Giuseppe Verdi", a) == []


def test_approximate_only_without_a_literal_match() -> None:
    text = "Il dott. Mario Rossl, Via Roma 1."

    assert [text[s:e] for s, e in find(text, "Mario Rossi", _anchoring())] == [
        "Mario Rossl"
    ]  # no comma
    assert find(text, "Mario Rossi", _anchoring(fuzzy=0)) == []


def test_tags_are_excluded() -> None:
    cfg = pipeline_config()
    text = "[PERSONA_1] e Rossi e █████ Rossi"

    spans = replacement_spans(text, cfg)

    assert [text[s:e] for s, e in spans] == ["[PERSONA_1]", "█████"]
    assert len(find(text, "Rossi", _anchoring(), spans)) == 2
    assert find(text, "PERSONA", _anchoring(), spans) == []
    assert replacement_regex(cfg).fullmatch("[CODICE_FISCALE_12]")


def test_tag_category() -> None:
    cfg = pipeline_config()

    assert tag_category("[CODICE_FISCALE_2]", cfg) == "CODICE_FISCALE"
    assert tag_category("█████", cfg) is None
