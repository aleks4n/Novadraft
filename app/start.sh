#!/usr/bin/env bash
#
# start.sh — launch the Research Ideation app (backend + web frontend) with one command.
#
#   ./app/start.sh            # from the novadraft root
#   ./start.sh                # from inside app/
#
# Starts:
#   - FastAPI backend  → http://localhost:8787   (uvicorn, --reload)
#   - Vite web frontend → http://localhost:5173  (proxies /api → :8787)
#
# Both run together; Ctrl-C stops both cleanly.

set -euo pipefail

# Resolve paths relative to this script, so it works from any cwd.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NOVADRAFT_ROOT="$(cd "$APP_DIR/.." && pwd)"
FRONTEND_DIR="$APP_DIR/frontend"

BACKEND_PORT=8787
FRONTEND_PORT=5173

# Prefer the local project environment. Fall back to the declared interpreter, then
# python3, so a checkout with `uv sync` is runnable even when another developer's
# absolute conda path in `.python-env` does not exist on this machine.
PYTHON="python3"
if [[ -x "$NOVADRAFT_ROOT/.venv/bin/python" ]]; then
  PYTHON="$NOVADRAFT_ROOT/.venv/bin/python"
elif [[ -f "$NOVADRAFT_ROOT/.python-env" ]]; then
  declared="$(head -n1 "$NOVADRAFT_ROOT/.python-env" | tr -d '[:space:]')"
  [[ -x "$declared" ]] && PYTHON="$declared"
fi

# Track child pids so we can tear everything down on exit.
pids=()
cleanup() {
  echo ""
  echo "› Shutting down…"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 1 — Backend (run from novadraft root so `dev.*` / `src.*` imports resolve).
echo "› Starting backend on http://localhost:$BACKEND_PORT …"
(
  cd "$NOVADRAFT_ROOT"
  exec "$PYTHON" -m uvicorn app.backend.main:app --reload --port "$BACKEND_PORT"
) &
pids+=($!)

# 2 — Frontend (install deps first run, then Vite dev server).
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "› Installing frontend dependencies (first run)…"
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "› Starting web frontend on http://localhost:$FRONTEND_PORT …"
(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --port "$FRONTEND_PORT"
) &
pids+=($!)

echo ""
echo "  Backend : http://localhost:$BACKEND_PORT/api/topics"
echo "  Web app : http://localhost:$FRONTEND_PORT"
echo "  (Ctrl-C to stop both)"
echo ""

# Wait on all children; if one dies, cleanup() tears down the rest.
wait -n 2>/dev/null || wait
