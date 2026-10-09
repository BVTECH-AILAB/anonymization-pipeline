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
RUN uv sync --frozen --no-install-project
COPY src ./src
RUN uv sync --frozen && chown -R appuser:appuser /app
ENV PATH="/app/.venv/bin:$PATH"
USER appuser
EXPOSE 8000
CMD ["uvicorn", "anonymization_pipeline.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS builder
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
RUN pip install --no-cache-dir --upgrade "pip>=25.3" \
    && apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home appuser
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser migrations ./migrations
COPY --chown=appuser:appuser scripts/entrypoint.sh ./entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
