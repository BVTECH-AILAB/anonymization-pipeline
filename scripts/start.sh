#!/usr/bin/env bash
# Local run: installs dependencies, starts the OCR server (services/ocr-server/,
# port 8829) unless one already answers and the API on the first free port,
# then opens the OpenAPI page (/docs) once the models are ready.
#   just start                  -> http://127.0.0.1:8000/docs
#   PORT=9000 just start
#   NO_BROWSER=1 just start
#   OCR_EXTRA=cpu just start    (default: gpu when nvidia-smi is found)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found: install it with  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "==> Installing dependencies (uv sync)"
uv sync --quiet

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
URL="${API_URL}/docs"
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
# not exec: the trap stops the OCR server when the API stops
uv run uvicorn anonymization_pipeline.main:app --host "$HOST" --port "$PORT"
