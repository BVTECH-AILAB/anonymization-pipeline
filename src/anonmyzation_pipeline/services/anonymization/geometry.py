"""Rectangles as ``(x0, y0, x1, y1)`` tuples, top-left origin, in PDF points.

Plain arithmetic, so the pipeline does not depend on a PDF library for it. A
rectangle with no width or no height is empty: it never intersects anything
and adds nothing to a union.
"""

from collections.abc import Iterable

Rect = tuple[float, float, float, float]


def is_empty(r: Rect) -> bool:
    """True when ``r`` has no area."""
    return r[2] <= r[0] or r[3] <= r[1]


def union(rects: Iterable[Rect]) -> Rect | None:
    """Smallest rectangle containing every non-empty rectangle, or None if there is none."""
    out: Rect | None = None
    for r in rects:
        if is_empty(r):
            continue
        out = (
            r
            if out is None
            else (
                min(out[0], r[0]),
                min(out[1], r[1]),
                max(out[2], r[2]),
                max(out[3], r[3]),
            )
        )
    return out


def intersection(a: Rect, b: Rect) -> Rect:
    """Overlap of ``a`` and ``b`` (empty when they do not overlap)."""
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def intersects(a: Rect, b: Rect) -> bool:
    """True when ``a`` and ``b`` share some area."""
    return not is_empty(a) and not is_empty(b) and not is_empty(intersection(a, b))


def area(r: Rect) -> float:
    """Area of ``r`` (0 when empty)."""
    return 0.0 if is_empty(r) else (r[2] - r[0]) * (r[3] - r[1])


def overlap_area(a: Rect, b: Rect) -> float:
    """Area shared by ``a`` and ``b``."""
    return area(intersection(a, b))


def grow(r: Rect, d: float) -> Rect:
    """``r`` with ``d`` points added on every side (shrunk when negative)."""
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def contains_point(r: Rect, x: float, y: float) -> bool:
    """True when the point lies inside ``r`` (edges included)."""
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


def to_display(rect: Rect, info: dict) -> Rect:
    """A page-space box as shown on the rendered page (``info``: an ``Analysis.pages`` item)."""
    x0, y0, x1, y1 = rect
    w, h = (
        info.get("page_width", info["width"]),
        info.get("page_height", info["height"]),
    )
    rotation = info.get("rotation", 0)
    if rotation == 90:
        return (h - y1, x0, h - y0, x1)
    if rotation == 180:
        return (w - x1, h - y1, w - x0, h - y0)
    if rotation == 270:
        return (y0, w - x1, y1, w - x0)
    return rect


def from_display(rect: Rect, info: dict) -> Rect:
    """The inverse of ``to_display``: an area drawn on the rendered page, in page space."""
    x0, y0, x1, y1 = rect
    w, h = (
        info.get("page_width", info["width"]),
        info.get("page_height", info["height"]),
    )
    rotation = info.get("rotation", 0)
    if rotation == 90:
        return (y0, h - x1, y1, h - x0)
    if rotation == 180:
        return (w - x1, h - y1, w - x0, h - y0)
    if rotation == 270:
        return (w - y1, x0, w - y0, x1)
    return rect


# reading direction -> unit vector on the page (y grows downwards)
DIRECTIONS = {"ltr": (1, 0), "rtl": (-1, 0), "ttb": (0, 1), "btt": (0, -1)}


def direction_from_display(direction: str, rotation: int) -> str:
    """The page-space reading direction of text that reads ``direction`` on the
    rendering of a page rotated by ``rotation`` degrees."""
    dx, dy = DIRECTIONS[direction]
    turns = {0: (dx, dy), 90: (dy, -dx), 180: (-dx, -dy), 270: (-dy, dx)}
    vx, vy = turns.get(rotation % 360, (dx, dy))
    return next(k for k, v in DIRECTIONS.items() if v == (vx, vy))
