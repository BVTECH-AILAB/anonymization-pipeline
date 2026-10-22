"""Wire models of the OCR API (LiteParse ``POST /ocr`` plus our ``POST /recognize``)."""

from pydantic import BaseModel


class OcrResult(BaseModel):
    """One word: pixels of the image sent, top-left origin."""

    text: str
    bbox: list[float]
    confidence: float
    # four corners, TL -> TR the reading direction; absent: left to right
    polygon: list[list[float]] | None = None


class OcrResponse(BaseModel):
    results: list[OcrResult]


class RecognizeResponse(BaseModel):
    text: str
    confidence: float


class HealthResponse(BaseModel):
    status: str
