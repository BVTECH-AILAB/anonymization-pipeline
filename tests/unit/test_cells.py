"""Codes printed one character per box on scans: boxes found, cells cleaned, read as one word."""

import io
from pathlib import Path

import httpx
import numpy as np
import pytest
from liteparse import LiteParse

from anonmyzation_pipeline.services.anonymization.cells import (
    cell_rows,
    compose,
    glyph,
    read_cells,
)
from anonmyzation_pipeline.services.anonymization.layers import Word, ocr_layer
from anonmyzation_pipeline.services.anonymization.reader import (
    LiteparseReader,
    read_upside_down,
)
from tests.conftest import FAKE_CF
from tests.documents import Page, draw_pdf, page_view, page_views, scanned_pdf

DPI = 300
S = DPI / 72
CELL = 14.0  # points


def _line(page: Page, a: tuple[float, float], b: tuple[float, float]) -> None:
    page.c.setLineWidth(0.8)
    page.c.line(a[0], page.height - a[1], b[0], page.height - b[1])


def _form(code: str, x0: float = 100, y0: float = 300):
    """A page with ``code`` written one character per box, a table and a paragraph."""

    def draw(page: Page) -> None:
        for i, c in enumerate(code):
            r = (x0 + i * CELL, y0, x0 + (i + 1) * CELL, y0 + CELL)
            page.rect(r, width=0.8)
            page.text(r[0] + 3.5, r[3] - 3.5, c)
        page.text(100, 280, "Codice fiscale")
        # a table of wide cells and a paragraph: no character boxes there
        for i in range(4):
            page.rect((100 + i * 90, 400, 190 + i * 90, 416), width=0.8)
            page.text(104 + i * 90, 412, "Emoglobina", size=9)
        page.text(100, 500, "Il paziente riferisce dispnea da sforzo.")

    return draw


def _render(tmp_path: Path, draw) -> np.ndarray:
    return page_view(draw_pdf(tmp_path / "form.pdf", draw)).render(DPI)


def _cell_px(
    i: int, x0: float = 100, y0: float = 300
) -> tuple[float, float, float, float]:
    return (
        (x0 + i * CELL) * S,
        y0 * S,
        (x0 + (i + 1) * CELL) * S,
        (y0 + CELL) * S,
    )


def _cell_pt(i: int) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = _cell_px(i)
    return (x0 / S, y0 / S, x1 / S, y1 / S)


def test_a_row_of_character_boxes_is_found_and_nothing_else(tmp_path: Path) -> None:
    rows = cell_rows(_render(tmp_path, _form(FAKE_CF)), S)

    assert len(rows) == 1
    assert len(rows[0]) == 16
    for i, cell in enumerate(rows[0]):
        assert cell == pytest.approx(_cell_px(i), abs=0.5 * S)


def test_outer_borders_lost_on_the_scan_are_restored_from_the_ruling(
    tmp_path: Path,
) -> None:
    # the first and the last vertical border are too faint to be seen: the
    # ruling lines still span the whole row, so all 16 boxes are found
    x0, y0, n = 100.0, 300.0, 16

    def draw(page: Page) -> None:
        _line(page, (x0, y0), (x0 + n * CELL, y0))
        _line(page, (x0, y0 + CELL), (x0 + n * CELL, y0 + CELL))
        for i in range(1, n):
            _line(page, (x0 + i * CELL, y0), (x0 + i * CELL, y0 + CELL))

    rows = cell_rows(_render(tmp_path, draw), S)

    assert [len(r) for r in rows] == [16]
    assert rows[0][0] == pytest.approx(_cell_px(0), abs=0.5 * S)
    assert rows[0][-1] == pytest.approx(_cell_px(15), abs=0.5 * S)


def test_a_row_is_not_stretched_along_a_long_rule_over_text(tmp_path: Path) -> None:
    # five boxes at the start of a long rule with text under it: only one box
    # past the borders seen may be added (a lost outer border), not the text
    x0, y0 = 100.0, 300.0

    def draw(page: Page) -> None:
        _line(page, (x0, y0), (x0 + 400, y0))
        _line(page, (x0, y0 + CELL), (x0 + 400, y0 + CELL))
        for i in range(6):
            _line(page, (x0 + i * CELL, y0), (x0 + i * CELL, y0 + CELL))
        for i in range(5):
            page.text(x0 + i * CELL + 3.5, y0 + CELL - 3.5, "AB12C"[i])
        page.text(
            x0 + 5 * CELL + 4, y0 + CELL - 3.5, "Paziente Mario Rossi nato a Roma"
        )

    rows = cell_rows(_render(tmp_path, draw), S)

    assert [len(r) for r in rows] in ([5], [6])


