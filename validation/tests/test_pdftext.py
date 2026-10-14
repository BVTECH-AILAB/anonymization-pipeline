"""Text of the censored layout PDF."""

import base64
import io

from reportlab.pdfgen.canvas import Canvas

from anonval.pdftext import layout_pages, similarity


def _pdf(*pages: str) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for text in pages:
        canvas.drawString(72, 720, text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def test_layout_pdf_becomes_page_texts() -> None:
    pdf = base64.b64encode(
        _pdf("Paziente: [PERSONA_1]", "Firma: Dott. Paolo Neri")
    ).decode()
    out = layout_pages({"markdown": "x", "layout_pdf_b64": pdf})
    assert "layout_pdf_b64" not in out
    assert [p.strip() for p in out["layout_pages"]] == [
        "Paziente: [PERSONA_1]",
        "Firma: Dott. Paolo Neri",
    ]


def test_no_layout_pdf() -> None:
    assert layout_pages({"markdown": "x", "layout_pdf_b64": None}) == {"markdown": "x"}
    assert layout_pages({"markdown": "x"}) == {"markdown": "x"}


def test_unreadable_layout_pdf() -> None:
    out = layout_pages({"layout_pdf_b64": base64.b64encode(b"not a pdf").decode()})
    assert out == {"layout_error": "PDF con layout non leggibile"}


def test_similarity_of_the_layout_pdf_to_the_original() -> None:
    original = _pdf("Paziente: Paolo Neri", "Firma")
    censored = _pdf("Paziente: [PERSONA_1]", "Firma")

    same = similarity(original, original)
    scores = similarity(original, censored)

    assert same == [1.0, 1.0]
    assert 0.9 < scores[0] < 1.0 and scores[1] == 1.0
    encoded = base64.b64encode(censored).decode()
    out = layout_pages({"layout_pdf_b64": encoded}, original)
    assert out["layout_similarity"] == scores
    assert "layout_similarity" not in layout_pages({"layout_pdf_b64": encoded}, b"text")
    broken = layout_pages({"layout_pdf_b64": encoded}, b"%PDF-1.4 broken")
    assert "layout_similarity" not in broken and "layout_pages" in broken
