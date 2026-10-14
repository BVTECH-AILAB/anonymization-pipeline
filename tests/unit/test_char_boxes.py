import pytest
from docling_core.types.doc import ContentLayer, DoclingDocument, PictureItem, TextItem

from anonymization_pipeline import metrics
from anonymization_pipeline.extraction import char_boxes
from tests.layout import BOX_ROWS, CF, TEAM, Boxes, boxed_pdf, boxed_structure

LAYERS = {ContentLayer.BODY, ContentLayer.FURNITURE}


def _markdown(doc: DoclingDocument) -> str:
    return doc.export_to_markdown(included_content_layers=LAYERS)


def _texts(doc: DoclingDocument) -> list[str]:
    """Texts in reading order (an inserted text is at the end of ``doc.texts``)."""
    return [
        item.text
        for item, _ in doc.iterate_items(
            included_content_layers=LAYERS, traverse_pictures=True
        )
        if isinstance(item, TextItem)
    ]


def _cells(doc: DoclingDocument) -> list[list[str]]:
    return [[c.text for c in t.data.table_cells] for t in doc.tables]


@pytest.mark.parametrize("missing", [0, 3])
def test_paragraph_boxes_become_one_text(missing: int) -> None:
    doc = boxed_structure(missing=missing)

    areas = char_boxes.compact(doc, boxed_pdf())

    assert _texts(doc) == [
        "Codice fiscale",
        CF,
        "Scala del dolore",
        *"0123456789"[: 10 - missing],
    ]
    assert f"Codice fiscale\n\n{CF}" in _markdown(doc)
    merged = next(t for t in doc.texts if t.text == CF)
    bbox = merged.prov[0].bbox.to_bottom_left_origin(842)
    assert bbox.l == pytest.approx(130 + 6 - 3, abs=1.5)  # the first character...
    assert bbox.r == pytest.approx(
        130 + 15 * 12 + 9, abs=1.5
    )  # ...to the last, also when lost
    # the frames of the 16 boxes, not the label and not the scale
    ((left, bottom, right, top),) = areas[1]
    assert left <= 130 and right >= 130 + 16 * 12 and bottom <= 696 and top >= 712
    assert right < 130 + 17 * 12 and left > 130 - 12


def test_cells_become_one_cell() -> None:
    doc = boxed_structure(kind="cells", missing=2)

    char_boxes.compact(doc, boxed_pdf())

    cells = _cells(doc)
    assert cells[0] == [CF] + [""] * 13
    assert cells[1] == list("01234567")  # not an identifier: as it was
    assert CF in _markdown(doc)


def test_boxes_inside_a_picture_come_out_of_it() -> None:
    doc = boxed_structure(kind="picture")

    char_boxes.compact(doc, boxed_pdf())

    assert CF in _markdown(doc)
    assert len(doc.pictures) == 1  # the picture of the scale stays, its text inside it
    assert [
        t.text
        for t in doc.texts
        if t.parent is not None and isinstance(t.parent.resolve(doc), PictureItem)
    ] == list("0123456789")


def test_two_rows_on_the_same_line_stay_apart() -> None:
    rows = [Boxes("CF", CF, 80, 700, 10), Boxes("TEAM", TEAM, 300, 700, 10)]
    doc = boxed_structure(rows)

    areas = char_boxes.compact(doc, boxed_pdf(rows))

    assert [t for t in _texts(doc) if len(t) > 4] == [CF, TEAM]
    assert len(areas[1]) == 2


def test_nothing_to_compact_and_counters() -> None:
    doc = boxed_structure([BOX_ROWS[1]])
    before = _markdown(doc)
    collector = metrics.start()
    try:
        assert char_boxes.compact(doc, boxed_pdf([BOX_ROWS[1]])) == {}
        assert _markdown(doc) == before
        assert collector is not None
        assert collector.counters == {"extraction.char_rows_skipped": 1}
        char_boxes.compact(boxed_structure(), boxed_pdf())
        assert collector.counters["extraction.char_rows_compacted"] == 1
    finally:
        metrics.start(enabled=False)
