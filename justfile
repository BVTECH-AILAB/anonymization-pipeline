# Task runner. `just` lists the recipes.

# Reads POSTGRES_* from .env: no credentials in this file.
set dotenv-load := true

default:
    @just --list

# --- Compose (service + Postgres) ---

# Build and start the stack in the background
up:
    docker compose up --build -d

down:
    docker compose down

# Stop the stack and delete the database volume
down-v:
    docker compose down -v

logs:
    docker compose logs -f app

db-shell:
    docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# --- Database migrations (run inside the app container) ---

migrate:
    docker compose exec app alembic upgrade head

# Autogenerate a migration from the model changes
revision msg:
    docker compose exec app alembic revision --autogenerate -m "{{msg}}"

downgrade rev="-1":
    docker compose exec app alembic downgrade {{rev}}

# --- Local (uv) ---

sync:
    uv sync

# Run the API on the host against the compose Postgres (`docker compose up -d db` first)
dev:
    uv run uvicorn agenas_service.main:app --reload --port 8000

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

# Unit tests (no database, no network)
test *args:
    uv run pytest {{args}}

# Integration tests against the compose Postgres (database app_test)
test-int *args:
    docker compose run --rm -e TEMPLATE_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/app_test" app pytest -m integration {{args}}

# Unit tests with coverage (fails under 80%), writes coverage.xml and junit.xml for Sonar
test-cov:
    uv run pytest --cov --cov-report=term-missing --cov-report=xml:coverage.xml --junitxml=junit.xml

# Known vulnerabilities in the dependencies
audit:
    uv run pip-audit --skip-editable
