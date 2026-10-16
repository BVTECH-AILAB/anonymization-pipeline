"""Text layout and visual marks: character boxes, vector signatures and stamps, clinical pictures."""

import io

import numpy as np
import pymupdf
from PIL import Image

from anonmyzation_pipeline.services.anonymization.detect import visual
from anonmyzation_pipeline.services.anonymization.layers import (
    Token,
    Word,
    native_layer,
    ocr_layer,
    split_glued,
)
from tests.conftest import FAKE_CF


def _page() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    return doc, doc.new_page(width=595, height=842)


def test_characters_printed_one_per_box_become_one_word() -> None:
    doc, page = _page()
    for i, ch in enumerate(FAKE_CF):  # one cell per character, as on paper forms
        page.draw_rect(pymupdf.Rect(50 + i * 15, 100, 65 + i * 15, 116))
        page.insert_text((54 + i * 15, 112), ch, fontsize=11)
    page.insert_text(
        (50, 200), "e a i o u sono vocali in una frase normale", fontsize=11
    )

    tokens = [t.text for t in native_layer(page).tokens]

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


def test_vector_signature_and_stamp_frame_are_found_and_logos_ignored() -> None:
    doc, page = _page()
    shape = page.new_shape()
    for k in range(3):  # a handwritten signature: an open stroke of curves
        shape.draw_bezier(
            (60 + 60 * k, 420),
            (80 + 60 * k, 400),
            (100 + 60 * k, 440),
            (120 + 60 * k, 420),
        )
    shape.finish(color=(0.1, 0.15, 0.4), width=0.9, closePath=False)
    shape.draw_rect(pymupdf.Rect(300, 400, 450, 460), radius=0.1)  # stamp frame
    shape.finish(color=(0.3, 0.2, 0.6), width=1.2)
    shape.draw_circle((60, 60), 15)  # filled logo
    shape.finish(fill=(0.2, 0.4, 0.6), color=None)
    shape.commit()

    labels = sorted(f.label for f in visual.vector_marks(page))

    assert labels == ["SIGNATURE", "STAMP"]


def _png(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array).save(buf, "PNG")
    return buf.getvalue()


def test_a_grayscale_xray_is_a_picture_not_a_stamp() -> None:
    doc, page = _page()
    rng = np.random.default_rng(0)
    xray = (rng.random((300, 300)) * 120).astype(np.uint8)  # dark, textured
    page.insert_image(
        pymupdf.Rect(60, 200, 300, 440), stream=_png(np.stack([xray] * 3, -1))
    )

    assert [colour for _, colour in visual.pictures(page)] == [False]
    assert visual.embedded_images(page) == []  # left visible: its text goes to OCR


def test_a_colour_photo_is_redacted_whole() -> None:
    doc, page = _page()
    photo = np.zeros((200, 200, 3), np.uint8)
    photo[..., 0], photo[..., 1] = 200, 80  # saturated orange skin-like tone
    page.insert_image(pymupdf.Rect(60, 200, 260, 400), stream=_png(photo))

    assert [f.label for f in visual.embedded_images(page)] == ["FACE"]


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
