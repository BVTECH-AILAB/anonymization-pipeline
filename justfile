# Task runner. `just` lists the recipes.

# Reads ANONPIP_* from .env: no credentials in this file.
set dotenv-load := true

default:
    @just --list

# --- Compose (service) ---

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
    uv run uvicorn anonmyzation_pipeline.main:app --reload --port 8000

# Everything for a local demo: deps, synthetic dataset if missing, API + web UI on the first free port, browser
start:
    bash scripts/start.sh

# Download the NER weights into the local cache (run once, before an offline start; the OCR server must answer)
models:
    uv run python -c "from anonmyzation_pipeline.config import get_settings; from anonmyzation_pipeline.main import build_pipeline, load_models; import sys; sys.exit(0 if load_models(build_pipeline(get_settings())) else 1)"

# --- Synthetic dataset and evaluation (tools/, dev only) ---

# Synthetic dataset: `just dataset` or `just dataset --n 7 --append`
dataset *args:
    uv run python -m tools generate {{args}}

# Score the pipeline against the ground truth -> reports/evaluation.md
evaluate *args:
    uv run python -m tools evaluate {{args}}

# Evaluation viewer: ground truth vs predictions per document, live while evaluating
eval-ui *args:
    uv run python -m tools viewer {{args}}

# Two evaluations side by side: `just eval-compare reports_a reports_b` -> reports/comparison.md
eval-compare *args:
    uv run python -m tools compare {{args}}

# OCR server on the host, port 8829 (extra: gpu | cpu); `just dev` and `just evaluate` need it
ocr-server extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} python -m paddle_ocr_server

# Unit tests of the OCR server (its own venv: ONNX Runtime stays out of the service)
ocr-server-test extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} pytest -q

# --- Quality ---

format:
    uv run ruff format

fix:
    uv run ruff check --fix

types:
    uv run ty check src tests

# Fix, format and type-check: run before every commit
lint: fix format types

# CI mode: no fixes, fails on any finding
check:
    uv run ruff check
    uv run ruff format --check
    uv run ty check src tests

# --- Tests ---

# Unit tests (no network, no model weights)
test *args:
    uv run pytest {{args}}

# Unit tests with coverage (fails under 80%), writes coverage.xml and junit.xml for Sonar
test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable
