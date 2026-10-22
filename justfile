# Task runner of the repository: the compose stack, and each service's own justfile
# as a module: `just backend::test`, `just backend::evaluate`. `just --list --list-submodules`
# lists everything.

# Reads OCR_EXTRA, OCRSRV_* and the compose ports from .env: no credentials in this file.
set dotenv-load := true

# The anonymization service (backend/justfile)
mod backend

default:
    @just --list

# --- Compose (service + OCR server) ---

# Build and start the stack in the background
up:
    docker compose up --build -d

down:
    docker compose down

# Stop the stack and delete the volumes (the cached model weights)
down-v:
    docker compose down -v

logs:
    docker compose logs -f app

# Local run: OCR server on the host unless one answers, API on the first free port, OpenAPI page in the browser
start:
    bash scripts/start.sh

# OCR server on the host, port 8829 (extra: gpu | cpu); `just backend::dev` and `just backend::evaluate` need it
ocr-server extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} python -m paddle_ocr_server

# Unit tests of the OCR server (its own venv: ONNX Runtime stays out of the service)
ocr-server-test extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} pytest -q
