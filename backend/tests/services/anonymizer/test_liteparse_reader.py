"""LiteparseReader: real LiteParse on synthetic PDFs, OCR server mocked."""

import io
import json
from pathlib import Path

import httpx
import numpy as np
import pypdfium2 as pdfium
import pytest
from liteparse import LiteParse
from PIL import Image

from src.services.anonymizer.config import PipelineConfig
from src.services.anonymizer.factory import (
    build_reader,
    load_models,
)
from src.services.anonymizer.layers import TextLayer, Word
from src.services.anonymizer.pdf_pages import PageView, open_pages
from src.services.anonymizer.pipeline import Pipeline
from src.services.anonymizer.reader import (
    LiteparseReader,
    flattened_tokens,
    group_lines,
    pdfium_tokens,
    polygon_direction,
    server_words,
    split_words,
)
from tests.documents import (
    FAKE_CF,
    FakeNER,
    FakeReader,
    Page,
    draw_pdf,
    edit_pdf,
    find_text,
    letter_pdf,
    overlay_pdf,
    page_views,
    scanned_pdf,
)

DPI = 150


def _parser() -> LiteParse:
    # no OCR inside LiteParse: scanned pages go through the reader's own OCR call
    return LiteParse(ocr_enabled=False, include_complexity=True, dpi=DPI, quiet=True)


def _client(
    results: list[dict], health: int = 200, seen: list | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(health, json={"status": "healthy"})
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"results": results})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _reader(
    results: list[dict] | None = None, health: int = 200, seen: list | None = None
) -> LiteparseReader:
    reader = LiteparseReader(
        "http://ocr.test",
        dpi=DPI,
        parser=_parser(),
        client=_client(results or [], health, seen),
    )
    reader.load()
    return reader


