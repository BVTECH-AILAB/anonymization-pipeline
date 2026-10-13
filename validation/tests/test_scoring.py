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


def _result(tagged: list[tuple[str, str]], residuals: int = 0) -> dict[str, Any]:
    occurrences: list[dict[str, Any]] = []
    for text, category in tagged:
        start, end = find(TRANSCRIPT, text)[-1]
        occurrences.append(
            {
                "original_start": start,
                "original_end": end,
                "original": text,
                "category": category,
            }
        )
    markdown = TRANSCRIPT
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
            "transcript": TRANSCRIPT,
            "occurrences": occurrences,
            "report": {
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
    assert score.false_positives == [("Diagnosi", "DIAGNOSI")]
    assert score.negatives_violated == [("Parkinson", "PERSONA")]
    assert score.leaked == ["Paolo Neri", "F"]


def test_family_category_counts_as_correct() -> None:
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


def test_specific_category_falls_back_to_active_generic() -> None:
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
