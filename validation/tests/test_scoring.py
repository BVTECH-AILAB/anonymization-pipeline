"""Scoring of one anonymization result against the ground truth, and aggregation."""

from typing import Any

import pytest

from anonval.scoring import (
    active_categories,
    error_score,
    score_document,
    summarize,
    verdict,
)
from anonval.settings import Thresholds
from anonval.text import find
from anonymization_pipeline.pipeline_config.categories import Registry

CF = "BNCGLI78D52A944I"
TRANSCRIPT = (
    "Paziente: Giulia Bianchi\n"
    f"C.F. {CF}\n"
    "Diagnosi: morbo di Parkinson. Medico: Dott. Paolo Neri. Sesso: F"
)
ALL = {
    "PERSONA",
    "CODICE_FISCALE",
    "DIAGNOSI",
    "MEDICO",
    "SESSO",
    "FAMILIARE",
}


def _gold() -> dict[str, Any]:
    def entity(
        value: str, category: str, group: str, location: str = "paragraph"
    ) -> dict[str, Any]:
        return {
            "value": value,
            "category": category,
            "group": group,
            "family": category,
            "mentions": 1,
            "locations": [location],
            "pages": [1],
        }

    return {
        "id": "referto_laboratorio_facile_01",
        "type": "referto_laboratorio",
        "difficulty": "facile",
        "pages": 1,
        "entities": [
            entity("Giulia Bianchi", "PERSONA", "anagrafica", "patient_box"),
            entity(CF, "CODICE_FISCALE", "identificativi", "char_boxes"),
            entity("morbo di Parkinson", "DIAGNOSI", "sanitari"),
            entity("Paolo Neri", "MEDICO", "persone_correlate", "signature"),
            entity("F", "SESSO", "anagrafica"),
            entity("Mario Verdi", "FAMILIARE", "persone_correlate"),
        ],
        "negatives": [{"value": "Parkinson", "forbidden_categories": ["PERSONA"]}],
    }


def _result(
    tagged: list[tuple[str, str]],
    residuals: int = 0,
    sources: dict[str, list[str]] | None = None,
    transcript: str = TRANSCRIPT,
) -> dict[str, Any]:
    """Service result that redacts ``tagged``; every value found by the VLM unless ``sources``
    says otherwise."""
    occurrences: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    for i, (text, category) in enumerate(tagged, start=1):
        start, end = find(transcript, text)[-1]
        replacement = f"[{category}_{i}]"
        occurrences.append(
            {
                "original_start": start,
                "original_end": end,
                "original": text,
                "category": category,
                "replacement": replacement,
            }
        )
        entities.append(
            {
                "replacement": replacement,
                "category": category,
                "sources": (sources or {}).get(text, ["detection"]),
            }
        )
    markdown = transcript
    for o in sorted(occurrences, key=lambda o: -o["original_start"]):
        markdown = (
            markdown[: o["original_start"]]
            + f"[{o['category']}]"
            + markdown[o["original_end"] :]
        )
    return {
        "status": "completed",
        "duration_s": 12.5,
        "result": {
            "markdown": markdown,
            "transcript": transcript,
            "occurrences": occurrences,
            "report": {
                "entities": entities,
                "extraction": {"docling": [1], "vlm": []},
                "detection": {"hallucinations_discarded": 2, "eponyms_discarded": 1},
                "verification": {
                    "mode": "report",
                    "outcome": "residuals" if residuals else "ok",
                    "residuals": [{"category": "MEDICO", "page": 1}] * residuals,
                },
                "times_s": {"total": 11.0},
            },
        },
    }


TAGGED = [
    ("Giulia Bianchi", "PERSONA"),
    (CF, "CODICE_FISCALE"),
    ("Parkinson", "PERSONA"),
    ("Diagnosi", "DIAGNOSI"),
]


def _by_value(score: Any) -> dict[str, Any]:
    return {v.value: v for v in score.values}


def test_values_are_classified() -> None:
    score = score_document(_gold(), _result(TAGGED), ALL)
    values = _by_value(score)
    assert (
        values["Giulia Bianchi"].protected and values["Giulia Bianchi"].category_exact
    )
    assert values[CF].protected
    diagnosis = values["morbo di Parkinson"]
    assert diagnosis.detected and diagnosis.protected
    assert not diagnosis.category_family
    assert not values["Paolo Neri"].detected and not values["Paolo Neri"].protected
    assert not values["F"].protected  # short value: decided by coverage, not by search
    assert not values["Mario Verdi"].extracted


def test_precision_counts_and_negatives() -> None:
    score = score_document(_gold(), _result(TAGGED), ALL)
    assert score.tp == 3
    assert score.tp_category == 2
    assert [(h.text, h.category) for h in score.false_positives] == [
        ("Diagnosi", "DIAGNOSI")
    ]
    assert [(h.text, h.category) for h in score.negatives_violated] == [
        ("Parkinson", "PERSONA")
    ]
    assert score.leaked == ["Paolo Neri", "F"]


