"""Layout of the censored document: styles and positions of the text, lines and rectangles.

The model is independent of where the geometry comes from (``PageLayout.source``: today the text
layer of a native PDF) and holds only numbers and enums: the text stays in the structure (a
DoclingDocument), every run points to a slice of one of its fields. Font names of the original
are never stored, only the catalog family that replaces them.

Coordinates are PDF points with the origin at the bottom left of the page.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FontKey = Literal[
    "liberation-sans",
    "liberation-serif",
    "liberation-mono",
    "carlito",
    "caladea",
    "dejavu-sans",
]
ATTACHMENT = (
    "documento.anon.layout.json"  # name of the layout attached to the censored PDF
)
MAX_GRAPHICS = 2000  # per page: over it the page keeps no graphics
MAX_RUNS = 20000  # per field
MAX_STYLES = 2000

_Channel = Annotated[int, Field(ge=0, le=255)]
Color = tuple[_Channel, _Channel, _Channel]
Coordinate = Annotated[float, Field(ge=-10_000, le=100_000)]
Length = Annotated[float, Field(ge=0, le=100_000)]
Offset = Annotated[int, Field(ge=0, le=10_000_000)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Style(_Model):
    """Font (catalog family), weight, slant, size in points and fill color of a run."""

    font: FontKey
    bold: bool
    italic: bool
    size: Annotated[float, Field(gt=0, le=1000)]
    color: Color


class Run(_Model):
    """A slice ``[start, end)`` of a field drawn on one baseline, ``width`` points wide.

    ``tag`` runs hold a replacement: the slice of the original value is gone, only the box it
    took on its first line is left.
    """

    start: Offset
    end: Offset
    x: Coordinate
    baseline: Coordinate
    width: Length
    style: Annotated[int, Field(ge=0, lt=MAX_STYLES)]
    tag: bool = False


class FieldLayout(_Model):
    """The runs of one field of the structure: a text item, or a table cell (``cell``)."""

    ref: Annotated[str, Field(pattern=r"^#/(texts|tables)/\d{1,7}$")]
    cell: Annotated[int, Field(ge=0)] | None
    page_no: Annotated[int, Field(ge=1)]
    runs: list[Run] = Field(max_length=MAX_RUNS)


class Graphic(_Model):
    """An axis-aligned line or rectangle (no curves: they may be glyph outlines or logos)."""

    kind: Literal["line", "rect"]
    x0: Coordinate
    y0: Coordinate
    x1: Coordinate
    y1: Coordinate
    stroke: Color | None
    fill: Color | None
    width: Annotated[float, Field(ge=0, le=100)]


class PageLayout(_Model):
    """Graphics of one page and where its geometry comes from.

    ``background`` is reserved for a raster of the page without text (not produced yet).
    """

    page_no: Annotated[int, Field(ge=1)]
    source: Literal["pdf"]
    graphics: list[Graphic] = Field(max_length=MAX_GRAPHICS)
    background: None = None


class Layout(_Model):
    """Styles, pages and fields of a document."""

    version: Literal[1] = 1
    styles: list[Style] = Field(max_length=MAX_STYLES)
    pages: list[PageLayout]
    fields: list[FieldLayout]


class LayoutError(ValueError):
    """The layout cannot be read or does not fit the structure."""


def canonical(layout: Layout) -> bytes:
    """Stable JSON of a layout (its fingerprint goes in the map)."""
    return json.dumps(
        layout.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(layout: Layout) -> str:
    return hashlib.sha256(canonical(layout)).hexdigest()


def load(data: bytes) -> Layout:
    """A layout from its JSON.

    Raises:
        LayoutError: Not a valid layout.
    """
    try:
        return Layout.model_validate_json(data)
    except ValidationError as exc:
        raise LayoutError("invalid layout") from exc
