#!/usr/bin/env bash
set -euo pipefail

# run_mesh.sh — Launch all 5 Band agents + dashboard.
# Usage:  bash scripts/run_mesh.sh
#
# Stops all processes on Ctrl+C.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Check .env
if [ ! -f .env ]; then
  echo "❌ No .env file found. Copy .env.example to .env and fill in credentials."
  exit 1
fi

# Source .env
set -a; source .env; set +a

# Validate required vars
MISSING=()
for var in BAND_SCRAPER_ID BAND_SCRAPER_KEY \
           BAND_KEEPER_ID BAND_KEEPER_KEY \
           BAND_RECONCILER_ID BAND_RECONCILER_KEY \
           BAND_REGISTRY_ID BAND_REGISTRY_KEY \
           BAND_BRIDGE_ID BAND_BRIDGE_KEY \
           BAND_ROOM_ID BAND_USER_HANDLE; do
  if [ -z "${!var:-}" ]; then
    MISSING+=("$var")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "❌ Missing env vars: ${MISSING[*]}"
  exit 1
fi

echo "🚀 Starting Knowledge Mesh agents..."
echo "  Room: $BAND_ROOM_ID"
echo "  Agents: Scraper, Keeper, Reconciler, Registry, Bridge"
echo ""

is_port_free() {
  if command -v lsof >/dev/null 2>&1; then
    [ -z "$(lsof -ti TCP:"$1" -sTCP:LISTEN 2>/dev/null)" ]
    return
  fi

  python3 -c '
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(("0.0.0.0", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
' "$1"
}

DASHBOARD_BIND_PORT="${DASHBOARD_PORT:-8766}"
BRIDGE_BIND_PORT="${BRIDGE_PORT:-8765}"

if [ "$DASHBOARD_BIND_PORT" = "$BRIDGE_BIND_PORT" ]; then
  echo "❌ DASHBOARD_PORT and BRIDGE_PORT are both set to $DASHBOARD_BIND_PORT."
  echo "   Use two different ports in .env, for example DASHBOARD_PORT=8766 and BRIDGE_PORT=8765."
  exit 1
fi

PORT_ERRORS=()
if ! is_port_free "$DASHBOARD_BIND_PORT"; then
  PORT_ERRORS+=("dashboard:$DASHBOARD_BIND_PORT")
fi
if ! is_port_free "$BRIDGE_BIND_PORT"; then
  PORT_ERRORS+=("bridge:$BRIDGE_BIND_PORT")
fi
if [ ${#PORT_ERRORS[@]} -gt 0 ]; then
  echo "❌ Ports already in use: ${PORT_ERRORS[*]}"
  echo "   Stop the old process, or change DASHBOARD_PORT / BRIDGE_PORT in .env."
  echo "   Useful check: lsof -i :$DASHBOARD_BIND_PORT -i :$BRIDGE_BIND_PORT"
  exit 1
fi

# Cleanup handler
cleanup() {
  echo ""
  echo "🛑 Stopping all agents..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "✅ All agents stopped."
  exit 0
}
trap cleanup SIGINT SIGTERM

PIDS=()

# Start dashboard
echo "  [dashboard] Starting on http://0.0.0.0:${DASHBOARD_BIND_PORT}..."
uv run python dashboard/server.py &
PIDS+=($!)

sleep 0.5

# Start agents
declare -A AGENTS=(
  ["bridge"]="agents.bridge_agent"
  ["scraper"]="agents.scraper_band"
  ["keeper"]="agents.keeper_band"
  ["reconciler"]="agents.reconciler_band"
  ["registry"]="agents.registry_band"
)

for name in "${!AGENTS[@]}"; do
  module="${AGENTS[$name]}"
  echo "  [$name] Starting $module..."
  uv run python -m "$module" &
  PIDS+=($!)
  sleep 1  # stagger startup to avoid rate limits
done

echo ""
echo "✅ All agents launched. Dashboard: http://0.0.0.0:${DASHBOARD_BIND_PORT}"
echo "   Press Ctrl+C to stop all."
echo ""

# Wait for any child to exit
wait