def test_family_category_counts_as_correct(families: Registry) -> None:
    score = score_document(_gold(), _result([("Paolo Neri", "PERSONA")]), ALL)
    neri = _by_value(score)["Paolo Neri"]
    assert neri.protected and neri.category_family and not neri.category_exact


def test_disabled_category_is_not_expected() -> None:
    active = ALL - {"PERSONA", "SESSO"}
    score = score_document(_gold(), _result(TAGGED), active)
    values = _by_value(score)
    assert not values["Giulia Bianchi"].expected
    assert values["Paolo Neri"].expected  # MEDICO stays active
    assert score.disabled_hits == 1  # Giulia Bianchi
    assert score.tp == 2  # CF, and Parkinson inside the expected diagnosis


def test_specific_category_falls_back_to_active_generic(families: Registry) -> None:
    score = score_document(_gold(), _result(TAGGED), ALL - {"MEDICO"})
    assert _by_value(score)["Paolo Neri"].expected


def test_active_categories_from_enable_and_disable() -> None:
    base = ["NOME", "PERSONA", "DIAGNOSI"]
    active = active_categories(base, enable=["SESSO"], disable=["sanitari", "NOME"])
    assert active == {"PERSONA", "SESSO"}
    with pytest.raises(ValueError):
        active_categories(base, enable=["GATTO"], disable=None)


def test_summary_and_verdict() -> None:
    good = score_document(
        _gold(), _result([*TAGGED, ("Paolo Neri", "MEDICO"), ("F", "SESSO")]), ALL
    )
    bad = score_document(_gold(), _result(TAGGED, residuals=1), ALL)
    failed = error_score(_gold(), "rejected", "residuals")
    summary = summarize([good, bad, failed])
    overall = summary["overall"]
    assert overall["documenti"] == {
        "totali": 3,
        "completati": 2,
        "respinti": 1,
        "errori": 0,
    }
    assert overall["valori"]["attesi"] == 10
    assert overall["valori"]["fughe"] == 2
    assert overall["documenti_con_fughe"] == 1
    assert overall["verificatore"] == {
        "fughe_segnalate": 1,
        "fughe_non_segnalate": 0,
        "falsi_allarmi": 0,
        "senza_fughe": 1,
    }
    assert summary["by_category"]["MEDICO"]["fughe"] == 1
    assert (
        summary["by_type_difficulty"]["referto_laboratorio"]["facile"]["attesi"] == 10
    )
    assert summary["by_location"]["signature"]["attesi"] == 2
    checks = verdict(overall, Thresholds(recall_min=0.5, tasso_fuga_max=0.0))
    assert checks["tasso_fuga_max"] is False
    assert checks["recall_min"] is True


def test_negative_inside_a_matching_gold_value_is_not_a_violation() -> None:
    gold = _gold()
    gold["entities"].append(
        {
            "value": "Paolo Neri",
            "category": "FAMILIARE",
            "group": "persone_correlate",
            "family": "PERSONA",
            "mentions": 1,
            "locations": ["paragraph"],
            "pages": [1],
        }
    )
    gold["negatives"] = [
        {"value": "Neri", "forbidden_categories": ["PERSONA"]},
        {"value": "Pao", "forbidden_categories": ["PERSONA"]},
    ]
    score = score_document(gold, _result([("Paolo Neri", "MEDICO")]), ALL)
    assert score.negatives_violated == []


def test_unmarked_copies_are_tolerated() -> None:
    gold = _gold()
    neri = next(e for e in gold["entities"] if e["value"] == "Paolo Neri")
    neri["unmarked"] = 1
    score = score_document(gold, _result(TAGGED), ALL)
    assert _by_value(score)["Paolo Neri"].protected
    neri["unmarked"] = 0
    score = score_document(gold, _result(TAGGED), ALL)
    assert not _by_value(score)["Paolo Neri"].protected


def test_replacement_tags_are_not_leaks() -> None:
    transcript = "Paziente in gravidanza alla 12a settimana"
    gold = {
        **_gold(),
        "entities": [
            {
                "value": "gravidanza",
                "category": "GRAVIDANZA",
                "group": "sanitari",
                "family": "GRAVIDANZA",
                "mentions": 1,
                "locations": ["paragraph"],
                "pages": [1],
            }
        ],
    }
    start = transcript.index("gravidanza")
    markdown = transcript.replace("gravidanza", "[GRAVIDANZA_1]")
    occurrence = {
        "original_start": start,
        "original_end": start + 10,
        "original": "gravidanza",
        "category": "GRAVIDANZA",
    }
    for tags in ([{"start": start, "end": start + 14}], None):
        result: dict[str, Any] = {
            "markdown": markdown,
            "transcript": transcript,
            "occurrences": [occurrence],
            "report": {},
        }
        if tags is not None:
            result["tags"] = tags
        score = score_document(
            gold, {"status": "completed", "result": result}, {"GRAVIDANZA"}
        )
        assert score.values[0].protected


