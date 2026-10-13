import json

import pytest
from docling_core.types.doc import (
    BoundingBox,
    ContentLayer,
    CoordOrigin,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from anonymization_pipeline.domain import Document, Entity, Occurrence, Page
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction import group_entities, redact
from anonymization_pipeline.redaction.structure import (
    FLOW_MARGIN,
    Censored,
    StructureError,
    StructureLeak,
    build_structure,
    canonical,
    censor,
    check,
    fields,
    load,
    map_section,
    restore,
)
from tests.pipeline import pipeline_config

CF = "RSSMRA85M01H501Q"


def _prov(page: int, top: float, text: str = "") -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page,
        bbox=BoundingBox(
            l=50, t=top, r=400, b=top - 14, coord_origin=CoordOrigin.BOTTOMLEFT
        ),
        charspan=(0, len(text)),
    )


def _docling() -> DoclingDocument:
    """Page 1 of a referto: title, patient line, a table, a picture with text inside, hidden text,
    a page header; page 2 is a scan (no items)."""
    doc = DoclingDocument(name="Rossi Mario referto")
    doc.add_page(1, Size(width=595, height=842))
    doc.add_page(2, Size(width=595, height=842))
    doc.add_title("Referto", prov=_prov(1, 800, "Referto"))
    line = "Paziente: Mario Rossi"
    patient = doc.add_text(DocItemLabel.TEXT, line, prov=_prov(1, 760, line))
    patient.hyperlink = "https://example.org/Mario-Rossi"
    cells = [
        TableCell(
            text=t,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
        )
        for r, c, t in [(0, 0, "Codice fiscale"), (0, 1, CF)]
    ]
    doc.add_table(
        TableData(table_cells=cells, num_rows=1, num_cols=2),
        prov=_prov(1, 700),
    )
    picture = doc.add_picture(prov=_prov(1, 600))
    doc.add_text(
        DocItemLabel.TEXT, "Firma Anna Bianchi", prov=_prov(1, 590), parent=picture
    )
    doc.add_text(
        DocItemLabel.TEXT,
        "Anna Bianchi",
        prov=_prov(1, 500),
        content_layer=ContentLayer.INVISIBLE,
    )
    doc.add_text(
        DocItemLabel.PAGE_HEADER,
        "Ospedale",
        prov=_prov(1, 830, "Ospedale"),
        content_layer=ContentLayer.FURNITURE,
    )
    return doc


def _document(scan: str = "Firma: Mario Rossi") -> Document:
    return Document(
        kind="pdf",
        pages=[
            Page(index=0, text="unused", extracted_with="docling"),
            Page(index=1, text=scan, extracted_with="vlm"),
        ],
        docling=_docling(),
    )


def _all_strings(doc: DoclingDocument) -> str:
    return json.dumps(doc.export_to_dict(), ensure_ascii=False)


def test_structure_keeps_only_what_the_markdown_holds() -> None:
    structure = build_structure(_document(), pipeline_config())

    texts = [t for _, t in fields(structure)]
    assert texts == [
        "Referto",
        "Paziente: Mario Rossi",
        "Ospedale",
        "Firma: Mario Rossi",
        "Codice fiscale",
        CF,
    ]
    everything = _all_strings(structure)
    # text inside the picture, hidden text, file name and hyperlink never reach the structure
    for absent in ("Anna Bianchi", "example.org", "referto"):
        assert absent not in everything
    assert structure.name == "documento" and structure.origin is None
    assert len(structure.pictures) == 1 and structure.pictures[0].image is None
    # the scanned page becomes one block of flowing text inside the page margins
    flow = structure.texts[-1]
    assert flow.prov[0].page_no == 2
    assert flow.prov[0].bbox.l == FLOW_MARGIN


def test_page_headers_follow_the_furniture_option() -> None:
    cfg = pipeline_config(
        {"phases": {"extraction": {"native_pdf": {"docling": {"furniture": False}}}}}
    )

    structure = build_structure(_document(), cfg)

    assert "Ospedale" not in [t for _, t in fields(structure)]


def _censored(
    mode: str = "pseudonym",
) -> tuple[PipelineConfig, DoclingDocument, Censored, str, list[Occurrence]]:
    cfg = pipeline_config(default_mode=mode)
    doc = _document()
    groups = group_entities(
        [
            Entity("Mario Rossi", "PERSONA", 0),
            Entity(CF, "CODICE_FISCALE", 0),
        ]
    )
    markdown = (
        "# Referto\n\nPaziente: Mario Rossi\n\n| Codice fiscale | "
        + CF
        + " |\n\nFirma: Mario Rossi\n\nOspedale"
    )
    pages, occurrences = redact([markdown], groups, cfg)
    structure = build_structure(doc, cfg)
    return cfg, structure, censor(structure, groups, cfg), pages[0], occurrences[0]


