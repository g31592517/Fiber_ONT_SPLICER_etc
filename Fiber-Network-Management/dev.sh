#!/usr/bin/env bash
# One-command local demo: SQLite database, simulated network, API + worker + UI dev server.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r backend/requirements-dev.txt
(cd frontend && [ -d node_modules ] || npm install --no-audit --no-fund)
export FNMS_DATABASE_URL="${FNMS_DATABASE_URL:-sqlite:///$ROOT/backend/fnms.db}"
(cd backend && ../.venv/bin/python -m app.seed --demo)
trap 'kill 0' EXIT
(cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000) &
(cd backend && ../.venv/bin/python -m app.jobs.worker) &
(cd frontend && npx vite --port 5173) &
echo "UI: http://localhost:5173   API docs: http://localhost:8000/api/docs   login admin / ChangeMe!2026"
wait
