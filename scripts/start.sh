#!/usr/bin/env bash
# Local demo: installs dependencies, prepares the synthetic dataset, starts the
# OCR server (ocr-server/, port 8829) unless one already answers, and the API with
# the web UI on the first free port, then opens the browser.
#   just start                  -> http://127.0.0.1:8000 (or the next free port)
#   PORT=9000 just start
#   NO_BROWSER=1 just start
#   OCR_EXTRA=cpu just start    (default: gpu when nvidia-smi is found)
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

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

# first free port starting from PORT (8000, 8001, ...)
port_busy() { (exec 3<>"/dev/tcp/${HOST}/$1") 2>/dev/null; }
START_PORT="$PORT"
while port_busy "$PORT"; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((START_PORT + 50)) ]; then echo "No free port between ${START_PORT} and ${PORT}" >&2; exit 1; fi
done
[ "$PORT" = "$START_PORT" ] || echo "==> Port ${START_PORT} is busy, using ${PORT}"

OCR_URL="${ANONPIP_OCR_SERVER_URL:-http://127.0.0.1:8829}"
if ! curl -sf -o /dev/null "${OCR_URL}/health"; then
  if [ -z "${OCR_EXTRA:-}" ]; then
    if command -v nvidia-smi >/dev/null 2>&1; then OCR_EXTRA=gpu; else OCR_EXTRA=cpu; fi
  fi
  echo "==> Starting the OCR server (${OCR_EXTRA}) at ${OCR_URL}; first run downloads the models"
  (cd ocr-server && VIRTUAL_ENV= uv run --quiet --extra "$OCR_EXTRA" python -m paddle_ocr_server) &
  OCR_PID=$!
  trap 'kill "$OCR_PID" 2>/dev/null || true' EXIT
fi

URL="http://${HOST}:${PORT}"
# open the browser once the models are loaded (WSL: wslview / explorer.exe; Linux: xdg-open)
[ -n "${NO_BROWSER:-}" ] || (
  for _ in $(seq 1 600); do
    if curl -sf -o /dev/null "${URL}/readyz"; then
      if command -v wslview >/dev/null 2>&1; then wslview "$URL"
      elif command -v explorer.exe >/dev/null 2>&1; then explorer.exe "$URL" || true
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
      fi
      exit 0
    fi
    sleep 1
  done
) &

echo "==> Starting the API and the UI at ${URL} (Ctrl+C to stop)"
export ANONPIP_ENVIRONMENT="${ANONPIP_ENVIRONMENT:-local}"
export ANONPIP_UI_ENABLED="${ANONPIP_UI_ENABLED:-true}"
export ANONPIP_OCR_SERVER_URL="$OCR_URL"
# not exec: the trap stops the OCR server when the API stops
uv run uvicorn anonmyzation_pipeline.main:app --host "$HOST" --port "$PORT"