def test_censored_structure_uses_the_markdown_tags() -> None:
    cfg, structure, censored, markdown, occurrences = _censored()

    texts = [t for _, t in fields(censored.document)]
    assert texts == [
        "Referto",
        "Paziente: [PERSONA_1]",
        "Ospedale",
        "Firma: [PERSONA_1]",
        "Codice fiscale",
        "[CODICE_FISCALE_1]",
    ]
    assert "[PERSONA_1]" in markdown and "[CODICE_FISCALE_1]" in markdown
    everything = _all_strings(censored.document)
    assert "Mario" not in everything and CF not in everything
    # orig follows text: the untreated representation would bring the value back
    assert all(t.orig == t.text for t in censored.document.texts)
    assert censored.marks[("#/texts/1", None)] == [(10, 21)]
    assert {e["originale"] for e in censored.elements} == {"Mario Rossi", CF}
    check(censored, markdown, occurrences, cfg)  # nothing to report
    # the clear structure is untouched
    assert "Mario Rossi" in _all_strings(structure)


def test_check_fails_closed_on_text_the_markdown_does_not_hold() -> None:
    cfg, _, censored, markdown, occurrences = _censored()
    censored.document.texts[0].text = "Referto di Giuseppe Verdi"

    with pytest.raises(StructureLeak):
        check(censored, markdown, occurrences, cfg)


def test_check_fails_closed_on_a_value_left_in_clear_text() -> None:
    cfg, _, censored, markdown, occurrences = _censored()
    # the words are all in the Markdown, but the value is whole again
    censored.texts[0] = "Referto Mario Rossi"

    with pytest.raises(StructureLeak):
        check(censored, markdown + " Mario Rossi", occurrences, cfg)


def test_restore_rebuilds_the_original_structure() -> None:
    _, structure, censored, _, _ = _censored()
    content = map_section(structure, censored)

    loaded = load(canonical(censored.document))
    restored = restore(loaded, content)

    assert canonical(restored) == canonical(structure)


def test_restore_in_redaction_mode() -> None:
    _, structure, censored, _, _ = _censored("redaction")
    assert "█████" in _all_strings(censored.document)

    restored = restore(censored.document, map_section(structure, censored))

    assert canonical(restored) == canonical(structure)


def test_restore_refuses_a_modified_structure_or_a_map_without_structure() -> None:
    _, structure, censored, _, _ = _censored()
    content = map_section(structure, censored)
    changed = censored.document.model_copy(deep=True)
    changed.texts[0].text = "Altro"

    with pytest.raises(StructureError, match="modificata"):
        restore(changed, content)
    with pytest.raises(StructureError, match="non contiene"):
        restore(censored.document, {"occorrenze": []})
    tampered = {
        **content,
        "elementi": [{**content["elementi"][0], "ref": "#/texts/99"}],
    }
    with pytest.raises(StructureError, match="non corrisponde"):
        restore(censored.document, tampered)
    with pytest.raises(StructureError, match="non valida"):
        load(b'{"not": "a document"}')


def test_structure_keeps_the_tree_lists_headings_and_captions() -> None:
    doc = DoclingDocument(name="x")
    doc.add_page(1, Size(width=595, height=842))
    doc.add_heading("Anamnesi", level=2, prov=_prov(1, 800, "Anamnesi"))
    group = doc.add_list_group()
    doc.add_list_item("Mario Rossi", parent=group, prov=_prov(1, 780, "Mario Rossi"))
    caption = doc.add_text(DocItemLabel.CAPTION, "Tabella 1", prov=_prov(1, 650))
    cell = TableCell(
        text="x",
        start_row_offset_idx=0,
        end_row_offset_idx=1,
        start_col_offset_idx=0,
        end_col_offset_idx=1,
    )
    doc.add_table(
        TableData(table_cells=[cell], num_rows=1, num_cols=1),
        caption=caption,
        prov=_prov(1, 700),
    )
    doc.add_code("print()", prov=_prov(1, 600, "print()"))
    doc.add_formula("E=mc2", prov=_prov(1, 550, "E=mc2"))
    document = Document(
        kind="pdf", pages=[Page(index=0, extracted_with="docling")], docling=doc
    )

    structure = build_structure(document, pipeline_config())

    labels = [(t.label.value, t.text) for t in structure.texts]
    assert ("section_header", "Anamnesi") in labels
    assert ("list_item", "Mario Rossi") in labels
    assert labels.count(("caption", "Tabella 1")) == 1
    assert ("code", "print()") in labels and ("formula", "E=mc2") in labels
    assert structure.groups and structure.tables[0].data.table_cells[0].text == "x"
    assert structure.texts[1].parent is not None
    assert structure.texts[1].parent.cref == structure.groups[0].self_ref
