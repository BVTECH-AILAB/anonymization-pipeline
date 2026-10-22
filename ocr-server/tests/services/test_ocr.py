"""Reading a page: margins, pages and lines upside down, noise."""

import numpy as np
import pytest

from src.services.ocr import margin_results, page_results
from src.services.words import Line, Quad


def test_margin_pass_keeps_the_confident_rotation() -> None:
    img = np.zeros((300, 200, 3), np.uint8)
    upright = ((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0))
    calls: list[tuple[int, int]] = []

    def predict(strip: np.ndarray) -> list[Line]:
        calls.append(strip.shape[:2])
        # first call of each strip is the clockwise rotation: make it the good one
        return [Line("Mario Rossi", 0.9 if len(calls) % 2 else 0.3, upright)]

    out = margin_results(img, 20, predict, 0.5)
    assert calls == [(20, 300)] * 4  # rotated strips: 20 px high, page-height wide
    assert [r["text"] for r in out] == ["Mario", "Rossi", "Mario", "Rossi"]
    # clockwise reading: bottom to top on the page, inside the strip
    left = out[0]["polygon"]
    assert left[1][1] < left[0][1]
    assert all(0 <= x <= 20 for x, _ in left)
    right = out[2]["bbox"]
    assert right[0] >= 180


def test_page_results_margin_width_follows_dpi() -> None:
    img = np.zeros((100, 400, 3), np.uint8)
    widths: list[int] = []

    def predict(a: np.ndarray) -> list[Line]:
        widths.append(a.shape[0])
        return [Line("Referto", 0.95, _quad(10, 10, 80, 20))]

    page_results(img, 144, predict, margin_pt=36, min_score=0.5)
    assert widths == [100, 72, 72, 72, 72]  # page, then 36 pt at 144 dpi = 72 px strips
    widths.clear()
    page_results(img, 144, predict, margin_pt=0, min_score=0.5)
    assert widths == [100]


def _quad(x0: float, y0: float, x1: float, y1: float) -> Quad:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _texts(found: list[dict]) -> list[str]:
    return [r["text"] for r in found]


def test_upright_page_is_read_once() -> None:
    calls: list[tuple[int, ...]] = []

    def predict(img: np.ndarray) -> list[Line]:
        calls.append(img.shape)
        return [Line("Rossi", 0.98, _quad(10, 10, 60, 20))]

    assert _texts(page_results(np.zeros((800, 600, 3), np.uint8), 72, predict, 0, 0.5))
    assert len(calls) == 1


def test_upside_down_page_is_read_turned_and_mapped_back() -> None:
    # the text sits in the bottom-right corner, upside down: turned by 180 degrees
    # it is in the top-left corner, where the fake engine reads it
    img = np.full((800, 600, 3), 255, np.uint8)
    img[760:780, 500:560] = 0

    def predict(a: np.ndarray) -> list[Line]:
        h, w = a.shape[:2]
        top_left = a[: h // 4, : w // 4]
        if top_left.min() < 128:
            ys, xs = np.nonzero(top_left.min(axis=2) < 128)
            box = _quad(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
            return [Line("Rossi", 0.97, box)]
        return [Line("e e r", 0.45, _quad(0, 0, w / 2, 10))]

    (word,) = page_results(img, 72, predict, 0, 0.5)
    assert word["text"] == "Rossi"
    assert word["bbox"] == pytest.approx([500, 760, 560, 780], abs=1)
    # TL -> TR runs right to left: the reading frame of text upside down
    assert word["polygon"][1][0] < word["polygon"][0][0]


def test_poor_reading_is_kept_when_the_turned_page_is_not_better() -> None:
    def predict(_: np.ndarray) -> list[Line]:
        return [Line("Bianchi", 0.6, _quad(10, 10, 60, 20))]

    (word,) = page_results(np.zeros((800, 600, 3), np.uint8), 72, predict, 0, 0.5)
    assert word["text"] == "Bianchi"
    assert word["polygon"][1][0] > word["polygon"][0][0]


def test_a_degraded_page_is_not_swapped_for_a_noisy_turned_reading() -> None:
    # a faded fax read poorly: turned, it gives more text but just as unsure
    img = np.full((800, 600, 3), 255, np.uint8)
    img[770:790, 540:580] = 0

    def predict(a: np.ndarray) -> list[Line]:
        if a[760:, 520:].min() < 128:  # the marker sits bottom-right: upright
            return [Line("Bianchi", 0.6, _quad(10, 10, 60, 20))]
        return [Line("eerr nnee aaee rrnn", 0.7, _quad(10, 10, 200, 20))]

    assert _texts(page_results(img, 72, predict, 0, 0.5)) == ["Bianchi"]


def _sticker(_: np.ndarray) -> list[Line]:
    """A page read well, but for a sticker pasted upside down read as noise."""
    return [
        Line("Paziente: ROSSI", 0.99, _quad(10, 10, 140, 20)),
        Line("e", 0.31, _quad(200, 100, 260, 112)),
        Line("rr", 0.28, _quad(270, 100, 330, 112)),
        Line("Ricovero", 0.97, _quad(10, 200, 80, 212)),
    ]


def test_a_line_upside_down_on_an_upright_page_is_read_turned() -> None:
    seen: list[np.ndarray] = []

    def recognize(img: np.ndarray) -> tuple[str, float]:
        seen.append(img)
        return "Mario Rossi", 0.96

    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, _sticker, 0, 0.5, recognize)

    assert _texts(found) == ["Paziente:", "ROSSI", "Mario", "Rossi", "Ricovero"]
    mario, rossi = found[2]["bbox"], found[3]["bbox"]
    # upside down the text runs right to left: the first word is on the right
    assert mario[0] > rossi[2] - 1
    assert rossi[0] == pytest.approx(200, abs=1)
    assert mario[2] == pytest.approx(330, abs=1)
    # the pieces are passed as one crop, turned, at its own size
    assert len(seen) == 1 and seen[0].shape[1] >= 130


def test_pieces_of_an_upside_down_line_are_read_again_together() -> None:
    # the detector cuts a line upside down into pieces; one piece is read just
    # above the noise threshold ("1138" for "1176"): the pieces on the band are
    # read again as one crop, the confident line below is left alone
    def predict(_: np.ndarray) -> list[Line]:
        return [
            Line("326", 0.14, _quad(200, 100, 240, 112)),
            Line("808", 0.21, _quad(246, 100, 286, 112)),
            Line("1138", 0.51, _quad(292, 100, 330, 112)),
            Line("Cl1", 0.21, _quad(336, 100, 360, 112)),
            Line("Ricovero", 0.97, _quad(200, 130, 280, 142)),
        ]

    seen: list[np.ndarray] = []

    def recognize(img: np.ndarray) -> tuple[str, float]:
        seen.append(img)
        return "Tel. 348 905 1176", 0.97

    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, predict, 0, 0.5, recognize)

    assert _texts(found) == ["Tel.", "348", "905", "1176", "Ricovero"]
    assert len(seen) == 1 and seen[0].shape[1] >= 160


@pytest.mark.parametrize("recognize", [None, lambda _: ("x7 ;", 0.4)])
def test_a_noise_line_that_is_not_upside_down_is_dropped(recognize) -> None:
    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, _sticker, 0, 0.5, recognize)
    assert _texts(found) == ["Paziente:", "ROSSI", "Ricovero"]
