from collections import Counter

import pytest
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from anonymization_pipeline.domain import Entity
from anonymization_pipeline.layout import censor as layout_censor
from anonymization_pipeline.layout.align import ClearField, align
from anonymization_pipeline.layout.model import Layout, LayoutError
from anonymization_pipeline.layout.pdf import extract
from anonymization_pipeline.redaction import group_entities, redact, structure
from anonymization_pipeline.redaction.structure import Censored, StructureLeak
from tests.layout import HEIGHT, LINES, WIDTH, Line, X, structure_of, styled_pdf
from tests.pipeline import pipeline_config

CF = "RSSMRA85M01H501U"
VALUES = [Entity("Mario Rossi", "PERSONA", 0), Entity(CF, "CODICE_FISCALE", 0)]


def _censored(clear: DoclingDocument, entities: list[Entity] = VALUES) -> Censored:
    cfg = pipeline_config()
    groups = group_entities(entities)
    redact([t for _, t in structure.fields(clear)], groups, cfg)
    return structure.censor(clear, groups, cfg)


def _aligned(
    lines: list[Line] = LINES, clear: DoclingDocument | None = None
) -> tuple[DoclingDocument, dict, Layout, Censored, Counter[str]]:
    clear = clear or structure_of(lines)
    extraction = extract(styled_pdf(lines), {1}, graphics=True)
    counts: Counter[str] = Counter()
    fields = align(clear, extraction, counts)
    censored = _censored(clear)
    layout = layout_censor.censor(fields, extraction, censored, counts)
    return clear, fields, layout, censored, counts


def _xs(field: ClearField) -> list[tuple[int, int, float]]:
    return [(r.start, r.end, round(r.xs[0], 2)) for r in field.runs]


def test_every_word_of_a_field_gets_its_glyph_positions() -> None:
    _, fields, _, _, counts = _aligned()

    patient = fields[("#/texts/1", None)]
    assert [(s, e) for s, e, _ in _xs(patient)] == [(0, 9), (10, 15), (16, 21)]
    assert patient.runs[0].xs[0] == X and patient.runs[0].baseline == 770
    assert all(r.xs == sorted(r.xs) for r in patient.runs)
    assert len(fields) == len(LINES) and counts["fields_fallback"] == 0


def test_a_value_becomes_one_tag_run_in_its_box() -> None:
    _, fields, layout, censored, counts = _aligned()

    layout_censor.check(layout, censored)
    patient = next(f for f in layout.fields if f.ref == "#/texts/1")
    text = censored.document.texts[1].text
    assert text == "Paziente: [PERSONA_1]"
    assert [(text[r.start : r.end], r.tag) for r in patient.runs] == [
        ("Paziente:", False),
        ("[PERSONA_1]", True),
    ]
    clear = fields[("#/texts/1", None)].runs
    tag = patient.runs[1]
    assert tag.x == clear[1].xs[0]
    assert tag.x + tag.width == pytest.approx(clear[2].xs[-1])
    assert counts["fields_styled"] == len(LINES)
    assert layout.pages[0].source == "pdf" and len(layout.pages[0].graphics) == 2


def test_a_value_inside_a_word_keeps_the_rest_of_the_word() -> None:
    lines = [Line("CF:" + CF + " ok", 700)]

    _, _, layout, censored, _ = _aligned(lines)

    layout_censor.check(layout, censored)
    runs = layout.fields[0].runs
    text = censored.document.texts[0].text
    assert [(text[r.start : r.end], r.tag) for r in runs] == [
        ("CF:", False),
        ("[CODICE_FISCALE_1]", True),
        ("ok", False),
    ]
    assert runs[0].x == X and runs[1].x > X