def test_boxes_full_of_ink_are_not_character_boxes(tmp_path: Path) -> None:
    # on a scan, a coloured title bar with white capitals thresholds into two
    # ruling lines with columns of ink between the letters: a row of "boxes"
    # whose inside is mostly ink, which must never replace the words of the bar
    img = _render(tmp_path, _form(FAKE_CF))
    x0, y0, x1, y1 = (int(v) for v in (*_cell_px(0)[:2], *_cell_px(15)[2:]))
    inside = img[y0 + 6 : y1 - 6, x0:x1]
    checker = (np.indices(inside.shape[:2]).sum(axis=0) % 2 == 0)[..., None]
    img[y0 + 6 : y1 - 6, x0:x1] = np.where(checker, 0, inside)

    assert cell_rows(img, S) == []


def test_speckles_are_removed_and_an_empty_cell_gives_nothing(tmp_path: Path) -> None:
    form = _form("A1")

    def draw(page: Page) -> None:
        form(page)
        # a speck of scanner noise inside the second box
        page.circle(100 + CELL + 2.5, 300 + 2.5, 0.4, fill=(0, 0, 0))

    gray = _render(tmp_path, draw).mean(axis=2).astype(np.uint8)

    one = glyph(gray, _cell_px(1), S)
    empty = glyph(gray, _cell_px(2), S)

    assert one is not None
    # only the digit is left: its ink is a single column band, the speck is gone
    ink_cols = np.nonzero((one < 128).any(axis=0))[0]
    assert ink_cols.max() - ink_cols.min() < 0.5 * CELL * S
    assert empty is None


def test_glyphs_are_composed_into_one_word_with_room_between_letters() -> None:
    glyphs = [np.full((40, 10), 0, np.uint8), np.full((30, 12), 0, np.uint8)]

    word = compose(glyphs, S)

    assert word.ndim == 3 and word.shape[2] == 3
    # repeated characters must not touch, or the recogniser reads "99" as "9"
    assert word.shape[1] >= 10 + 12 + int(3 * S)


def _noisy_words() -> list[Word]:
    # the page OCR reads the boxes with noise: ".C", "s", "i7" break the run
    noisy = ["s", ".C", "i7"] + list(FAKE_CF[3:])
    return [
        Word("Codice", (100, 270, 140, 282), 0.99, "ocr", 2),
        *(Word(t, _cell_pt(i), 0.6, "ocr", 3) for i, t in enumerate(noisy)),
    ]


def _recognizer(text: str, conf: float = 0.97, seen: list | None = None):
    def recognize(img: np.ndarray) -> tuple[str, float]:
        if seen is not None:
            seen.append(img)
        return text, conf

    return recognize


def test_noisy_boxes_are_replaced_by_the_clean_reading(tmp_path: Path) -> None:
    seen: list[np.ndarray] = []
    img = _render(tmp_path, _form(FAKE_CF))

    words = read_cells(img, S, _noisy_words(), _recognizer(FAKE_CF, seen=seen))
    layer = ocr_layer(0, words)

    assert len(seen) == 1
    assert [t.text for t in layer.tokens] == ["Codice", FAKE_CF]
    cf = layer.tokens[1]
    assert len(cf.chars) == 16
    for i, box in enumerate(cf.chars):
        assert box == pytest.approx(_cell_pt(i), abs=0.5)


@pytest.mark.parametrize(
    ("recognized", "conf"),
    [
        (FAKE_CF[:-1], 0.97),  # a character fewer than the boxes
        ("RSSMRA85M01H5O1U(", 0.97),  # a stray symbol
        (FAKE_CF, 0.3),  # unsure
    ],
)
def test_a_reading_that_does_not_fit_the_boxes_leaves_the_page_ocr(
    tmp_path: Path, recognized: str, conf: float
) -> None:
    img = _render(tmp_path, _form(FAKE_CF))
    words = read_cells(img, S, _noisy_words(), _recognizer(recognized, conf))

    assert [w.text for w in words][:4] == ["Codice", "s", ".C", "i7"]


def test_a_word_read_across_the_boxes_is_never_replaced(tmp_path: Path) -> None:
    # the page OCR read the whole code as one word, wider than the boxes found:
    # replacing it by the boxes would drop the characters outside them
    x0, y0, _, _ = _cell_pt(0)
    _, _, x1, y1 = _cell_pt(15)
    page = [Word("CF" + FAKE_CF, (x0 - 2 * CELL, y0, x1, y1), 0.95, "ocr", 3)]
    img = _render(tmp_path, _form(FAKE_CF))

    words = read_cells(img, S, page, _recognizer(FAKE_CF, 0.99))

    assert [w.text for w in words] == ["CF" + FAKE_CF]


def test_a_row_found_only_in_part_is_left_to_the_page_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # only 10 of the 16 boxes are found: the page OCR read the others one per
    # box, and replacing part of the code would split it in two
    page = [Word(c, _cell_pt(i), 0.9, "ocr", 3) for i, c in enumerate(FAKE_CF)]
    monkeypatch.setattr(
        "anonmyzation_pipeline.services.anonymization.cells.cell_rows",
        lambda img, s: [[_cell_px(i) for i in range(10)]],
    )
    img = _render(tmp_path, _form(FAKE_CF))

    words = read_cells(img, S, page, _recognizer(FAKE_CF[:10], 0.99))

    assert [w.conf for w in words] == [0.9] * 16
    assert [t.text for t in ocr_layer(0, words).tokens] == [FAKE_CF]


