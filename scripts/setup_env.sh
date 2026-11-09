#!/bin/bash
# Build the project environment from scratch (macOS or Linux, Python 3.11+).
#   scripts/setup_env.sh
# Needs uv (https://docs.astral.sh/uv/). Environment variables: VENV (default .venv), PYTHON (default 3.13)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv}"; PYTHON="${PYTHON:-3.13}"
say() { printf '\n==> %s\n' "$*"; }
command -v uv >/dev/null || { echo "uv not found: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
say "dependencies from uv.lock into $VENV (the runtime and the database layer, no test tooling; add --group audio for the signal-analysis tools)"
UV_PROJECT_ENVIRONMENT="$VENV" uv sync --locked --no-dev --group db --python "$PYTHON" --project "$ROOT"
say "checking"
APP_PYTHON="$VENV/bin/python" "$ROOT/app" doctor

# ---- web app (React + Tailwind, built into src/app/server/static) -------------------------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  ( cd "$ROOT/web" && npm install --silent && npm run build ) && echo "web app built"
else
  echo "npm not found: install Node.js to build the web app (cd web && npm install && npm run build)"
fi
