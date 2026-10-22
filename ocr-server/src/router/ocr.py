"""LiteParse OCR API over PP-OCRv5 on RapidOCR (see LiteParse ``OCR_API_SPEC.md``).

Derived from the reference server of run-llama/liteparse
(``ocr/paddleocr/server.py``, Apache-2.0). The main difference: one result
per word instead of one per line, plus a pass on the side margins.
``POST /recognize`` is ours, outside the LiteParse API: recognition only, on an
image holding one line (the backend sends the characters of a row of boxes).

The OCR text is never logged: it is health data.
"""

import logging
from typing import Annotated

import anyio
import numpy as np
from fastapi import APIRouter, File, Form, Header, Request, UploadFile

from src.config import Settings
from src.errors import ImageTooLargeError, ModelsNotLoadedError, OcrFailedError
from src.models.ocr import OcrResponse, OcrResult, RecognizeResponse
from src.services.ocr import OcrEngine, load_image, read_page

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ocr"])


def _engine(request: Request) -> OcrEngine:
    """The loaded engine of the app.

    Raises:
        ModelsNotLoadedError: Missing, or its models are not loaded yet.
    """
    engine = getattr(request.app.state, "engine", None)
    if engine is None or not engine.ready:
        raise ModelsNotLoadedError()
    return engine


async def _image(file: UploadFile, settings: Settings) -> np.ndarray:
    """The uploaded image, refused above ``max_upload_mb`` before it is decoded."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageTooLargeError()
    return load_image(data)


@router.post("/ocr", response_model_exclude_none=True)
async def ocr(
    request: Request,
    file: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "en",
    x_render_dpi: Annotated[float | None, Header()] = None,
) -> OcrResponse:
    """Every word of a page image (LiteParse OCR API), one result per word."""
    engine = _engine(request)
    settings: Settings = request.app.state.settings
    img = await _image(file, settings)
    dpi = (
        x_render_dpi
        if x_render_dpi and 50 <= x_render_dpi <= 1200
        else settings.default_dpi
    )
    try:
        found = await anyio.to_thread.run_sync(
            read_page, engine, img, dpi, settings, language
        )
    except Exception as exc:
        logger.error("OCR failed: error=%s", type(exc).__name__)
        raise OcrFailedError() from None
    return OcrResponse(results=[OcrResult(**r) for r in found])


@router.post("/recognize")
async def recognize(
    request: Request, file: Annotated[UploadFile, File()]
) -> RecognizeResponse:
    """Text and confidence of an image holding one line: recognition only."""
    engine = _engine(request)
    img = await _image(file, request.app.state.settings)
    try:
        text, conf = await anyio.to_thread.run_sync(engine.recognize, img)
    except Exception as exc:
        logger.error("Recognition failed: error=%s", type(exc).__name__)
        raise OcrFailedError() from None
    return RecognizeResponse(text=text, confidence=round(float(conf), 4))