def _read(reader: LiteparseReader, path: Path):
    return reader.read(path, page_views(path), lambda _: None)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def test_native_tokens_have_the_text_and_its_boxes(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    (page,) = _read(_reader(), src)
    (layer,) = page.layers
    assert layer.source == "native"
    assert "Paziente: Mario Rossi" in layer.text
    for needle in (FAKE_CF, "Paziente:", "metformina", "Babinski"):
        expected = find_text(src, needle)
        assert any(
            g.text == needle and _iou(g.rect, expected) > 0.5 for g in layer.tokens
        ), needle
    assert page.ocr_words is None and page.info["scanned"] is False


def test_rotated_page_comes_back_in_page_space(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    expected = find_text(src, FAKE_CF)  # page space, as on the unrotated page
    edit_pdf(src, tmp_path / "r.pdf", rotate={0: 90})
    (page,) = _read(_reader(), tmp_path / "r.pdf")
    assert page.info["rotation"] == 90
    assert (page.info["width"], page.info["height"]) == (842, 595)
    (layer,) = page.layers
    cf = next(t for t in layer.tokens if t.text == FAKE_CF)
    assert _iou(cf.rect, expected) > 0.5


def test_invisible_text_is_read(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        p.text(40, 100, "Referto visibile")
        p.text(40, 130, FAKE_CF, invisible=True)
        p.text(40, 160, "Mario Rossi", colour=(1, 1, 1))

    (page,) = _read(_reader(), draw_pdf(tmp_path / "h.pdf", draw))
    assert FAKE_CF in page.layers[0].text
    assert "Mario Rossi" in page.layers[0].text


def test_scan_is_ocred_through_the_server(tmp_path: Path) -> None:
    src = scanned_pdf(letter_pdf(tmp_path / "l.pdf"), tmp_path / "s.pdf", dpi=DPI)
    seen: list[httpx.Request] = []
    s = DPI / 72
    results = [
        {
            "text": "Codice",
            "bbox": [40 * s, 200 * s, 75 * s, 210 * s],
            "confidence": 0.95,
        },
        {
            "text": FAKE_CF,
            "bbox": [80 * s, 200 * s, 180 * s, 210 * s],
            "confidence": 0.93,
        },
    ]
    (page,) = _read(_reader(results, seen=seen), src)
    assert page.info["scanned"] is True
    assert len(seen) == 1
    assert seen[0].headers["X-Render-DPI"] == str(DPI)
    (layer,) = page.layers
    assert layer.source == "ocr" and FAKE_CF in layer.text
    cf = next(t for t in layer.tokens if t.text == FAKE_CF)
    assert cf.rect == pytest.approx((80, 200, 180, 210))
    assert page.ocr_words is not None and [w.text for w in page.ocr_words] == [
        "Codice",
        FAKE_CF,
    ]


def test_ocr_server_failure_fails_the_read(tmp_path: Path) -> None:
    src = scanned_pdf(letter_pdf(tmp_path / "l.pdf"), tmp_path / "s.pdf", dpi=DPI)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(500, json={"error": "OCR failed"})

    reader = LiteparseReader(
        "http://ocr.test",
        dpi=DPI,
        parser=_parser(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    reader.load()
    with pytest.raises(httpx.HTTPStatusError):
        _read(reader, src)


def test_not_ready_without_a_healthy_server(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        LiteparseReader("", parser=_parser()).load()
    reader = LiteparseReader(
        "http://ocr.test", parser=_parser(), client=_client([], health=503)
    )
    with pytest.raises(RuntimeError):
        reader.load()
    assert not reader.ready
    with pytest.raises(RuntimeError):
        _read(reader, letter_pdf(tmp_path / "l.pdf"))


def test_load_models_stays_not_ready_without_server() -> None:
    reader = build_reader(PipelineConfig(ocr_server_url="", ner_enabled=False))
    assert isinstance(reader, LiteparseReader)
    assert load_models(Pipeline(reader, None)) is False


def test_build_reader_uses_the_configured_server() -> None:
    reader = build_reader(
        PipelineConfig(ocr_server_url="http://ocr.test/", ocr_dpi=200)
    )
    assert isinstance(reader, LiteparseReader)
    assert (reader.server_url, reader.dpi) == ("http://ocr.test", 200)


def test_pipeline_on_liteparse_finds_and_never_logs_the_cf(
    tmp_path: Path, log_stream: io.StringIO
) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    analysis = Pipeline(_reader(), FakeNER()).analyze(src)
    assert any(b.label == "FISCAL_CODE" for b in analysis.boxes)
    assert FAKE_CF not in log_stream.getvalue()
    assert "Mario Rossi" not in log_stream.getvalue()


def test_polygon_direction_follows_the_long_side() -> None:
    assert polygon_direction([[0, 0], [100, 0], [100, 10], [0, 10]], "Mario") == "ltr"
    assert polygon_direction([[10, 100], [10, 0], [30, 0], [30, 100]], "Mario") == "btt"
    assert polygon_direction([[10, 0], [10, 100], [30, 100], [30, 0]], "Mario") == "ttb"
    # screen-axis order, short side first: the long side decides
    assert (
        polygon_direction([[20, 50], [50, 50], [50, 750], [20, 750]], "Mario") == "ttb"
    )
    assert polygon_direction([[0, 0], [20, 0], [20, 22], [0, 22]], "Mario") == "ltr"


def test_split_words_of_a_vertical_line() -> None:
    w = Word("ab cd", (0.0, 0.0, 10.0, 50.0), 0.9, "ocr", 3, "btt")
    a, b = split_words(w)
    assert (a.text, b.text) == ("ab", "cd")
    # bottom to top: the first word is at the bottom
    assert a.rect[1] > b.rect[1]
    assert a.rect == pytest.approx((0, 30, 10, 50))


def test_server_words_scale_pixels_to_points() -> None:
    words = server_words(
        [
            {"text": "Mario Rossi", "bbox": [0, 0, 200, 20], "confidence": 0.9},
            {"text": "", "bbox": [0, 0, 1, 1]},
            {"text": "x", "bbox": None},
        ],
        0.5,
    )
    assert [w.text for w in words] == ["Mario", "Rossi"]
    assert words[0].rect == pytest.approx((0, 0, 100 * 5 / 11, 10))
    assert json.dumps([w.direction for w in words]) == '["ltr", "ltr"]'


def test_vertical_native_text_keeps_whole_words(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        p.text(30, 700, "Stampato da Mario Rossi", size=8, rotate=90)
        p.text(560, 100, f"Copia conforme {FAKE_CF}", size=8, rotate=270)

    expected = sorted(
        ["Stampato", "da", "Mario", "Rossi", "Copia", "conforme", FAKE_CF]
    )
    (page,) = _read(_reader(), draw_pdf(tmp_path / "v.pdf", draw))
    (layer,) = page.layers
    assert sorted(t.text for t in layer.tokens) == expected
    assert FAKE_CF in layer.text
    assert "Stampato da Mario Rossi" in layer.text


def test_table_cells_stay_separate_lines() -> None:
    words = [
        Word("Nessuna", (240, 627, 270, 634), 1.0, "native"),
        Word("Vivente", (368, 627, 393, 634), 1.0, "native"),
        Word("Mario", (40, 100, 65, 108), 1.0, "native"),
        Word("Rossi", (68, 101, 92, 108), 1.0, "native"),
    ]
    lines = {w.text: w.line for w in group_lines(words)}
    assert lines["Mario"] == lines["Rossi"]
    assert lines["Nessuna"] != lines["Vivente"]
    assert lines["Mario"] < lines["Nessuna"]


def test_vertical_words_grouped_by_column_in_reading_order() -> None:
    words = [
        Word("da", (10, 60, 18, 70), 1.0, "ocr", 0, "btt"),
        Word("Stampato", (10, 72, 18, 110), 1.0, "ocr", 0, "btt"),
        Word("Copia", (580, 10, 588, 40), 1.0, "ocr", 0, "ttb"),
    ]
    out = group_lines(words)
    assert [w.text for w in out] == ["Stampato", "da", "Copia"]
    assert out[0].line == out[1].line != out[2].line


def test_hidden_layer_of_a_scan_is_kept_next_to_the_ocr(tmp_path: Path) -> None:
    # LiteParse with OCR on replaces such a layer with what OCR sees: the hidden
    # codice fiscale would never be redacted. Native text comes from a parse without OCR.
    scan = scanned_pdf(letter_pdf(tmp_path / "l.pdf"), tmp_path / "s.pdf", dpi=DPI)
    overlay_pdf(
        scan,
        lambda p: p.text(300, 800, f"Paz. {FAKE_CF}", size=8, invisible=True),
        tmp_path / "h.pdf",
    )
    s = DPI / 72
    other = [
        {
            "text": "QUALITA",
            "bbox": [300 * s, 792 * s, 360 * s, 801 * s],
            "confidence": 0.9,
        }
    ]
    (page,) = _read(_reader(other), tmp_path / "h.pdf")
    native, ocr = page.layers
    assert native.source == "native" and FAKE_CF in native.text
    assert ocr.source == "ocr" and "QUALITA" in ocr.text
    assert page.info["scanned"] is True


def test_overlapping_rows_of_a_form_keep_their_cells_together() -> None:
    # "Nome: Carmela" sits a few points above the T.S. row: the digits must not
    # be split between the two rows, or no rule sees the 20-digit code
    words = [
        Word("Nome:", (51.6, 319.2, 74.2, 328.1), 1.0, "ocr"),
        Word("Carmela", (101.0, 319.2, 131.8, 328.3), 1.0, "ocr"),
        Word("T.S.", (307.2, 324.2, 324.5, 333.6), 1.0, "ocr"),
    ]
    digits = "80380010436506917601"
    # vertical extents read by the OCR on the gold set (autorizzazione_preventiva_complessa_03)
    ys = [
        (324.2, 333.4),
        (325.0, 332.6),
        (324.5, 333.4),
        (324.5, 332.9),
        (324.5, 332.9),
        (324.5, 332.9),
        (325.0, 332.6),
        (324.2, 332.6),
        (323.8, 332.2),
        (323.8, 332.6),
        (323.8, 332.2),
        (323.8, 331.9),
        (324.2, 331.4),
        (324.2, 331.4),
        (323.8, 331.4),
        (323.8, 331.4),
        (323.8, 331.2),
        (323.8, 331.2),
        (323.5, 331.4),
        (324.2, 330.7),
    ]
    words += [
        Word(d, (332.9 + 11 * i, y0, 340.3 + 11 * i, y1), 1.0, "ocr")
        for i, (d, (y0, y1)) in enumerate(zip(digits, ys, strict=True))
    ]
    out = group_lines(words)
    lines: dict[int, list[str]] = {}
    for w in out:
        lines.setdefault(w.line, []).append(w.text)
    assert (
        "".join(next(v for v in lines.values() if "8" in v and "T.S." not in v))
        == digits
    )
    assert ["Nome:"] in lines.values() and ["Carmela"] in lines.values()


def test_lines_of_a_row_follow_reading_order() -> None:
    # labels and values on one row: a 0.1 pt higher value must not jump ahead of its label
    words = [
        Word("Enrico", (132.3, 187.7, 161.9, 199.0), 1.0, "native"),
        Word("Bellandi", (164.5, 187.7, 201.5, 199.0), 1.0, "native"),
        Word("21/09/1967", (369.9, 187.7, 417.4, 199.0), 1.0, "native"),
        Word("Paziente:", (65.0, 187.8, 100.5, 197.7), 1.0, "native"),
        Word("Nato", (302.6, 187.8, 320.6, 197.7), 1.0, "native"),
        Word("il:", (323.0, 187.8, 329.1, 197.7), 1.0, "native"),
        Word("Referto", (65.0, 210.0, 100.0, 220.0), 1.0, "native"),
    ]
    out = group_lines(words)
    assert [w.text for w in out] == [
        "Paziente:",
        "Enrico",
        "Bellandi",
        "Nato",
        "il:",
        "21/09/1967",
        "Referto",
    ]


def test_single_glyphs_taller_than_wide_read_left_to_right() -> None:
    # a digit cell is taller than wide: no reading direction can be inferred from
    # its box, and calling it vertical would cut it out of its line
    quad = [[454.0, 324.0], [460.0, 324.0], [460.0, 332.0], [454.0, 332.0]]
    words = server_words(
        [
            {
                "text": "6",
                "bbox": [444, 324, 449, 332],
                "polygon": quad,
                "confidence": 1,
            },
            {
                "text": "5",
                "bbox": [454, 324, 460, 332],
                "polygon": quad,
                "confidence": 1,
            },
        ],
        1.0,
    )
    assert [w.direction for w in words] == ["ltr", "ltr"]
    assert words[0].line == words[1].line
    assert polygon_direction(quad, "ab") == "ltr"
    assert (
        polygon_direction([[10, 100], [10, 0], [30, 0], [30, 100]], "Stampato") == "btt"
    )


def _pdfium_tokens(path: Path) -> list:
    with pdfium.PdfDocument(path) as pdf:
        return pdfium_tokens(pdf[0])


def test_exact_native_tokens_cover_the_whole_glyphs(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    tokens = _pdfium_tokens(src)
    cf = next(t for t in tokens if t.text == FAKE_CF)
    found = find_text(src, FAKE_CF)  # pdfium's tight box of the glyphs
    assert len(cf.chars) == len(FAKE_CF)
    # from the font's descender to its ascender: taller than the tight glyph box
    assert cf.rect[1] < found[1] and cf.rect[3] > found[3]
    assert cf.rect[0] == pytest.approx(found[0], abs=1)
    assert cf.rect[2] == pytest.approx(found[2], abs=1)


def test_exact_native_tokens_of_vertical_and_glued_text(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        p.text(30, 700, "Stampato da Mario Rossi", size=8, rotate=90)
        p.text(560, 100, f"Copia conforme {FAKE_CF}", size=8, rotate=270)
        p.text(100, 300, "Paz.", size=10)
        p.text(124, 300, "BOTTA", size=10)  # no space character, a 4 pt gap
        p.text(40, 400, "Nascosto", size=10, invisible=True)

    tokens = _pdfium_tokens(draw_pdf(tmp_path / "v.pdf", draw))
    by_text = {t.text: t for t in tokens}
    assert {"Stampato", "Rossi", FAKE_CF, "Paz.", "BOTTA", "Nascosto"} <= set(by_text)
    assert by_text["Stampato"].direction == "btt"
    assert by_text[FAKE_CF].direction == "ttb"
    assert by_text["Stampato"].line == by_text["Rossi"].line
    layer = TextLayer.build(0, "native", tokens)
    assert "Stampato da Mario Rossi" in layer.text


def test_reader_reads_exact_native_tokens(tmp_path: Path) -> None:
    src = letter_pdf(tmp_path / "l.pdf")
    (page,) = _read(_reader(), src)
    (layer,) = page.layers
    assert all(len(t.chars) == len(t.text) for t in layer.tokens)
    assert "Paziente: Mario Rossi" in layer.text


def test_exact_tokens_of_a_rotated_page_read_its_rows(tmp_path: Path) -> None:
    # a landscape table on a /Rotate 90 page: drawn turned in page space, read
    # row by row on the page as displayed; boxes come back in page space
    def draw(p: Page) -> None:
        p.text(100, 700, "2A", size=10, rotate=90)
        p.text(100, 640, "Adebayo Oluwaseun", size=10, rotate=90)
        p.text(100, 500, "55", size=10, rotate=90)
        p.text(130, 700, "3A", size=10, rotate=90)
        p.text(130, 640, "Amato Vito", size=10, rotate=90)

    src = edit_pdf(draw_pdf(tmp_path / "t.pdf", draw), rotate={0: 90})
    with pdfium.PdfDocument(src) as pdf:
        tokens = pdfium_tokens(pdf[0], PageView(pdf[0], 0).info)
    layer = TextLayer.build(0, "native", tokens)
    assert layer.text.index("2A") < layer.text.index("Adebayo") < layer.text.index("3A")
    vito = next(t for t in tokens if t.text == "Vito")
    assert (
        _iou(vito.rect, find_text(src, "Vito")) > 0.3
    )  # page space, as the text layer


def test_form_field_values_are_read_as_page_text(tmp_path: Path) -> None:
    def draw(p: Page) -> None:
        p.text(40, 100, "Modulo di consenso")
        p.c.acroForm.textfield(
            name="cf", value=FAKE_CF, x=200, y=842 - 160, width=160, height=18
        )

    src = draw_pdf(tmp_path / "form.pdf", draw)
    with pdfium.PdfDocument(src) as pdf:
        tokens = flattened_tokens(src, open_pages(pdf))
        assert FAKE_CF not in [t.text for t in pdfium_tokens(pdf[0])]  # not page text
    assert FAKE_CF in [t.text for t in tokens[0]]


def test_grayscale_pictures_of_native_pages_are_read_inside_only(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(0)
    xray = (rng.random((300, 300)) * 120).astype(np.uint8)  # dark, textured
    buf = io.BytesIO()
    Image.fromarray(np.stack([xray] * 3, -1)).save(buf, "PNG")

    def draw(p: Page) -> None:
        p.text(40, 100, "Referto radiologico")
        p.image((60, 200, 300, 440), buf.getvalue())

    src = draw_pdf(tmp_path / "xray.pdf", draw)
    inside = Word(FAKE_CF, (80, 220, 200, 232), 0.9, "ocr", 0)
    outside = Word("Rossi", (400, 600, 440, 612), 0.9, "ocr", 1)
    reader = FakeReader({0: [inside, outside]})

    (page,) = _read(reader, src)

    native, ocr = page.layers
    assert ocr.source == "ocr" and [t.text for t in ocr.tokens] == [FAKE_CF]
    assert page.ocr_words is None  # a native page
