import pypdfium2 as pdfium
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

from anonymization_pipeline.layout.model import (
    FieldLayout,
    Graphic,
    Layout,
    PageLayout,
    Run,
    Style,
)
from anonymization_pipeline.layout.pdf import extract
from anonymization_pipeline.rendering import (
    _words,
    attach,
    attachment,
    render_pdf,
    text_of,
)


def _box(left: float, top: float, right: float, bottom: float) -> BoundingBox:
    return BoundingBox(
        l=left, t=top, r=right, b=bottom, coord_origin=CoordOrigin.BOTTOMLEFT
    )


def _structure() -> DoclingDocument:
    doc = DoclingDocument(name="documento")
    doc.add_page(1, Size(width=595, height=842))
    doc.add_page(2, Size(width=300, height=400))
    line = "Paziente: [PERSONA_1]"
    doc.add_text(
        DocItemLabel.TEXT,
        line,
        prov=ProvenanceItem(
            page_no=1, bbox=_box(50, 800, 400, 786), charspan=(0, len(line))
        ),
    )
    cells = [
        TableCell(
            text=t,
            start_row_offset_idx=0,
            end_row_offset_idx=1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
        )
        for c, t in enumerate(["Codice fiscale", "[CODICE_FISCALE_1]"])
    ]
    doc.add_table(
        TableData(table_cells=cells, num_rows=1, num_cols=2),
        prov=ProvenanceItem(page_no=1, bbox=_box(50, 700, 500, 680), charspan=(0, 0)),
    )
    doc.add_picture(
        prov=ProvenanceItem(page_no=2, bbox=_box(20, 380, 120, 300), charspan=(0, 0))
    )
    return doc


def _page_text(pdf: bytes) -> list[str]:
    return text_of(pdf)[:-1]


def test_words_keep_tags_whole_and_break_on_newlines() -> None:
    text = "Sig. [PERSONA 1] ok\nfine"

    assert _words(text, [(5, 16)]) == [
        ("Sig.", False),
        ("[PERSONA 1]", True),
        ("ok", False),
        None,
        ("fine", False),
    ]


def test_pages_text_tables_and_pictures() -> None:
    pdf = render_pdf(_structure(), {("#/texts/0", None): [(10, 21)]})

    document = pdfium.PdfDocument(pdf)
    sizes = [document[i].get_size() for i in range(len(document))]
    document.close()
    assert sizes == [(595, 842), (300, 400)]
    first, second = _page_text(pdf)
    assert "Paziente: [PERSONA_1]" in first
    assert "Codice fiscale" in first and "[CODICE_FISCALE_1]" in first
    assert "[IMMAGINE]" in second


def test_text_that_does_not_fit_shrinks_but_is_all_there() -> None:
    doc = DoclingDocument(name="documento")
    doc.add_page(1, Size(width=200, height=200))
    text = " ".join(f"parola{i}" for i in range(60))
    doc.add_text(
        DocItemLabel.TEXT,
        text,
        prov=ProvenanceItem(
            page_no=1, bbox=_box(10, 150, 110, 120), charspan=(0, len(text))
        ),
    )

    page = _page_text(render_pdf(doc))[0]

    assert all(f"parola{i}" in page for i in range(60))


def test_metadata_are_neutral() -> None:
    metadata = text_of(render_pdf(_structure()))[-1]

    assert "documento" in metadata and "Rossi" not in metadata


def test_attachment_round_trip() -> None:
    pdf = attach(render_pdf(_structure()), "documento.anon.docling.json", b'{"a": 1}')

    assert attachment(pdf, "documento.anon.docling.json") == b'{"a": 1}'
    assert attachment(pdf, "altro.json") is None
    with pytest.raises(ValueError):
        attachment(b"not a pdf", "x")


def _layout(tag_width: float = 60.0) -> Layout:
    """Layout of ``_structure``: the text item and the second table cell, a rule on page 1."""
    style = Style(
        font="liberation-serif", bold=True, italic=False, size=12, color=(0, 0, 255)
    )
    return Layout(
        styles=[style],
        pages=[
            PageLayout(
                page_no=1,
                source="pdf",
                graphics=[
                    Graphic(
                        kind="line",
                        x0=50,
                        y0=650,
                        x1=500,
                        y1=650,
                        stroke=(0, 0, 0),
                        fill=None,
                        width=1,
                    )
                ],
            )
        ],
        fields=[
            FieldLayout(
                ref="#/texts/0",
                cell=None,
                page_no=1,
                runs=[
                    Run(start=0, end=9, x=60, baseline=790, width=55, style=0),
                    Run(
                        start=10,
                        end=21,
                        x=120,
                        baseline=790,
                        width=tag_width,
                        style=0,
                        tag=True,
                    ),
                ],
            ),
            FieldLayout(
                ref="#/tables/0",
                cell=1,
                page_no=1,
                runs=[
                    Run(
                        start=0,
                        end=18,
                        x=300,
                        baseline=690,
                        width=100,
                        style=0,
                        tag=True,
                    )
                ],
            ),
        ],
    )


def test_layout_draws_words_at_their_place_with_their_style() -> None:
    pdf = render_pdf(_structure(), {("#/texts/0", None): [(10, 21)]}, layout=_layout())

    page = extract(pdf, {1}, graphics=True)
    glyphs = page.pages[1].glyphs
    first = next(g for g in glyphs if g.char == "P")
    assert (round(first.x0, 1), round(first.baseline, 1)) == (60, 790)
    tags = sorted(round(g.x0, 1) for g in glyphs if g.char == "[")
    assert tags == [120, 300]  # the text tag, then the cell tag
    assert {(s.bold, s.color) for s in page.styles if s.size == 12} == {
        (True, (0, 0, 255))
    }
    # the page has its own lines: the table is not drawn as a grid (the tags are grey fills)
    strokes = [(g.kind, g.y0) for g in page.pages[1].graphics if g.stroke is not None]
    assert strokes == [("line", 650)]
    text = " ".join(_page_text(pdf))
    assert (
        "Paziente:" in text
        and "[CODICE_FISCALE_1]" in text
        and "Codice fiscale" in text
    )


def test_a_tag_shrinks_into_the_box_of_its_value() -> None:
    pdf = render_pdf(_structure(), layout=_layout(tag_width=20))

    glyphs = extract(pdf, {1}, graphics=False).pages[1].glyphs
    tag = [g for g in glyphs if abs(g.baseline - 790) < 1 and g.x0 >= 119]
    assert tag[0].x0 == pytest.approx(120, abs=0.1)
    assert tag[-1].x1 <= 141
