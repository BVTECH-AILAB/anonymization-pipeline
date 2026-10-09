# Stages:
#   dev      - used by docker-compose (dev dependencies, editable install, --reload)
#   runtime  - the image CI builds and pushes to Harbor (last stage = default target)
#
# EXTRAS selects the optional components: NER detector (torch CPU + transformers) and docling for
# the native PDF pages. Without them the service still works, with warnings, using the VLM only.
# The models are never in the image: they are read, offline, from volumes filled by
# `python -m anonymization_pipeline.model_store` (`just models`).

ARG EXTRAS="--extra ner --extra docling"

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.12-slim AS base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# /models: mount points of the model volumes, owned by the user so a new volume inherits it
RUN pip install --no-cache-dir --upgrade "pip>=25.3" \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /models/hf /models/docling && chown -R appuser:appuser /models
WORKDIR /app

FROM base AS dev
ARG EXTRAS
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project ${EXTRAS}
COPY src ./src
RUN uv sync --frozen ${EXTRAS} && chown -R appuser:appuser /app
ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1
USER appuser
EXPOSE 8000
CMD ["uvicorn", "anonymization_pipeline.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS builder
ARG EXTRAS
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project ${EXTRAS}
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable ${EXTRAS}

FROM python:3.12-slim AS runtime
RUN pip install --no-cache-dir --upgrade "pip>=25.3" \
    && apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /models/hf /models/docling && chown -R appuser:appuser /models
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser scripts/entrypoint.sh ./entrypoint.sh
# HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE: the NER model is read from the cache, never downloaded
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_OFFLINE=1
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
