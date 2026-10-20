"""Text layout and visual marks: character boxes, vector signatures and stamps, clinical pictures."""

import io
from pathlib import Path

import numpy as np
from PIL import Image

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.layers import (
    Token,
    Word,
    ocr_layer,
    split_glued,
)
from anonmyzation_pipeline.services.anonymization.pipeline import Pipeline
from tests.conftest import FAKE_CF
from tests.documents import FakeNER, FakeReader, Page, draw_pdf, page_view


def test_characters_printed_one_per_box_become_one_word(tmp_path: Path) -> None:
    def draw(page: Page) -> None:
        for i, ch in enumerate(FAKE_CF):  # one cell per character, as on paper forms
            page.rect((50 + i * 15, 100, 65 + i * 15, 116))
            page.text(54 + i * 15, 112, ch, size=11)
        page.text(50, 200, "e a i o u sono vocali in una frase normale", size=11)

    src = draw_pdf(tmp_path / "cells.pdf", draw)
    analysis = Pipeline(FakeReader(), FakeNER(())).analyze(src)
    (layer,) = analysis.layers

    tokens = [t.text for t in layer.tokens]

    assert FAKE_CF in tokens
    assert "frase" in tokens and "a" in tokens  # prose stays word by word


def test_ocr_cells_join_despite_overlaps_dots_and_bars() -> None:
    # OCR of a code printed one character per box: boxes overlap by 1pt, an "I"
    # is read as "|", a "1" carries a dot
    cells = [
        "B",
        "N",
        "C",
        "G",
        "L",
        "|",
        "7",
        "8",
        "D",
        "1.",
        "2",
        "A",
        "9",
        "4",
        "4",
        "|",
    ]
    words = [
        Word(c, (100 + 12 * i, 200, 113 + 12 * i, 212), 0.9, "ocr", i)
        for i, c in enumerate(cells)
    ]
    words.append(Word("| |", (400, 200, 420, 212), 0.9, "ocr", 99))

    tokens = [t.text for t in ocr_layer(0, words).tokens]

    assert tokens[0] == "BNCGLI78D12A9441"
    assert len(ocr_layer(0, words).tokens[0].chars) == len(cells)


def test_table_borders_alone_are_not_a_code() -> None:
    words = [
        Word(c, (100 + 12 * i, 200, 110 + 12 * i, 212), 0.9, "ocr", i)
        for i, c in enumerate(["|", "|", "|", "|", "|", "|"])
    ]

    assert [t.text for t in ocr_layer(0, words).tokens] == ["|"] * 6


def test_vector_signature_and_stamp_frame_are_found_and_logos_ignored(
    tmp_path: Path,
) -> None:
    def draw(page: Page) -> None:
        points = [(60.0, 420.0)]
        for k in range(3):  # a handwritten signature: an open stroke of curves
            points += [(80 + 60 * k, 400), (100 + 60 * k, 440), (120 + 60 * k, 420)]
        page.bezier(points, (0.1, 0.15, 0.4), 0.9)
        page.rect((300, 400, 450, 460), stroke=(0.3, 0.2, 0.6), width=1.2, radius=6)
        page.circle(60, 60, 15, fill=(0.2, 0.4, 0.6))  # filled logo

    src = draw_pdf(tmp_path / "marks.pdf", draw)

    labels = sorted(f.label for f in visual.vector_marks(page_view(src)))

    assert labels == ["SIGNATURE", "STAMP"]


def _png(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, "PNG")
    return buf.getvalue()


def test_a_grayscale_xray_is_a_picture_not_a_stamp(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    xray = (rng.random((300, 300)) * 120).astype(np.uint8)  # dark, textured
    png = _png(np.stack([xray] * 3, -1))
    src = draw_pdf(tmp_path / "xray.pdf", lambda p: p.image((60, 200, 300, 440), png))

    assert [colour for _, colour in visual.pictures(page_view(src))] == [False]
    # left visible: its text goes to OCR
    assert visual.embedded_images(page_view(src)) == []


def test_a_colour_photo_is_redacted_whole(tmp_path: Path) -> None:
    photo = np.zeros((200, 200, 3), np.uint8)
    photo[..., 0], photo[..., 1] = 200, 80  # saturated orange skin-like tone
    png = _png(photo)
    src = draw_pdf(tmp_path / "photo.pdf", lambda p: p.image((60, 200, 260, 400), png))

    assert [f.label for f in visual.embedded_images(page_view(src))] == ["FACE"]


def _token(text: str) -> Token:
    chars = [(5.0 * i, 0.0, 5.0 * i + 5, 10.0) for i in range(len(text))]
    return Token(text, (0.0, 0.0, 5.0 * len(text), 10.0), chars, 0, "native")


def test_words_glued_by_a_scanner_ocr_layer_are_split() -> None:
    parts = split_glued([_token("prevpt:LindaMarshRX-550-2877")])

    assert [t.text for t in parts] == ["prevpt:", "Linda", "Marsh", "RX-550-2877"]
    assert parts[1].rect == (35.0, 0.0, 60.0, 10.0)  # the boxes of its own characters


def test_mixed_case_names_and_terms_stay_whole() -> None:
    words = ["McDonald", "DiMaio", "HbA1c", "SerD", "OMCeO", "eGFR", "NaCl"]

    assert [t.text for t in split_glued([_token(w) for w in words])] == words


def test_a_speck_read_before_a_cell_character_does_not_break_the_code() -> None:
    # a dirty scan: OCR reads one cell as ".1" (a speck before the digit)
    cells = list("80380") + [".1"] + list("60196130540595")
    words = [
        Word(c, (100 + 12 * i, 200, 110 + 12 * i, 212), 0.9, "ocr", i)
        for i, c in enumerate(cells)
    ]

    assert ocr_layer(0, words).tokens[0].text == "80380160196130540595"
