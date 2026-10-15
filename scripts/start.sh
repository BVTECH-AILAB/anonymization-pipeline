#!/usr/bin/env bash
# Local demo: installs dependencies, prepares the synthetic dataset and starts
# the API with the web UI on the first free port, then opens the browser.
#   just start                  -> http://127.0.0.1:8000 (or the next free port)
#   PORT=9000 just start
#   NO_BROWSER=1 just start
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
  uv run python -m tools generate --n 21 --out dataset
fi

# first free port starting from PORT (8000, 8001, ...)
port_busy() { (exec 3<>"/dev/tcp/${HOST}/$1") 2>/dev/null; }
START_PORT="$PORT"
while port_busy "$PORT"; do
  PORT=$((PORT + 1))
  if [ "$PORT" -gt $((START_PORT + 50)) ]; then echo "No free port between ${START_PORT} and ${PORT}" >&2; exit 1; fi
done
[ "$PORT" = "$START_PORT" ] || echo "==> Port ${START_PORT} is busy, using ${PORT}"

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
exec uv run uvicorn anonmyzation_pipeline.main:app --host "$HOST" --port "$PORT"
