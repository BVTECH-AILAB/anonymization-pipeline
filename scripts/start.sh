#!/usr/bin/env bash
# Local demo: installs dependencies, prepares the synthetic dataset, starts the
# OCR server (services/ocr-server/, port 8829) unless one already answers, the API and the
# demo page (apps/web: nginx in Docker, host network, proxying to the API), each
# on the first free port, then opens the browser on the page.
#   just start                  -> page http://127.0.0.1:8080, API http://127.0.0.1:8000
#   PORT=9000 WEB_PORT=9080 just start
#   NO_BROWSER=1 just start
#   OCR_EXTRA=cpu just start    (default: gpu when nvidia-smi is found)
# Without Docker only the API starts. The host network needs a Linux Docker
# engine (WSL included); with Docker Desktop use `just up` instead.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
WEB_PORT="${WEB_PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
PIDS=()
WEB_CONTAINER=""
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  [ -z "$WEB_CONTAINER" ] || docker stop "$WEB_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found: install it with  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "==> Installing dependencies (uv sync)"
uv sync --quiet

if [ ! -d dataset/native ]; then
  echo "==> Generating the synthetic dataset (dataset/)"
  uv run python -m anonymization_eval generate --n 21 --out dataset
fi

# first free port starting from $1 (8000, 8001, ...)
port_busy() { (exec 3<>"/dev/tcp/${HOST}/$1") 2>/dev/null; }
free_port() {
  local start="$1" port="$1"
  while port_busy "$port"; do
    port=$((port + 1))
    if [ "$port" -gt $((start + 50)) ]; then echo "No free port between ${start} and ${port}" >&2; exit 1; fi
  done
  [ "$port" = "$start" ] || echo "==> Port ${start} is busy, using ${port}" >&2
  echo "$port"
}
PORT="$(free_port "$PORT")"

OCR_URL="${ANONPIP_OCR_SERVER_URL:-http://127.0.0.1:8829}"
if ! curl -sf -o /dev/null "${OCR_URL}/health"; then
  if [ -z "${OCR_EXTRA:-}" ]; then
    if command -v nvidia-smi >/dev/null 2>&1; then OCR_EXTRA=gpu; else OCR_EXTRA=cpu; fi
  fi
  echo "==> Starting the OCR server (${OCR_EXTRA}) at ${OCR_URL}; first run downloads the models"
  (cd services/ocr-server && VIRTUAL_ENV= uv run --quiet --extra "$OCR_EXTRA" python -m paddle_ocr_server) &
  PIDS+=("$!")
fi

API_URL="http://${HOST}:${PORT}"
URL="$API_URL"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  WEB_PORT="$(free_port "$WEB_PORT")"
  echo "==> Building and starting the demo page (apps/web) at http://${HOST}:${WEB_PORT}"
  docker build --quiet --tag anonpip-web:local apps/web >/dev/null
  WEB_CONTAINER="anonpip-web-${WEB_PORT}"
  docker run --detach --rm --name "$WEB_CONTAINER" --network host \
    -e WEB_PORT="$WEB_PORT" -e API_UPSTREAM="$API_URL" anonpip-web:local >/dev/null
  URL="http://${HOST}:${WEB_PORT}"
else
  echo "==> Docker not available: starting the API only (no demo page)" >&2
fi
# open the browser once the models are loaded (WSL: wslview / explorer.exe; Linux: xdg-open)
[ -n "${NO_BROWSER:-}" ] || (
  for _ in $(seq 1 600); do
    if curl -sf -o /dev/null "${API_URL}/readyz"; then
      if command -v wslview >/dev/null 2>&1; then wslview "$URL"
      elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$URL" || true
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
      fi
      exit 0
    fi
    sleep 1
  done
) &

echo "==> Starting the API at ${API_URL}; open ${URL} (Ctrl+C to stop)"
export ANONPIP_ENVIRONMENT="${ANONPIP_ENVIRONMENT:-local}"
export ANONPIP_OCR_SERVER_URL="$OCR_URL"
# not exec: the trap stops the OCR server and the page when the API stops
uv run uvicorn anonmyzation_pipeline.main:app --host "$HOST" --port "$PORT"
