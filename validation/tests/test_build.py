"""Ground truth built from the mentions and the PDF text."""

import pytest

from anonval.build import ground_truth, rubric_problems
from anonval.spec import Mention, Spec
from anonymization_pipeline.pipeline_config.categories import Registry

SPEC = Spec.model_validate(
    {
        "id": "nota_ambulatoriale_facile_01",
        "type": "nota_ambulatoriale",
        "difficulty": "facile",
        "letterhead": {"lines": ["Ambulatorio"]},
        "blocks": [{"type": "paragraph", "text": "x"}],
    }
)


def test_entries_pages_and_unmarked_copies(families: Registry) -> None:
    mentions = [
        Mention("Rosa Neri", "PERSONA", "patient_box"),
        Mention("Rosa Neri", "PERSONA", "footer"),
        Mention("Rosa", "NOME", "paragraph"),
    ]
    texts = ["Paziente Rosa Neri, colorito rosa", "Rosa Neri - pag. 2"]
    gold = ground_truth(
        SPEC, mentions, texts, masked="Paziente #, colorito rosa # - pag. 2"
    )
    entries = {e["value"]: e for e in gold["entities"]}
    assert entries["Rosa Neri"]["mentions"] == 2
    assert entries["Rosa Neri"]["locations"] == ["patient_box", "footer"]
    assert entries["Rosa Neri"]["pages"] == [1, 2]
    assert entries["Rosa Neri"]["unmarked"] == 0
    assert entries["Rosa"]["unmarked"] == 1
    assert entries["Rosa"]["family"] == "PERSONA"


def test_value_missing_from_the_pdf_is_an_error() -> None:
    with pytest.raises(ValueError, match="Mario Verdi"):
        ground_truth(SPEC, [Mention("Mario Verdi", "PERSONA", "paragraph")], ["altro"])


def test_rubric() -> None:
    gold = {"pages": 2, "entities": [{"group": "anagrafica"}] * 5}
    assert len(rubric_problems("facile", gold)) == 2
