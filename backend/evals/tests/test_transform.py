import numpy as np

from evals.evaluate import transform_rect


def test_identity_transform_keeps_the_box() -> None:
    m = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

    assert transform_rect((10.0, 20.0, 30.0, 40.0), m, 300 / 72) == (
        10.0,
        20.0,
        30.0,
        40.0,
    )


def test_shift_in_pixels_moves_the_box_in_points() -> None:
    s = 300 / 72
    m = np.array([[1.0, 0.0, 12.5 * s], [0.0, 1.0, -4.0 * s]])

    assert transform_rect((10.0, 20.0, 30.0, 40.0), m, s) == (22.5, 16.0, 42.5, 36.0)


def test_rotation_returns_the_bound_of_the_corners() -> None:
    # 90 degrees around the origin: (x, y) -> (-y, x)
    m = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0]])

    assert transform_rect((10.0, 20.0, 30.0, 40.0), m, 1.0) == (
        -40.0,
        10.0,
        -20.0,
        30.0,
    )
