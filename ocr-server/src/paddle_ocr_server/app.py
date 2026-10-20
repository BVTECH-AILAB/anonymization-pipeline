"""LiteParse OCR API over PP-OCRv5 on RapidOCR (see LiteParse ``OCR_API_SPEC.md``).

Derived from the reference server of run-llama/liteparse
(``ocr/paddleocr/server.py``, Apache-2.0). The main difference: one result
per word instead of one per line, plus a pass on the side margins.

The OCR text is never logged: it is health data.
"""

import io
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

import anyio
import numpy as np
from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError

from paddle_ocr_server.config import Settings, get_settings
from paddle_ocr_server.engine import RapidEngine

logger = logging.getLogger(__name__)


class Engine(Protocol):
    """What the API needs from an OCR engine."""

    @property
    def ready(self) -> bool: ...

    def load(self) -> None: ...

    def read(self, img: np.ndarray, dpi: float) -> list[dict]: ...


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(
    settings: Settings | None = None, engine: Engine | None = None
) -> FastAPI:
    """Application factory.

    Args:
        settings: Server settings (default: from the environment).
        engine: OCR engine (default: RapidOCR, loaded at startup).
    """
    settings = settings or get_settings()
    ocr: Engine = engine or RapidEngine(settings)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not ocr.ready:
            await anyio.to_thread.run_sync(ocr.load)
        yield

    app = FastAPI(
        title="paddle-ocr-server", lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.get("/health", response_model=None)
    def health() -> dict[str, str] | JSONResponse:
        if not ocr.ready:
            return _error(503, "models not loaded")
        return {"status": "healthy"}

    @app.post("/ocr", response_model=None)
    def read_image(
        file: Annotated[UploadFile, File()],
        language: Annotated[str, Form()] = "en",
        x_render_dpi: Annotated[float | None, Header()] = None,
    ) -> dict[str, Any] | JSONResponse:
        if not ocr.ready:
            return _error(503, "models not loaded")
        data = file.file.read(max_bytes + 1)
        if len(data) > max_bytes:
            return _error(400, "image too large")
        try:
            img = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return _error(400, "unreadable image")
        dpi = (
            x_render_dpi
            if x_render_dpi and 50 <= x_render_dpi <= 1200
            else settings.default_dpi
        )
        t0 = time.perf_counter()
        try:
            found = ocr.read(img, dpi)
        except Exception as exc:
            logger.error("OCR failed: error=%s", type(exc).__name__)
            return _error(500, "OCR failed")
        logger.info(
            "OCR page read: size=%dx%d dpi=%.0f words=%d seconds=%.2f language=%s",
            img.shape[1],
            img.shape[0],
            dpi,
            len(found),
            time.perf_counter() - t0,
            language[:8] if language.isalpha() else "invalid",
        )
        return {"results": found}

    return app