def test_sources_of_redactions() -> None:
    sources = {"Giulia Bianchi": ["labels", "detection"], "Diagnosi": ["ner"]}
    score = score_document(_gold(), _result(TAGGED, sources=sources), ALL)
    values = _by_value(score)
    assert values["Giulia Bianchi"].sources == ["detection", "labels"]
    assert values["Paolo Neri"].sources == []
    (fp,) = score.false_positives
    assert (fp.text, fp.category, fp.sources, fp.page) == (
        "Diagnosi",
        "DIAGNOSI",
        ["ner"],
        1,
    )
    (negative,) = score.negatives_violated
    assert (negative.text, negative.sources) == ("Parkinson", ["detection"])


def test_pages_of_values_and_leaks() -> None:
    transcript = (
        "Paziente: Giulia Bianchi\n\n<!-- pagina 2 -->\n\nFirma: Dott. Paolo Neri"
    )
    score = score_document(
        _gold(), _result([("Giulia Bianchi", "PERSONA")], transcript=transcript), ALL
    )
    values = _by_value(score)
    assert values["Giulia Bianchi"].pages == [1]
    assert values["Giulia Bianchi"].leak_pages == []
    assert values["Paolo Neri"].pages == [2]
    assert values["Paolo Neri"].leak_pages == [2]


def _with_layout(job: dict[str, Any], pages: list[str] | None) -> dict[str, Any]:
    job["result"]["layout_pages"] = pages
    return job


def test_layout_pdf_leaks() -> None:
    tagged = [*TAGGED, ("Paolo Neri", "MEDICO"), ("F", "SESSO")]
    layout = [
        "Paziente: [PERSONA_1]\nC.F. [CODICE_FISCALE_2]",
        "Diagnosi: morbo di [PERSONA_3]. Medico: Dott. Paolo Neri. Sesso: [SESSO_5]",
    ]
    score = score_document(_gold(), _with_layout(_result(tagged), layout), ALL)
    values = _by_value(score)
    assert score.layout_checked
    assert values["Paolo Neri"].protected  # censored in the Markdown...
    assert (
        values["Paolo Neri"].layout_protected is False
    )  # ...but not in the layout PDF
    assert values["Paolo Neri"].layout_leak_pages == [2]
    assert values["Giulia Bianchi"].layout_protected is True
    assert (
        values["F"].layout_protected is None
    )  # short values are not checked in the PDF
    assert score.layout_leaked == ["Paolo Neri"]


def test_layout_not_requested_or_not_produced() -> None:
    score = score_document(_gold(), _result(TAGGED), ALL)
    assert not score.layout_checked
    assert all(v.layout_protected is None for v in score.values)
    assert score.layout_leaked == []


def test_layout_leaks_in_summary_and_verdict() -> None:
    tagged = [*TAGGED, ("Paolo Neri", "MEDICO"), ("F", "SESSO")]
    clean = score_document(_gold(), _result(tagged), ALL)
    leaky = score_document(
        _gold(), _with_layout(_result(tagged), ["Giulia Bianchi " + CF]), ALL
    )
    overall = summarize([clean, leaky])["overall"]
    assert overall["layout"] == {
        "documenti_verificati": 1,
        "pdf_non_prodotti": 0,
        "valori_verificati": 4,  # extracted, expected, at least 3 letters and digits
        "fughe": 2,
        "tasso_fuga": 0.5,
        "documenti_con_fughe": 1,
        "somiglianza_media": None,
        "somiglianza_minima": None,
    }
    checks = verdict(overall, Thresholds(tasso_fuga_max=0.0))
    assert checks["tasso_fuga_max"] is True
    assert checks["tasso_fuga_layout_max"] is False
    assert (
        verdict(summarize([clean])["overall"], Thresholds())["tasso_fuga_layout_max"]
        is None
    )


def test_layout_requested_but_not_produced() -> None:
    job = _result(TAGGED)
    job["result"]["report"]["layout"] = "unavailable"
    score = score_document(_gold(), job, ALL)
    assert score.layout_missing and not score.layout_checked
    layout = summarize([score])["overall"]["layout"]
    assert layout["pdf_non_prodotti"] == 1
    assert layout["tasso_fuga"] is None


def test_layout_similarity_in_summary() -> None:
    first = _with_layout(_result(TAGGED), ["", ""])
    first["result"]["layout_similarity"] = [0.9, 0.8]
    second = _with_layout(_result(TAGGED), [""])
    second["result"]["layout_similarity"] = [1.0]

    scores = [score_document(_gold(), job, ALL) for job in (first, second)]

    assert scores[0].layout_similarity == [0.9, 0.8]
    layout = summarize(scores)["overall"]["layout"]
    assert layout["somiglianza_media"] == 0.9 and layout["somiglianza_minima"] == 0.8
