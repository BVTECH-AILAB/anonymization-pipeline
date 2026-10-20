import cv2
import numpy as np

from paddle_ocr_server.words import (
    Line,
    is_vertical,
    line_words,
    proportional_words,
    results,
    rotate_back,
)

H_QUAD = ((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0))


def test_proportional_split_follows_characters() -> None:
    words = proportional_words("ab cd", H_QUAD)
    assert [w for w, _ in words] == ["ab", "cd"]
    (_, q1), (_, q2) = words
    assert q1[0] == (0.0, 0.0) and q1[1] == (40.0, 0.0)
    assert q2[0] == (60.0, 0.0) and q2[2] == (100.0, 10.0)


def test_proportional_split_keeps_reading_frame_of_vertical_text() -> None:
    # bottom-to-top line: TL -> TR goes up the page
    quad = ((0.0, 100.0), (0.0, 0.0), (10.0, 0.0), (10.0, 100.0))
    (w1, q1), (w2, q2) = proportional_words("ab cd", quad)
    assert (w1, w2) == ("ab", "cd")
    assert q1[0] == (0.0, 100.0) and q1[1] == (0.0, 60.0)
    assert q2[1] == (0.0, 0.0)


def test_recogniser_boxes_used_when_they_match_the_text() -> None:
    box_a = ((0.0, 0.0), (30.0, 0.0), (30.0, 10.0), (0.0, 10.0))
    space = ((30.0, 0.0), (35.0, 0.0), (35.0, 10.0), (30.0, 10.0))
    box_b = ((35.0, 0.0), (100.0, 0.0), (100.0, 10.0), (35.0, 10.0))
    line = Line(
        "Mario Rossi", 0.9, H_QUAD, [("Mario", box_a), (" ", space), ("Rossi", box_b)]
    )
    assert [w for w, _ in line_words(line)] == ["Mario", "Rossi"]


def test_mismatching_recogniser_boxes_fall_back_to_proportional() -> None:
    line = Line("Mario Rossi", 0.9, H_QUAD, [("MarioRossi", H_QUAD)])
    assert [w for w, _ in line_words(line)] == ["Mario", "Rossi"]


def test_results_one_per_word_and_vertical_lines_whole() -> None:
    vertical = ((0.0, 0.0), (10.0, 0.0), (10.0, 300.0), (0.0, 300.0))
    out = results(
        [
            Line("Mario Rossi", 0.91, H_QUAD),
            Line("Stampato da Rossi", 0.8, vertical),
            Line("  ", 0.9, H_QUAD),
        ]
    )
    assert [r["text"] for r in out] == ["Mario", "Rossi", "Stampato da Rossi"]
    assert out[0]["bbox"] == [0.0, 0.0, 100 * 5 / 11, 10.0]
    assert out[0]["confidence"] == 0.91
    assert len(out[0]["polygon"]) == 4
    assert is_vertical(vertical) and not is_vertical(H_QUAD)


def _corners_after_rotation(
    w: int, h: int, rot: int
) -> dict[tuple[int, int], tuple[float, float]]:
    """Where each strip pixel lands after cv2.rotate: rotated -> original."""
    strip = np.zeros((h, w), np.int32)
    for y in range(h):
        for x in range(w):
            strip[y, x] = y * w + x
    rotated = cv2.rotate(strip, rot)
    out = {}
    for yr in range(rotated.shape[0]):
        for xr in range(rotated.shape[1]):
            v = int(rotated[yr, xr])
            out[(xr, yr)] = (float(v % w), float(v // w))
    return out


def test_rotate_back_inverts_cv2_rotations() -> None:
    w, h, x_off = 4, 6, 10
    for rot, clockwise in (
        (cv2.ROTATE_90_CLOCKWISE, True),
        (cv2.ROTATE_90_COUNTERCLOCKWISE, False),
    ):
        mapping = _corners_after_rotation(w, h, rot)
        for (xr, yr), (x, y) in mapping.items():
            # pixel centres: the box of pixel (xr, yr) is [xr, xr+1] x [yr, yr+1]
            quad = ((xr, yr), (xr + 1, yr), (xr + 1, yr + 1), (xr, yr + 1))
            back = rotate_back(quad, clockwise, w, h, x_off)
            xs = [p[0] for p in back]
            ys = [p[1] for p in back]
            assert (min(xs), min(ys)) == (x + x_off, y)
            assert (max(xs), max(ys)) == (x + x_off + 1, y + 1)


def test_rotate_back_keeps_reading_direction() -> None:
    # text upright on the strip rotated clockwise reads bottom to top on the page
    quad = ((0.0, 0.0), (50.0, 0.0), (50.0, 10.0), (0.0, 10.0))
    tl, tr, _, _ = rotate_back(quad, True, 20, 200, 0)
    assert tr[1] < tl[1] and tr[0] == tl[0]
    tl, tr, _, _ = rotate_back(quad, False, 20, 200, 0)
    assert tr[1] > tl[1]


def test_recogniser_boxes_are_widened_to_whole_characters() -> None:
    # PaddleX word boxes span CTC columns (about h/6 wide each), not glyph edges:
    # a single digit in a 1.4 pt box. As in RapidOCR, each word covers from half a
    # character before its first column centre to half a character after its last.
    line_quad = ((100.0, 0.0), (130.0, 0.0), (130.0, 36.0), (100.0, 36.0))
    ctc = ((112.0, 0.0), (118.0, 0.0), (118.0, 36.0), (112.0, 36.0))
    (word,) = line_words(Line("8", 1.0, line_quad, [("8", ctc)]))
    assert word[0] == "8"
    (x0, _), (x1, _), _, _ = word[1]
    assert (x0, x1) == (100.0, 130.0)  # the whole line: one character

    line_quad = ((0.0, 0.0), (220.0, 0.0), (220.0, 30.0), (0.0, 30.0))
    a = ((10.0, 0.0), (95.0, 0.0), (95.0, 30.0), (10.0, 30.0))
    b = ((125.0, 0.0), (210.0, 0.0), (210.0, 30.0), (125.0, 30.0))
    w1, w2 = line_words(
        Line("Mario Rossi", 0.9, line_quad, [("Mario", a), ("Rossi", b)])
    )
    pitch = 220 / 11
    assert w1[1][0][0] == 10 + 2.5 - pitch / 2 and w1[1][1][0] == 95 - 2.5 + pitch / 2
    assert w2[1][1][0] == 210 - 2.5 + pitch / 2
    assert w1[1][1][0] <= w2[1][0][0]  # no overlap


def test_widened_boxes_stay_inside_the_line_and_apart() -> None:
    line_quad = ((0.0, 0.0), (40.0, 0.0), (40.0, 12.0), (0.0, 12.0))
    a = ((0.0, 0.0), (12.0, 0.0), (12.0, 12.0), (0.0, 12.0))
    b = ((14.0, 0.0), (40.0, 0.0), (40.0, 12.0), (14.0, 12.0))
    w1, w2 = line_words(Line("ab cde", 0.9, line_quad, [("ab", a), ("cde", b)]))
    assert w1[1][0][0] >= 0 and w2[1][1][0] <= 40
    assert w1[1][1][0] == w2[1][0][0]  # overlap split in the middle
