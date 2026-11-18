#!/usr/bin/env bash
# One-shot setup + run for macOS (Apple Silicon). See MAC_SETUP.md.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-/opt/homebrew/bin/python3.13}"
command -v "$PY" >/dev/null || PY="$(command -v python3.13)"

if [ ! -x .venv/bin/python ]; then
  echo "==> Creating virtualenv with $PY"
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

if [ ! -f globe_check.db ]; then
  echo "==> Creating database"
  .venv/bin/reflex db migrate
  echo "==> Importing bundled CSV data"
  .venv/bin/python -m scripts.restore_bundled_data
fi

echo "==> Starting app at http://localhost:${FRONTEND_PORT:-3000}/"
exec .venv/bin/reflex run --frontend-port "${FRONTEND_PORT:-3000}" --backend-port "${BACKEND_PORT:-8000}"
