"""Wire models of the anonymization API (requests, responses, previews)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.services.anonymization import Mode


class LabelInfo(BaseModel):
    it: str
    kind: Literal["text", "visual"]
    redact: bool


class JobCreated(BaseModel):
    id: str
    pages: int
    size: int
    mode: Mode


class JobStatus(BaseModel):
    id: str
    mode: Mode
    status: str
    step: str
    percent: int
    error_code: str | None = None
    result: dict[str, Any] | None = None


Coordinate = Annotated[float, Field(ge=-100, le=20000, allow_inf_nan=False)]


class AreaEdit(BaseModel):
    page: int = Field(ge=0, le=500)
    rect: tuple[Coordinate, Coordinate, Coordinate, Coordinate]


class EditRequest(BaseModel):
    add: list[AreaEdit] = Field(default_factory=list, max_length=500)
    remove: list[int] = Field(default_factory=list, max_length=5000)
    restore: list[int] = Field(default_factory=list, max_length=5000)
    search: str = Field(default="", max_length=200)


class PreviewPage(BaseModel):
    """One page: size in PDF points (display space) and the URLs of its two renderings."""

    index: int
    width: float
    height: float
    original: str
    anonymized: str


class PreviewBox(BaseModel):
    """A detected or manual area; ``rect`` is ``(x0, y0, x1, y1)`` in points, top-left origin."""

    id: int
    page: int
    rect: tuple[float, float, float, float]
    label: str
    layer: str
    redact: bool


class PreviewManifest(BaseModel):
    """Everything the side-by-side review needs; ``version`` changes with every edit."""

    id: str
    mode: Mode
    version: int
    verified: bool
    pages: list[PreviewPage]
    boxes: list[PreviewBox]
    labels: dict[str, str]
