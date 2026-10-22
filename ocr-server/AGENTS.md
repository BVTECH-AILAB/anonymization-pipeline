# AGENTS.md

Instructions for coding agents working on the OCR server, the second pod of the
anonymization service. The contract it serves is
`../backend/src/services/anonymizer/OCR_CONTRACT.md`: change it only together
with both sides' tests.

## Project

PP-OCRv5 on RapidOCR (ONNX Runtime, CPU or GPU) behind the LiteParse OCR API:
`POST /ocr` (every word of a page image), `POST /recognize` (one line,
recognition only), `GET /health` (503 until the models are loaded). Port 8829.
Only the backend calls it; it is never exposed.

## Layout

- `src/router/`: HTTP only: `ocr.py` (`/ocr`, `/recognize`), `health.py`,
  `errors.py` (`{"error": "<generic message>"}`)
- `src/services/ocr.py`: how a page is read: full page, side margins rotated,
  pages and lines upside down, noise; `services/words.py`: words from lines
- `src/llms/rapidocr.py`: the model: loading, detection and recognition, GPU memory
- `src/models/ocr.py`: wire models; `src/errors.py`: errors with status and message
- `src/config.py`: `Settings` (prefix `OCRSRV_`); `src/lifespan.py`: loads the
  engine at startup; `src/main.py`: `create_app()`
- `tests/` mirrors `src/`; `tests/conftest.py` has the fake engine

## Rules

- Page images are health data: never log the text read, never write images to disk.
- Errors answer a generic message, never the exception of the engine.
- Python 3.12, type hints, Google-style docstrings, imports at module level only.
- `/ocr`, `/recognize`, `/health` and their bodies are a contract with the backend.

## Commands

From `ocr-server/` (or `just ocr::<recipe>` from the root); the first argument
is the ONNX Runtime extra (`gpu` by default, `cpu` without a GPU):

```bash
just lint        # ruff fix + format + ty
just test cpu    # unit tests, no model weights
just serve gpu   # the server on the host, port 8829
```
