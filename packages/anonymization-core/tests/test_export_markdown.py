from pathlib import Path

from anonymization_core.export_markdown import (
    export_markdown,
)
from anonymization_core.geometry import Rect
from anonymization_core.layers import TextLayer, Token
from anonymization_core.pipeline import (
    Analysis,
    Box,
    Pipeline,
)
from anonymization_core.reader import Region
from anonymization_core.testing import (
    EMAIL,
    FAKE_CF,
    PATIENT,
    FakeNER,
    FakeReader,
    Page,
    draw_pdf,
    letter_pdf,
    native_words,
    scanned_pdf,
)
from anonymization_core.verify import verify_markdown


def lab_table(page: Page) -> None:
    """A ruled table with patient data, then a borderless one with results."""
    ruled = [
        ["Paziente", "Codice fiscale", "Esito"],
        [PATIENT, FAKE_CF, "negativo"],
    ]
    for i, row in enumerate(ruled):
        for j, cell in enumerate(row):
            x, y = 40 + j * 150, 380 + i * 20
            page.rect((x, y, x + 150, y + 20))
            page.text(x + 5, y + 14, cell)
    results = [
        ["Esame", "Valore", "Unita"],
        ["Emoglobina", "13.2", "g/dL"],
        ["Glicemia", "95", "mg/dL"],
        ["Creatinina", "0.9", "mg/dL"],
    ]
    for i, row in enumerate(results):
        for j, cell in enumerate(row):
            page.text(40 + j * 150, 480 + i * 16, cell)
    page.text(40, 600, "Conclusioni: paziente dimesso in buone condizioni.")


def markdown_of(src: Path, tmp_path: Path, reader: FakeReader | None = None) -> str:
    analysis = Pipeline(reader or FakeReader(), FakeNER()).analyze(src, visual=False)
    out = tmp_path / "out.md"
    export_markdown(analysis, out)
    return out.read_text(encoding="utf-8")


def test_pii_becomes_placeholders_and_clinical_text_stays(tmp_path: Path) -> None:
    md = markdown_of(letter_pdf(tmp_path / "in.pdf"), tmp_path)

    for value in (FAKE_CF, PATIENT, "Rossi", EMAIL, "Garibaldi"):
        assert value not in md
    assert "Paziente: [NOME]" in md
    assert "Codice fiscale: [CODICE FISCALE]" in md
    assert "[EMAIL]" in md
    assert "morbo di Crohn" in md
    assert "LETTERA DI DIMISSIONE" in md


def test_tables_are_exported_as_anonymized_markdown_tables(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "in.pdf", extra=lab_table)

    md = markdown_of(src, tmp_path)

    assert "| Paziente | Codice fiscale | Esito |\n| --- | --- | --- |\n" in md
    assert "| [NOME] | [CODICE FISCALE] | negativo |" in md
    assert "| Esame | Valore | Unita |" in md
    assert "| Emoglobina | 13.2 | g/dL |" in md
    assert "| Creatinina | 0.9 | mg/dL |" in md
    assert "[TABELLA]" not in md
    for value in (FAKE_CF, PATIENT):
        assert value not in md
    assert md.index("Il medico") < md.index("| Paziente")
    assert md.index("| Paziente") < md.index("| Esame") < md.index("Conclusioni")


def test_images_become_placeholders_in_reading_order(tmp_path: Path) -> None:
    src = letter_pdf(
        tmp_path / "in.pdf",
        qr_text="https://referti.example.it/r?id=1",
        signature=True,
    )

    md = markdown_of(src, tmp_path)

    assert md.count("[IMMAGINE]") == 2
    assert md.index("Il medico") < md.index("[IMMAGINE]")


def test_a_table_with_text_outside_its_cells_is_exported_as_lines(
    tmp_path: Path,
) -> None:
    def token(text: str, x: float, line: int) -> Token:
        y = 100.0 + line * 20
        chars = [(x + i * 6, y, x + (i + 1) * 6, y + 10) for i in range(len(text))]
        return Token(text, (x, y, x + len(text) * 6, y + 10), chars, line, "native")

    layer = TextLayer.build(
        0,
        "native",
        [token("Esame", 40, 0), token("Valore", 200, 0), token("nota", 40, 1)],
    )
    cells: list[list[Rect | None]] = [[(38, 98, 150, 112), (198, 98, 300, 112)]]
    analysis = Analysis(
        tmp_path / "x.pdf",
        [{"width": 595, "height": 842}],
        layers=[layer],
        regions=[(0, Region("table", (38, 98, 300, 132), cells))],
    )
    out = tmp_path / "out.md"

    export_markdown(analysis, out)

    assert out.read_text(encoding="utf-8") == (
        "<!-- pagina 1 -->\n\nEsame Valore\n\nnota\n"
    )


def test_a_pipe_inside_a_cell_does_not_break_the_table(tmp_path: Path) -> None:
    def draw(page: Page) -> None:
        for i, row in enumerate([["Esame", "Note"], ["Glicemia", "a | b"]]):
            for j, cell in enumerate(row):
                x, y = 40 + j * 150, 100 + i * 20
                page.rect((x, y, x + 150, y + 20))
                page.text(x + 5, y + 14, cell)

    md = markdown_of(draw_pdf(tmp_path / "pipe.pdf", draw), tmp_path)

    assert "| Glicemia | a \\| b |" in md


def test_every_page_has_a_delimiter(tmp_path: Path) -> None:
    src = draw_pdf(
        tmp_path / "two.pdf",
        lambda p: p.text(40, 100, "Prima pagina"),
        lambda p: p.text(40, 100, "Seconda pagina"),
    )

    md = markdown_of(src, tmp_path)

    assert md.startswith("<!-- pagina 1 -->")
    assert md.index("Prima pagina") < md.index("<!-- pagina 2 -->")
    assert md.index("<!-- pagina 2 -->") < md.index("Seconda pagina")


def test_scanned_pages_are_read_by_ocr(tmp_path: Path) -> None:
    native = letter_pdf(tmp_path / "native.pdf")
    scan = scanned_pdf(native, tmp_path / "scan.pdf")

    md = markdown_of(scan, tmp_path, FakeReader(native_words(native)))

    assert FAKE_CF not in md
    assert "[CODICE FISCALE]" in md
    assert "Crohn" in md
    assert "[IMMAGINE]" not in md  # the scan itself is not a picture


def test_fast_analysis_skips_the_visual_detectors(tmp_path: Path) -> None:
    src = letter_pdf(
        tmp_path / "in.pdf", qr_text="https://x.example.it", signature=True
    )

    analysis = Pipeline(FakeReader(), FakeNER()).analyze(src, visual=False)

    assert not [b for b in analysis.boxes if b.layer == "visual"]
    assert analysis.timings["visual"] == 0
    assert sorted(r.kind for _, r in analysis.regions) == ["figure", "figure"]


def test_a_value_left_in_the_markdown_export_is_reported(tmp_path: Path) -> None:
    analysis = Analysis(tmp_path / "x.pdf", [{"width": 595, "height": 842}])
    analysis.boxes.append(
        Box(0, (10, 10, 90, 20), "PERSON", "Mario Rossi", 0.9, ["ner"], "native", True)
    )
    leaky, clean = tmp_path / "leaky.md", tmp_path / "clean.md"
    leaky.write_text("Paziente Mario  Rossi", encoding="utf-8")
    clean.write_text("Paziente [NOME]", encoding="utf-8")

    assert verify_markdown(leaky, analysis) == [
        "value still in the Markdown export: 'Mario Rossi'"
    ]
    assert verify_markdown(clean, analysis) == []
