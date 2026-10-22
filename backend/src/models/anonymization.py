"""Wire models of the anonymization API, shaped like the document agent's.

A session is created by the upload; its work runs inside an SSE stream whose
frames are ``status`` (``StatusEvent``), ``done`` (``AnonymizationTurnResponse``
or ``EditTurnResponse``) and ``error`` (``AnonymizationErrorEvent``).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.services.anonymization import Mode, Status


class LabelView(BaseModel):
    """One PII label: Italian title, kind and default policy."""

    code: str
    title: str
    kind: Literal["text", "visual"]
    redact: bool


class AnonymizationSessionResponse(BaseModel):
    """A session: its state and, once done, the result (``verified`` says if it can be released)."""

    session_id: str
    mode: Mode
    status: Status
    step: str
    percent: int
    error_code: str | None = None
    pages: int
    size: int
    result: dict[str, Any] | None = None


class StatusEvent(BaseModel):
    """SSE frame ``status``: progress of the running step."""

    step: str
    percent: int
    text: str


class AnonymizationTurnResponse(BaseModel):
    """SSE frame ``done`` of an anonymization."""

    session: AnonymizationSessionResponse
    elapsed_ms: int


class EditTurnResponse(BaseModel):
    """SSE frame ``done`` of an edit."""

    session: AnonymizationSessionResponse
    search_hits: int
    edit_seconds: float
    elapsed_ms: int


class AnonymizationErrorEvent(BaseModel):
    """SSE frame ``error``: generic Italian title and message, stable code."""

    error_title: str
    error_message: str
    error_code: str


Coordinate = Annotated[float, Field(ge=-100, le=20000, allow_inf_nan=False)]


class AreaEdit(BaseModel):
    page: int = Field(ge=0, le=500)
    rect: tuple[Coordinate, Coordinate, Coordinate, Coordinate]


class EditRequest(BaseModel):
    add: list[AreaEdit] = Field(default_factory=list, max_length=500)
    remove: list[int] = Field(default_factory=list, max_length=5000)
    restore: list[int] = Field(default_factory=list, max_length=5000)
    search: str = Field(default="", max_length=200)


class ReviewPage(BaseModel):
    """One page: size in PDF points (display space) and the URLs of its two renderings."""

    index: int
    width: float
    height: float
    original: str
    anonymized: str


class ReviewBox(BaseModel):
    """A detected or manual area; ``rect`` is ``(x0, y0, x1, y1)`` in points, top-left origin."""

    id: int
    page: int
    rect: tuple[float, float, float, float]
    label: str
    layer: str
    redact: bool


class ReviewManifest(BaseModel):
    """Everything the side-by-side review needs; ``version`` changes with every edit."""

    session_id: str
    mode: Mode
    version: int
    verified: bool
    pages: list[ReviewPage]
    boxes: list[ReviewBox]
    labels: dict[str, str]
