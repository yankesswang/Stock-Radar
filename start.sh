#!/usr/bin/env bash
# start.sh — Launch backend (FastAPI), frontend (Vite), and optional Cloudflare tunnel.
# Usage: ./start.sh
# Stop all with Ctrl+C.

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Must match what Arthur.OS start-all.sh uses, otherwise the portal's health
# check (:6000) and the Vite /api proxy both point at a backend that isn't there.
BACKEND_PORT="${STOCKS_BACKEND_PORT:-6000}"
FRONTEND_PORT="${STOCKS_FRONTEND_PORT:-4000}"
# The Vite proxy reads this to find the backend.
export STOCKS_BACKEND_PORT="$BACKEND_PORT"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[start]${RESET} $*"; }
ok()   { echo -e "${GREEN}[start]${RESET} $*"; }
err()  { echo -e "${RED}[start]${RESET} $*"; }
warn() { echo -e "${YELLOW}[start]${RESET} $*"; }

BACKEND_PID=""
FRONTEND_PID=""
TUNNEL_PID=""
TUNNEL_LOG=""

# ── Free required ports ───────────────────────────────────────────────────────
free_port() {
    local port=$1
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null)
    if [[ -n "$pids" ]]; then
        warn "Port $port in use — killing existing process(es): $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 0.5
    fi
}
free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
    log "Shutting down…"
    [[ -n "$BACKEND_PID" ]]  && kill "$BACKEND_PID"  2>/dev/null
    [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null
    [[ -n "$TUNNEL_PID" ]]   && kill "$TUNNEL_PID"   2>/dev/null
    [[ -n "$TUNNEL_LOG" ]]   && rm -f "$TUNNEL_LOG"
    wait 2>/dev/null
    ok "Done."
}
trap cleanup INT TERM

# ── Backend ───────────────────────────────────────────────────────────────────
log "Starting backend on port $BACKEND_PORT…"
cd "$PROJECT_ROOT"

if command -v uv &>/dev/null; then
    uv run python -m uvicorn backend.app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload &
elif command -v uvicorn &>/dev/null; then
    uvicorn backend.app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload &
else
    err "Neither uv nor uvicorn found. Install uv: https://docs.astral.sh/uv/"
    exit 1
fi
BACKEND_PID=$!

# ── Cloudflare Tunnel ─────────────────────────────────────────────────────────
TUNNEL_URL=""
if command -v cloudflared &>/dev/null; then
    log "Starting Cloudflare tunnel → http://localhost:${FRONTEND_PORT}…"
    TUNNEL_LOG=$(mktemp)
    cloudflared tunnel --url "http://localhost:${FRONTEND_PORT}" >"$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    # Poll up to 30 s for the tunnel URL in cloudflared's output
    log "Waiting for tunnel URL (up to 30 s)…"
    for i in $(seq 1 60); do
        TUNNEL_URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | head -1)
        [[ -n "$TUNNEL_URL" ]] && break
        sleep 0.5
    done

    if [[ -n "$TUNNEL_URL" ]]; then
        # No sed rewrite of vite.config.js here. It used to pin this run's hostname
        # into a tracked source file, which then went stale on the next restart and
        # made Vite 403 every tunnel request. vite.config.js now allows the whole
        # .trycloudflare.com domain, so nothing needs patching.
        ok "Tunnel ready → $TUNNEL_URL"
    else
        warn "Tunnel URL not detected within 30 s."
        warn "Check cloudflared logs or run: tail $TUNNEL_LOG"
    fi
else
    warn "cloudflared not found — skipping tunnel."
    warn "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
fi

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
ok "All services started."
echo ""
echo "  Backend  → http://localhost:${BACKEND_PORT}"
echo "  Frontend → http://localhost:${FRONTEND_PORT}"
if [[ -n "$TUNNEL_URL" ]]; then
    echo ""
    echo -e "  ${GREEN}Public   → ${TUNNEL_URL}${RESET}"
fi
echo ""
echo "  Press Ctrl+C to stop all."
echo ""

wait
