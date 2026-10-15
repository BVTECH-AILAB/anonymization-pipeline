"""Locating ground truth values in a PDF and scoring the boxes of the service against them."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from reportlab.pdfgen import canvas

from anonval.locate import PageText, locate, page_texts
from anonval.scoring import active_categories, expected, score_document
from anonymization_pipeline.pdf.geometry import union_all
from anonymization_pipeline.pipeline_config.categories import Registry

HEIGHT = 842


def _pdf(lines: list[tuple[float, float, str]], comb: str | None = None) -> bytes:
    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=(595, HEIGHT))
    cv.setFont("Helvetica", 10)
    for x, y, text in lines:
        cv.drawString(x, HEIGHT - y, text)
    if comb:  # one character per box, like a form
        for i, char in enumerate(comb):
            cv.rect(40 + i * 14, HEIGHT - 312, 12, 14)
            cv.drawString(43 + i * 14, HEIGHT - 308, char)
    cv.save()
    return buf.getvalue()


@pytest.fixture
def pages() -> list[PageText]:
    return page_texts(
        _pdf(
            [
                (40, 100, "Paziente: Mario Rossi"),
                (40, 130, "Sindrome di Apgar, colorito rosa"),
                (40, 160, "Rosa Neri, figlia"),
                (40, 190, "Ospedale Maggiore"),
            ],
            comb="RSSMRA85M01H501Q",
        )
    )


def _box(
    pages: list[PageText], value: str, category: str, page: int = 1
) -> dict[str, Any]:
    (occurrence,) = locate(pages, value)[:1]
    return {
        "page": page,
        "rect": list(union_all(list(occurrence.chars))),
        "category": category,
    }


def _status(boxes: list[dict[str, Any]], status: str = "done") -> dict[str, Any]:
    return {"status": status, "result": {"boxes": boxes, "timings": {"total": 0.5}}}


def _gold(
    entities: list[dict[str, Any]], negatives: list[dict[str, Any]] | None = None
) -> dict:
    return {
        "id": "nota_ambulatoriale_facile_01",
        "type": "nota_ambulatoriale",
        "difficulty": "facile",
        "entities": entities,
        "negatives": negatives or [],
    }


def test_values_are_located_with_their_character_boxes(pages: list[PageText]) -> None:
    (rossi,) = locate(pages, "Mario Rossi")
    (comb,) = locate(pages, "RSSMRA85M01H501Q")

    assert rossi.page == 0 and len(rossi.chars) == len("MarioRossi")
    assert len(comb.chars) == 16  # one box per character, still one occurrence
    assert locate(pages, "Giuseppe Verdi") == []


def test_detected_protected_and_leaked_values(pages: list[PageText]) -> None:
    gold = _gold(
        [
            {"value": "Mario Rossi", "category": "PERSONA"},
            {"value": "RSSMRA85M01H501Q", "category": "CODICE_FISCALE"},
            {"value": "Ospedale Maggiore", "category": "STRUTTURA_SANITARIA"},
        ]
    )
    boxes = [
        _box(pages, "Mario Rossi", "PERSONA"),
        _box(pages, "RSSMRA85M01H501Q", "CODICE_FISCALE"),
    ]

    score = score_document(gold, pages, _status(boxes), {"PERSONA", "CODICE_FISCALE"})

    values = {v.value: v for v in score.values}
    assert values["Mario Rossi"].detected and values["Mario Rossi"].protected
    assert values["RSSMRA85M01H501Q"].coverage == 1.0
    assert not values["Ospedale Maggiore"].expected  # its category is off
    assert not any(v.leaked for v in score.values)
    assert (score.correct_boxes, score.false_positives) == (2, [])


def test_a_partly_covered_value_is_detected_but_leaked(pages: list[PageText]) -> None:
    gold = _gold([{"value": "Mario Rossi", "category": "PERSONA"}])
    box = _box(pages, "Rossi", "PERSONA")

    (value,) = score_document(gold, pages, _status([box]), {"PERSONA"}).values

    assert value.coverage == pytest.approx(0.5)
    assert value.detected and value.leaked


def test_unmarked_copies_are_tolerated(pages: list[PageText]) -> None:
    gold = _gold([{"value": "Rosa", "category": "NOME", "unmarked": 1}])
    box = _box(pages, "Rosa Neri", "PERSONA", page=1)
    # "rosa" the colour stays in clear text, "Rosa" the name is covered

    (value,) = score_document(gold, pages, _status([box]), {"NOME"}).values

    assert value.occurrences == 2 and value.protected


def test_false_positives_and_disabled_boxes(pages: list[PageText]) -> None:
    gold = _gold(
        [
            {"value": "Mario Rossi", "category": "PERSONA"},
            {"value": "Ospedale Maggiore", "category": "STRUTTURA_SANITARIA"},
        ]
    )
    boxes = [
        _box(pages, "Ospedale Maggiore", "STRUTTURA_SANITARIA"),
        _box(pages, "Sindrome", "PERSONA"),
    ]

    score = score_document(gold, pages, _status(boxes), {"PERSONA"})

    assert score.disabled_boxes == 1
    assert [h.category for h in score.false_positives] == ["PERSONA"]


def test_negatives_are_violated_only_outside_values_of_their_family(
    pages: list[PageText], families: Registry
) -> None:
    negatives = [
        {"value": "Apgar", "forbidden_categories": ["PERSONA"]},
        {"value": "Neri", "forbidden_categories": ["PERSONA"]},
    ]
    gold = _gold([{"value": "Rosa Neri", "category": "FAMILIARE"}], negatives)
    boxes = [_box(pages, "Apgar", "PERSONA"), _box(pages, "Rosa Neri", "FAMILIARE")]

    score = score_document(gold, pages, _status(boxes), {"PERSONA", "FAMILIARE"})

    assert [h.value for h in score.negatives_violated] == ["Apgar"]


def test_blocked_documents_are_not_scored(pages: list[PageText]) -> None:
    gold = _gold([{"value": "Mario Rossi", "category": "PERSONA"}])
    status = {"status": "blocked", "reason": "ocr_unavailable", "result": {"boxes": []}}

    score = score_document(gold, pages, status, {"PERSONA"})

    assert (
        not score.released and score.values == [] and score.reason == "ocr_unavailable"
    )


def test_values_left_in_the_text_of_the_released_pdf(pages: list[PageText]) -> None:
    gold = _gold([{"value": "Mario Rossi", "category": "PERSONA"}])
    released = page_texts(_pdf([(40, 100, "Paziente: Mario Rossi")]))

    score = score_document(gold, pages, _status([]), {"PERSONA"}, released)

    assert score.text_leaks == ["Mario Rossi"]


def test_active_categories_and_generic_fallback(families: Registry) -> None:
    catalog = [
        {"category": "PERSONA", "default": True},
        {"category": "MEDICO", "default": False},
        {"category": "DATA", "default": False},
        {"category": "ETA", "default": True},
    ]

    active = active_categories(catalog, ["DATA"], ["ETA"])

    assert active == {"PERSONA", "DATA"}
    assert expected("MEDICO", active)  # redacted as PERSONA


def _dataset_document(doc_id: str) -> tuple[dict[str, Any], list[PageText]]:
    root = Path(__file__).resolve().parents[1] / "dataset"
    entry = next(
        e
        for e in json.loads((root / "manifest.json").read_text())["documents"]
        if e["id"] == doc_id
    )
    gold = json.loads((root / entry["ground_truth"]).read_text())
    return gold, page_texts((root / entry["pdf"]).read_bytes())


@pytest.mark.parametrize(
    "doc_id", ["referto_laboratorio_facile_01", "lettera_dimissione_complessa_01"]
)
def test_a_perfect_service_scores_full_marks_and_an_idle_one_zero(doc_id: str) -> None:
    gold, pages = _dataset_document(doc_id)
    active = {e["category"] for e in gold["entities"]}
    perfect = [
        {"page": o.page + 1, "rect": list(r), "category": e["category"]}
        for e in gold["entities"]
        for o in locate(pages, e["value"])
        for r in o.chars
    ]

    best = score_document(gold, pages, _status(perfect), active)
    idle = score_document(gold, pages, _status([]), active)

    assert all(v.located for v in best.values)
    assert all(v.detected and v.protected for v in best.values)
    assert best.false_positives == [] and best.correct_boxes == len(perfect)
    assert not any(v.detected for v in idle.values)
    assert sum(v.leaked for v in idle.values) == len(idle.values)
