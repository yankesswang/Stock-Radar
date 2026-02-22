#!/usr/bin/env bash
# start.sh — Launch backend (FastAPI) and frontend (Vite) together.
# Usage: ./start.sh
# Stop both with Ctrl+C.

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

BACKEND_PORT=6000
FRONTEND_PORT=5000

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[start]${RESET} $*"; }
ok()   { echo -e "${GREEN}[start]${RESET} $*"; }
err()  { echo -e "${RED}[start]${RESET} $*"; }

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
    log "Shutting down…"
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
    ok "Done."
}
trap cleanup INT TERM

# ── Backend ───────────────────────────────────────────────────────────────────
log "Starting backend on port $BACKEND_PORT…"
cd "$PROJECT_ROOT"

# Use uv (preferred), then fall back to system uvicorn
if command -v uv &>/dev/null; then
    uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload &
elif command -v uvicorn &>/dev/null; then
    uvicorn backend.app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload &
else
    err "Neither uv nor uvicorn found. Install uv: https://docs.astral.sh/uv/"
    exit 1
fi
BACKEND_PID=$!

# ── Frontend ──────────────────────────────────────────────────────────────────
log "Starting frontend on port $FRONTEND_PORT…"
cd "$PROJECT_ROOT/frontend"

if ! command -v npm &>/dev/null; then
    err "npm not found. Install Node.js first."
    kill "$BACKEND_PID" 2>/dev/null
    exit 1
fi

npm run dev -- --port "$FRONTEND_PORT" &
FRONTEND_PID=$!

# ── Ready ─────────────────────────────────────────────────────────────────────
cd "$PROJECT_ROOT"
ok "Both services started."
echo ""
echo "  Backend  → http://localhost:${BACKEND_PORT}"
echo "  Frontend → http://localhost:${FRONTEND_PORT}"
echo ""
echo "  Press Ctrl+C to stop both."
echo ""

wait "$BACKEND_PID" "$FRONTEND_PID"
