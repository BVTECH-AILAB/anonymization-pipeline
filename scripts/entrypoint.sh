#!/bin/sh
# Container entrypoint: apply migrations, then start the API.
# One uvicorn worker per pod: scale with Kubernetes replicas, not workers.
set -eu

# Without a database URL the API still starts (readyz reports 503),
# so a missing secret never puts the pod in a crash loop.
if [ "${ANONPIP_RUN_MIGRATIONS:-true}" = "true" ]; then
    if [ -n "${ANONPIP_DATABASE_URL:-}" ]; then
        alembic upgrade head
    else
        echo "ANONPIP_DATABASE_URL not set: skipping migrations" >&2
    fi
fi

exec uvicorn anonmyzation_pipeline.main:app \
    --host 0.0.0.0 \
    --port "${ANONPIP_PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "*" \
    "$@"
