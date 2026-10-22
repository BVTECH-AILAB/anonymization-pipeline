# Task runner. `just` lists the recipes.

# Reads ANONPIP_* from .env: no credentials in this file.
set dotenv-load := true

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

# --- Local (uv) ---

sync:
    uv sync

# Run the API on the host
dev:
    uv run uvicorn anonymization_pipeline.main:app --reload --port 8000

# Local run: deps, OCR server on the host unless one answers, API on the first free port, OpenAPI page in the browser
start:
    bash scripts/start.sh

# Check the OCR server and the NER service (both must answer; nothing is downloaded)
models:
    uv run python -c "from anonymization_pipeline.config import get_settings; from anonymization_core.factory import build_pipeline, load_models; import sys; sys.exit(0 if load_models(build_pipeline(get_settings().pipeline_config())) else 1)"

# OCR server on the host, port 8829 (extra: gpu | cpu); `just dev` needs it
ocr-server extra="gpu":
    cd services/ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} python -m paddle_ocr_server

# Unit tests of the OCR server (its own venv: ONNX Runtime stays out of the service)
ocr-server-test extra="gpu":
    cd services/ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} pytest -q

# --- Quality (ruff picks each package's own config: engine boundaries included) ---

core := "packages/anonymization-core"
api := "apps/api"

format:
    uv run ruff format

fix:
    uv run ruff check --fix

types:
    cd {{core}} && uv run ty check src tests
    cd {{api}} && uv run ty check src tests

# Fix, format and type-check: run before every commit
lint: fix format types

# CI mode: no fixes, fails on any finding
check:
    uv run ruff check
    uv run ruff format --check
    cd {{core}} && uv run ty check src tests
    cd {{api}} && uv run ty check src tests

# --- Tests ---

# Unit tests of the engine and the service (no network, no model weights)
test: test-core test-api

# Engine tests alone, in its package: `just test-core -k release`
test-core *args:
    cd {{core}} && uv run pytest {{args}}

# Service tests alone, in its package: `just test-api -k edits`
test-api *args:
    cd {{api}} && uv run pytest {{args}}

# Engine and service with coverage (each fails under 80%): coverage.xml and junit.xml per package, for Sonar
test-cov:
    cd {{core}} && uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml
    cd {{api}} && uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable
