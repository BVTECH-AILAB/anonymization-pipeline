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

# Download the OCR weights and check the NER service (run once, before an offline start)
models:
    uv run python -c "from anonmyzation_pipeline.config import get_settings; from anonmyzation_pipeline.main import build_pipeline, load_models; import sys; sys.exit(0 if load_models(build_pipeline(get_settings())) else 1)"

# --- Synthetic dataset and evaluation (tools/, dev only) ---

# Synthetic dataset: `just dataset` or `just dataset --n 7 --append`
dataset *args:
    uv run python -m tools generate {{args}}

# NER timeout of evaluate and eval-ui, above the one in .env (for the API): a slow page must not
# stop a run of hours. Change it with `EVAL_NER_TIMEOUT_SECONDS=300 just evaluate`.
eval_ner_timeout := env("EVAL_NER_TIMEOUT_SECONDS", "120")

# Score the pipeline against the ground truth -> reports/evaluation.md
evaluate *args:
    ANONPIP_NER_TIMEOUT_SECONDS={{eval_ner_timeout}} uv run python -m tools evaluate {{args}}

# Evaluation viewer: ground truth vs predictions per document, live while evaluating
eval-ui *args:
    ANONPIP_NER_TIMEOUT_SECONDS={{eval_ner_timeout}} uv run python -m tools viewer {{args}}

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
