#!/usr/bin/env bash
# start_local.sh — run the full stack locally (no Docker needed)
# Usage: bash start_local.sh
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/ml_env"
PYTHON="$VENV/bin/python3"

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f "$ROOT/.env" ]; then
    set -a; source "$ROOT/.env"; set +a
fi

# ── Build GPU LD_LIBRARY_PATH ────────────────────────────────────────────────
PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
SITE_PKGS="$VENV/lib/python${PY_VER}/site-packages"
NVIDIA_LIBS=""
for d in "$SITE_PKGS"/nvidia/*/lib; do
    [ -d "$d" ] && NVIDIA_LIBS="$d:$NVIDIA_LIBS"
done
export LD_LIBRARY_PATH="${NVIDIA_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

export JWT_SECRET="${JWT_SECRET:-dev-secret-change-me}"
export DEMO_USER="${DEMO_USER:-admin}"
export DEMO_PASS="${DEMO_PASS:-admin}"
export DB_PATH="$ROOT/backend/sessions.db"
export MODEL_PATH="$ROOT/backend/model.h5"
export COLLECT_CSV="$ROOT/data/collected_dataset.csv"
mkdir -p "$ROOT/data"

# ── Start backend ────────────────────────────────────────────────────────────
echo "Starting backend on http://localhost:8001 ..."
cd "$ROOT/backend"
"$PYTHON" -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# ── Start frontend ───────────────────────────────────────────────────────────
echo "Starting frontend on http://localhost:5173 ..."
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  Smart Gym Rep Counter is running!           ║"
echo "║                                              ║"
echo "║  Frontend  →  http://localhost:5173          ║"
echo "║  Backend   →  http://localhost:8001          ║"
echo "║  API docs  →  http://localhost:8001/docs     ║"
echo "║                                              ║"
echo "║  Login: admin / admin                        ║"
echo "║  Press Ctrl+C to stop                        ║"
echo "╚══════════════════════════════════════════════╝"

# ── Cleanup on exit ──────────────────────────────────────────────────────────
trap "echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM
wait
