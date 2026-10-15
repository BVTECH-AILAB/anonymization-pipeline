#!/bin/sh
# Container entrypoint: start the API (stateless service, no migrations).
# One uvicorn worker per pod: scale with Kubernetes replicas, not workers.
set -eu

exec uvicorn anonmyzation_pipeline.main:app \
    --host 0.0.0.0 \
    --port "${ANONPIP_PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "*" \
    "$@"
