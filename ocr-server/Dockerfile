# PaddleOCR behind the LiteParse OCR API (POST /ocr, GET /health), port 8829.
# PADDLE_EXTRA=gpu (CUDA 12.9 wheels, Blackwell included; the CUDA runtime comes with
# the wheels, the host needs only the NVIDIA driver) or cpu.
# Model weights are read from /models/paddleocr/<model name>: mount a volume there.
# The cluster has no internet access.

FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/
ARG PADDLE_EXTRA=gpu
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra ${PADDLE_EXTRA} --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra ${PADDLE_EXTRA} --no-editable

FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /models && chown appuser:appuser /models
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCRSRV_MODEL_DIR=/models/paddleocr \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
USER appuser
EXPOSE 8829
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8829/health', timeout=3)" || exit 1
CMD ["python", "-m", "paddle_ocr_server"]
