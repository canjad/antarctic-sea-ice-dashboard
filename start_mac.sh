#!/usr/bin/env bash
# One-shot setup + run for macOS (Apple Silicon) with PostgreSQL.
# See MAC_SETUP.md. Set REFLEX_DB_URL to use a database other than the
# default local PostgreSQL database "antarctic_sea_ice".
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-/opt/homebrew/bin/python3.13}"
command -v "$PY" >/dev/null || PY="$(command -v python3.13)"
PG_FORMULA="${PG_FORMULA:-postgresql@18}"
PG_BIN="$(brew --prefix "$PG_FORMULA" 2>/dev/null)/bin"
DB_NAME="antarctic_sea_ice"

if [ -z "${REFLEX_DB_URL:-}" ]; then
  if ! "$PG_BIN/pg_isready" -q -h localhost; then
    echo "==> Starting $PG_FORMULA"
    brew services start "$PG_FORMULA"
    for _ in $(seq 1 20); do "$PG_BIN/pg_isready" -q -h localhost && break; sleep 1; done
  fi
  if ! "$PG_BIN/psql" -h localhost -d postgres -Atc \
      "select 1 from pg_database where datname='$DB_NAME'" | grep -q 1; then
    echo "==> Creating database $DB_NAME"
    "$PG_BIN/createdb" -h localhost "$DB_NAME"
  fi
fi

if [ ! -x .venv/bin/python ]; then
  echo "==> Creating virtualenv with $PY"
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

echo "==> Applying database migrations"
.venv/bin/reflex db migrate
echo "==> Importing bundled CSV data (existing datasets are skipped)"
.venv/bin/python -m scripts.restore_bundled_data

echo "==> Starting app at http://localhost:${FRONTEND_PORT:-3000}/"
exec .venv/bin/reflex run --frontend-port "${FRONTEND_PORT:-3000}" --backend-port "${BACKEND_PORT:-8000}"