def test_a_valid_code_read_by_the_page_is_not_replaced_by_an_invalid_one(
    tmp_path: Path,
) -> None:
    # the page OCR read every box of a tessera sanitaria right; the clean
    # reading takes a 0 for an O: the page reading is kept
    card = "80380000000000000001"
    page = [Word(c, _cell_pt(i), 0.9, "ocr", 3) for i, c in enumerate(card)]
    wrong = card[:13] + "O" + card[14:]
    img = _render(tmp_path, _form(card))

    words = read_cells(img, S, page, _recognizer(wrong, 0.99))

    assert [t.text for t in ocr_layer(0, words).tokens] == [card]


def test_lower_case_and_spaces_of_the_reading_are_normalised(tmp_path: Path) -> None:
    img = _render(tmp_path, _form(FAKE_CF))
    words = read_cells(img, S, _noisy_words(), _recognizer("rssmra85 m01h501u"))

    assert "".join(w.text for w in words[1:]) == FAKE_CF


def test_a_row_the_page_ocr_missed_gets_its_own_line(tmp_path: Path) -> None:
    img = _render(tmp_path, _form(FAKE_CF))
    page = [Word("Codice", (100, 270, 140, 282), 0.99, "ocr", 2)]

    words = read_cells(img, S, page, _recognizer(FAKE_CF))

    assert "".join(w.text for w in words[1:]) == FAKE_CF
    assert {w.line for w in words[1:]} == {3}


def test_a_page_read_upside_down_is_recognised_from_its_words() -> None:
    rtl = Word("Mario Rossi", (0, 0, 50, 10), 0.9, "ocr", 0, "rtl")
    ltr = Word("Via", (0, 20, 15, 30), 0.9, "ocr", 1)
    assert read_upside_down([rtl, ltr])
    assert not read_upside_down([ltr])
    assert not read_upside_down([])


# --- through the reader: page OCR and recognition both on the OCR server ---

READER_DPI = 150


def _server(seen: list[str], page_words: list[dict], recognized: str):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path == "/recognize":
            return httpx.Response(200, json={"text": recognized, "confidence": 0.97})
        return httpx.Response(200, json={"results": page_words})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _scan_results(direction: str = "ltr") -> list[dict]:
    """The page OCR of the form scan: the boxes read with noise, then a paragraph."""
    s = READER_DPI / 72
    noisy = ["s", ".C", "i7"] + list(FAKE_CF[3:])
    boxes = [(t, _cell_pt(i)) for i, t in enumerate(noisy)]
    boxes += [
        ("paziente", (100, 490, 140, 502)),
        ("riferisce", (145, 490, 185, 502)),
        ("dispnea", (190, 490, 225, 502)),
    ]
    out = []
    for t, rect in boxes:
        x0, y0, x1, y1 = (v * s for v in rect)
        quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        if direction == "rtl":
            quad = [[x1, y1], [x0, y1], [x0, y0], [x1, y0]]
        out.append(
            {"text": t, "bbox": [x0, y0, x1, y1], "confidence": 0.6, "polygon": quad}
        )
    return out


def _scan_reader(tmp_path: Path, results: list[dict], seen: list[str]):
    native = draw_pdf(tmp_path / "form.pdf", _form(FAKE_CF))
    scan = scanned_pdf(native, tmp_path / "scan.pdf", dpi=300)
    reader = LiteparseReader(
        "http://ocr.test",
        dpi=READER_DPI,
        parser=LiteParse(
            ocr_enabled=False, include_complexity=True, dpi=READER_DPI, quiet=True
        ),
        client=_server(seen, results, FAKE_CF),
    )
    reader.load()
    return reader, scan


def test_the_reader_reads_the_boxes_of_a_scan_again(tmp_path: Path) -> None:
    seen: list[str] = []
    reader, scan = _scan_reader(tmp_path, _scan_results(), seen)

    (page,) = reader.read(scan, page_views(scan), lambda _: None)

    assert "/recognize" in seen
    ocr = next(layer for layer in page.layers if layer.source == "ocr")
    assert FAKE_CF in [t.text for t in ocr.tokens]


def test_the_reader_leaves_the_boxes_of_a_page_read_upside_down(tmp_path: Path) -> None:
    seen: list[str] = []
    reader, scan = _scan_reader(tmp_path, _scan_results("rtl"), seen)

    reader.read(scan, page_views(scan), lambda _: None)

    assert "/recognize" not in seen


def test_reading_the_boxes_again_never_logs_the_code(
    tmp_path: Path, log_stream: io.StringIO
) -> None:
    seen: list[str] = []
    reader, scan = _scan_reader(tmp_path, _scan_results(), seen)

    reader.read(scan, page_views(scan), lambda _: None)

    assert "/recognize" in seen
    assert FAKE_CF not in log_stream.getvalue()
    assert FAKE_CF[:6] not in log_stream.getvalue()
