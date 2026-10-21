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

# Demo page image (apps/web): build it and check its nginx configuration
web-check:
    docker build --quiet --tag anonpip-web:check apps/web
    docker run --rm -e API_UPSTREAM=http://127.0.0.1:1 anonpip-web:check nginx -t

# --- Local (uv) ---

sync:
    uv sync

# Run the API on the host
dev:
    uv run uvicorn anonmyzation_pipeline.main:app --reload --port 8000

# Everything for a local demo: deps, synthetic dataset if missing, API + web UI on the first free port, browser
start:
    bash scripts/start.sh

# Check the OCR server and the NER service (both must answer; nothing is downloaded)
models:
    uv run python -c "from anonmyzation_pipeline.config import get_settings; from anonymization_core.factory import build_pipeline, load_models; import sys; sys.exit(0 if load_models(build_pipeline(get_settings().pipeline_config())) else 1)"

# --- Synthetic dataset and evaluation (apps/eval, dev only; run from the root: dataset/ and reports/ live here) ---

# Synthetic dataset: `just dataset` or `just dataset --n 7 --append`
dataset *args:
    uv run python -m anonymization_eval generate {{args}}

# NER timeout of evaluate and eval-ui, above the one in .env (for the API): a slow page must not
# stop a run of hours. Change it with `EVAL_NER_TIMEOUT_SECONDS=300 just evaluate`.
eval_ner_timeout := env("EVAL_NER_TIMEOUT_SECONDS", "120")

# Score the pipeline against the ground truth -> reports/evaluation.md
evaluate *args:
    ANONPIP_NER_TIMEOUT_SECONDS={{eval_ner_timeout}} uv run python -m anonymization_eval evaluate {{args}}

# Evaluation viewer: ground truth vs predictions per document, live while evaluating
eval-ui *args:
    ANONPIP_NER_TIMEOUT_SECONDS={{eval_ner_timeout}} uv run python -m anonymization_eval viewer {{args}}

# Two evaluations side by side: `just eval-compare reports_a reports_b` -> reports/comparison.md
eval-compare *args:
    uv run python -m anonymization_eval compare {{args}}

# OCR server on the host, port 8829 (extra: gpu | cpu); `just dev` and `just evaluate` need it
ocr-server extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} python -m paddle_ocr_server

# Unit tests of the OCR server (its own venv: ONNX Runtime stays out of the service)
ocr-server-test extra="gpu":
    cd ocr-server && VIRTUAL_ENV= uv run --extra {{extra}} pytest -q

# --- Quality (ruff picks each package's own config: engine boundaries included) ---

core := "packages/anonymization-core"
api := "apps/api"
eval := "apps/eval"

format:
    uv run ruff format

fix:
    uv run ruff check --fix

types:
    cd {{core}} && uv run ty check src tests
    cd {{api}} && uv run ty check src tests
    cd {{eval}} && uv run ty check src tests

# Fix, format and type-check: run before every commit
lint: fix format types

# CI mode: no fixes, fails on any finding
check:
    uv run ruff check
    uv run ruff format --check
    cd {{core}} && uv run ty check src tests
    cd {{api}} && uv run ty check src tests
    cd {{eval}} && uv run ty check src tests

# --- Tests ---

# Unit tests of the engine, the service and the evaluation (no network, no model weights)
test: test-core test-api test-eval

# Engine tests alone, in its package: `just test-core -k release`
test-core *args:
    cd {{core}} && uv run pytest {{args}}

# Service tests alone, in its package: `just test-api -k edits`
test-api *args:
    cd {{api}} && uv run pytest {{args}}

# Evaluation tests alone, in its package: `just test-eval -k viewer`
test-eval *args:
    cd {{eval}} && uv run pytest {{args}}

# Engine and service with coverage (each fails under 80%), then the evaluation tests
# (dev only: not gated); coverage.xml and junit.xml per package, for Sonar
test-cov:
    cd {{core}} && uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml
    cd {{api}} && uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml
    cd {{eval}} && uv run pytest -q

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable
