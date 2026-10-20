"""``python -m paddle_ocr_server``: serve the OCR API on port 8829."""

import logging

import uvicorn

from paddle_ocr_server.app import create_app
from paddle_ocr_server.config import get_settings


def main() -> None:
    """Configure logging and serve."""
    settings = get_settings()
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    # uvicorn access lines carry no body, but the OCR text never reaches them either way
    uvicorn.run(create_app(settings), host="0.0.0.0", port=8829, log_config=None)  # noqa: S104


if __name__ == "__main__":
    main()
