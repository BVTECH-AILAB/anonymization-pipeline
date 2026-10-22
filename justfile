# Task runner of the repository: the compose stack, and each service's own justfile
# as a module: `just backend::test`, `just ocr::serve`. `just --list --list-submodules`
# lists everything.

# Reads OCR_EXTRA and the compose ports from .env: no credentials in this file.
set dotenv-load := true

# The anonymization service (backend/justfile)
mod backend
# The OCR server (ocr-server/justfile)
mod ocr 'ocr-server'

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
