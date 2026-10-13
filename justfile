# Task runner. `just` lists the recipes.

# Reads .env: no credentials in this file.
set dotenv-load := true

default:
    @just --list

# --- Compose (service + Ollama + web UI) ---

# Download the models into the volumes: the only step with internet access (run it first)
models:
    docker compose --profile models build models-init
    docker compose --profile models run --rm models-init
    docker compose --profile models rm --stop --force ollama-models

# Build and start the stack in the background, VLM on CPU (slow: for trials only)
up:
    docker compose up --build -d

# Build and start the stack with the VLM on an NVIDIA GPU
up-gpu:
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build -d

down:
    docker compose down

# Stop the stack and delete the volumes (models included)
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

# --- Quality ---

format:
    uv run ruff format

fix:
    uv run ruff check --fix

types:
    uv run ty check src tests validation

# Fix, format and type-check: run before every commit
lint: fix format types

# CI mode: no fixes, fails on any finding
check:
    uv run ruff check
    uv run ruff format --check
    uv run ty check src tests validation

# --- Tests ---

# Unit tests (no network)
test *args:
    uv run pytest {{args}}

# Integration tests against the compose Ollama (`just models` and `just up` first)
test-vlm *args:
    docker compose exec app pytest -m integration -s {{args}}

# Unit tests with coverage (fails under 80%), writes coverage.xml and junit.xml for Sonar
test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable

# --- Validation (validation/README.md) ---

# Synthetic dataset: native PDFs and ground truth from validation/dataset/specs
validation-dataset *args:
    PYTHONPATH=validation uv run python -m anonval.build {{args}}

# Validation run against the service (`just up` first, with ANONPIP_ALLOW_TRANSCRIPT=true)
validation *args:
    bash validation/validate.sh {{args}}

# Validation API on the host, for the "Validazione" section of `npm run dev` (port 8001)
validation-api:
    PYTHONPATH=validation uv run uvicorn anonval.api:app --factory --reload --reload-dir validation --port 8001

# --- Docs ---

# Technical documentation PDF (docs/architettura-anonymization-pipeline.pdf, BV TECH style)
docs-pdf:
    uv run scripts/docs/architettura.py
