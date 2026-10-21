"""Visual detectors with fake models: no weights, no network."""

from pathlib import Path

import numpy as np

from anonymization_core.detect import visual
from anonymization_core.layers import Word
from anonymization_core.testing import letter_pdf, native_words, page_view, scanned_pdf


def test_visual_codes_and_signatures_on_native_pages(tmp_path: Path) -> None:
    src = letter_pdf(
        tmp_path / "in.pdf", qr_text="https://x.example.it/r?id=1", signature=True
    )
    view = page_view(src)
    found = visual.embedded_images(view)
    codes = visual.codes(view)

    assert sorted(f.label for f in found) == ["QRCODE", "SIGNATURE"]
    assert [c.label for c in codes] == ["QRCODE"]


def test_stamp_needs_a_frame() -> None:
    img = np.full((80, 200, 3), 255, np.uint8)
    img[2:5, :] = img[-5:-2, :] = (40, 50, 160)
    img[:, 2:5] = img[:, -5:-2] = (40, 50, 160)
    img[35:45, 30:170] = (40, 50, 160)

    assert visual._ink_class(img)[0] == "STAMP"
    img[:, :8] = img[:, -8:] = 255
    img[:8, :] = 255
    assert visual._ink_class(img)[0] == "SIGNATURE"


def test_unexplained_ink_finds_a_signature_on_a_scan(tmp_path: Path) -> None:
    native = letter_pdf(tmp_path / "native.pdf")
    scan = scanned_pdf(native, tmp_path / "scan.pdf", scribble=True)
    words = native_words(native)[0]

    found = visual.unexplained_ink(page_view(scan), words)

    assert any(
        f.rect[0] < 330 and f.rect[2] > 400 and 690 < f.rect[3] < 760 for f in found
    )


def test_stamp_from_text() -> None:
    words = [
        Word("Dott.", (400, 100, 425, 110)),
        Word("Bianchi", (428, 100, 470, 110)),
        Word("Medico", (400, 112, 435, 122)),
        Word("Chirurgo", (438, 112, 480, 122)),
        Word("OMCeO", (400, 124, 435, 134)),
        Word("lontano", (100, 600, 140, 610)),
    ]

    stamps = visual.stamp_from_text(0, words)

    assert len(stamps) == 1
    assert stamps[0].rect[0] < 400 and stamps[0].rect[3] > 134
