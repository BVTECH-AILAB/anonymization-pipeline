# The service image. Build context: the repository root (the engine is a
# workspace member): docker build -f apps/api/Dockerfile .
# Only this package and its dependencies are installed (--package): never apps/eval.
# uv needs the pyproject.toml of every workspace member to read the lock.
# Stages:
#   dev      - used by docker-compose (dev dependencies, editable install, --reload)
#   runtime  - the image CI builds and pushes to Harbor (last stage = default target)

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.12-slim AS base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir --upgrade "pip>=25.3" \
    && useradd --uid 1000 --create-home appuser
WORKDIR /app

FROM base AS dev
COPY pyproject.toml uv.lock ./
COPY packages/anonymization-core/pyproject.toml ./packages/anonymization-core/
COPY apps/api/pyproject.toml ./apps/api/
COPY apps/eval/pyproject.toml ./apps/eval/
RUN uv sync --frozen --package anonmyzation-pipeline --no-install-workspace
COPY packages ./packages
COPY apps/api/src ./apps/api/src
RUN uv sync --frozen --package anonmyzation-pipeline && chown -R appuser:appuser /app
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "anonmyzation_pipeline.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# The workspace members are installed as wheels: the runtime image has no sources.
FROM base AS builder
COPY pyproject.toml uv.lock ./
COPY packages/anonymization-core/pyproject.toml ./packages/anonymization-core/
COPY apps/api/pyproject.toml ./apps/api/
COPY apps/eval/pyproject.toml ./apps/eval/
RUN uv sync --frozen --package anonmyzation-pipeline --no-dev --no-install-workspace
COPY packages ./packages
COPY apps/api/src ./apps/api/src
RUN uv sync --frozen --package anonmyzation-pipeline --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN pip install --no-cache-dir --upgrade "pip>=25.3" \
    && apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser apps/api/scripts/entrypoint.sh ./entrypoint.sh
# No model weights here: OCR runs in the OCR server (ocr-server/), NER in the AILAB NER service.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
