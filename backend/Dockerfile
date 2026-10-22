# Tooling image: tests and evaluation, never deployed. Build context: backend/
#   docker build -t anonymization-pipeline-tools .
#   docker run --rm anonymization-pipeline-tools uv run pytest
# The evaluation needs the dataset mounted (-v ../dataset:/data) and the OCR server and NER.
FROM ghcr.io/astral-sh/uv:0.12.18 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --group evals
COPY src ./src
COPY tests ./tests
COPY evals ./evals
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uv", "run", "pytest", "-q"]