def test_a_value_on_two_lines_keeps_only_the_box_of_its_first_line() -> None:
    lines = [Line("Medico curante Mario", 700), Line("Rossi in visita", 685)]
    clear = DoclingDocument(name="fake")
    clear.add_page(1, Size(width=WIDTH, height=HEIGHT))
    clear.add_text(
        DocItemLabel.TEXT,
        "Medico curante Mario Rossi in visita",
        prov=ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(
                l=X, t=712, r=300, b=682, coord_origin=CoordOrigin.BOTTOMLEFT
            ),
            charspan=(0, 36),
        ),
    )

    _, fields, layout, censored, _ = _aligned(lines, clear)

    layout_censor.check(layout, censored)
    runs = layout.fields[0].runs
    text = censored.document.texts[0].text
    assert [(text[r.start : r.end], r.baseline) for r in runs] == [
        ("Medico", 700),
        ("curante", 700),
        ("[PERSONA_1]", 700),
        ("in", 685),
        ("visita", 685),
    ]
    mario = fields[("#/texts/0", None)].runs[2]
    assert (runs[2].x, runs[2].width) == (mario.xs[0], mario.xs[-1] - mario.xs[0])


def test_a_field_that_does_not_match_the_pdf_keeps_no_layout() -> None:
    clear = structure_of(LINES)
    clear.texts[3].text = clear.texts[3].orig = "Testo che nel PDF non esiste affatto"

    _, fields, layout, censored, counts = _aligned(LINES, clear)

    assert ("#/texts/3", None) not in fields
    assert counts["fields_fallback"] == 1
    layout_censor.check(layout, censored)


def _tampered(layout: Layout, **update: object) -> Layout:
    field = layout.fields[1]
    return layout.model_copy(
        update={
            "fields": [
                *layout.fields[:1],
                field.model_copy(update=update),
                *layout.fields[2:],
            ]
        }
    )


def test_check_fails_closed() -> None:
    _, _, layout, censored, _ = _aligned()
    runs = layout.fields[1].runs
    untagged = [r.model_copy(update={"tag": False}) for r in runs]
    beyond = [runs[0].model_copy(update={"end": 500}), *runs[1:]]

    for bad in (
        _tampered(layout, ref="#/texts/99"),
        _tampered(layout, page_no=2),
        _tampered(layout, runs=untagged),
        _tampered(layout, runs=[runs[0]]),
        _tampered(layout, runs=beyond),
        _tampered(layout, runs=[*runs, runs[1].model_copy(update={"tag": False})]),
        layout.model_copy(update={"fields": [*layout.fields, layout.fields[0]]}),
        layout.model_copy(update={"styles": []}),
    ):
        with pytest.raises(StructureLeak):
            layout_censor.check(bad, censored)


def test_restore_puts_the_values_back_in_their_boxes() -> None:
    clear, _, layout, censored, _ = _aligned()

    restored = layout_censor.restore(layout, censored.elements)

    original = dict(structure.fields(clear))
    patient = next(f for f in restored.fields if f.ref == "#/texts/1")
    text = original[("#/texts/1", None)]
    assert [(text[r.start : r.end], r.tag) for r in patient.runs] == [
        ("Paziente:", False),
        ("Mario Rossi", True),
    ]
    assert patient.runs[1].x == layout.fields[1].runs[1].x
    with pytest.raises(LayoutError):
        layout_censor.restore(layout, [])


def test_table_cells_are_aligned_one_by_one() -> None:
    lines = [Line("Glicemia", 600), Line("Mario Rossi", 580)]
    clear = DoclingDocument(name="fake")
    clear.add_page(1, Size(width=WIDTH, height=HEIGHT))
    cells = [
        TableCell(
            text=text,
            bbox=BoundingBox(
                l=X, t=y + 12, r=200, b=y - 3, coord_origin=CoordOrigin.BOTTOMLEFT
            ),
            start_row_offset_idx=row,
            end_row_offset_idx=row + 1,
            start_col_offset_idx=0,
            end_col_offset_idx=1,
        )
        for row, (text, y) in enumerate([("Glicemia", 600), ("Mario Rossi", 580)])
    ]
    clear.add_table(
        TableData(table_cells=cells, num_rows=2, num_cols=1),
        prov=ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(
                l=X, t=615, r=200, b=575, coord_origin=CoordOrigin.BOTTOMLEFT
            ),
            charspan=(0, 0),
        ),
    )

    _, fields, layout, censored, _ = _aligned(lines, clear)

    assert set(fields) == {("#/tables/0", 0), ("#/tables/0", 1)}
    layout_censor.check(layout, censored)
    name = next(f for f in layout.fields if f.cell == 1)
    assert [(r.start, r.end, r.tag, r.baseline) for r in name.runs] == [
        (0, 11, True, 580)
    ]
