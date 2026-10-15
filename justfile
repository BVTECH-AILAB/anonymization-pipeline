# Task runner. `just` lists the recipes.

# Reads .env: no credentials in this file.
set dotenv-load := true

default:
    @just --list

# --- Compose ---

# Build and start the stack in the background
up:
    docker compose up --build -d

down:
    docker compose down

# Stop the stack and delete the volumes
down-v:
    docker compose down -v

logs:
    docker compose logs -f app

# --- Local (uv) ---

sync:
    uv sync --all-extras

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

# Unit tests with coverage (fails under 80%), writes coverage.xml and junit.xml for Sonar
test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

# NER models into the Hugging Face cache (the only step with network); names override the configured one
models *names:
    uv run python -m anonymization_pipeline.model_store {{names}}

# Tests with the real NER models (`just models` with the names of tests/unit/test_ner.py first)
test-ner:
    uv run pytest -m ner

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable

# Runtime dependency licences: fails outside the permissive allow-list (Apache, MIT, BSD, ...)
licences:
    ./scripts/check-licences.sh

# --- Validation (validation/README.md) ---

# Synthetic dataset: native PDFs and ground truth from validation/dataset/specs
validation-dataset *args:
    PYTHONPATH=validation uv run python -m anonval.build {{args}}

# Validation run against the service (`just up` or `just dev` first)
validation *args:
    PYTHONPATH=validation uv run python -m anonval.run {{args}}
