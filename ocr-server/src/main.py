"""Application factory and ASGI entry point (``src.main:app``, port 8829)."""

from fastapi import FastAPI

from src.config import Settings, get_settings
from src.lifespan import lifespan
from src.llms.rapidocr import RapidEngine
from src.router import health, ocr
from src.router.errors import register_exception_handlers
from src.services.ocr import OcrEngine
from src.utils.logging_setup import setup_logging


def create_app(
    settings: Settings | None = None, engine: OcrEngine | None = None
) -> FastAPI:
    """Build the OCR application.

    Args:
        settings: Server settings (default: from the environment).
        engine: OCR engine (default: RapidOCR, loaded at startup).
    """
    settings = settings or get_settings()
    app = FastAPI(
        title="paddle-ocr-server", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.settings = settings
    app.state.engine = engine or RapidEngine(settings)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(ocr.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings.log_level)
    return create_app(settings)


app = build_app()
