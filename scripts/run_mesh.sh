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

# Warn about missing handles (optional — routing uses fallback names)
WARN=()
for var in BAND_KEEPER_HANDLE BAND_RECONCILER_HANDLE \
           BAND_REGISTRY_HANDLE BAND_SCRAPER_HANDLE; do
  if [ -z "${!var:-}" ]; then
    WARN+=("$var")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "❌ Missing env vars: ${MISSING[*]}"
  exit 1
fi

if [ ${#WARN[@]} -gt 0 ]; then
  echo "⚠️  Optional env vars unset (agent routing uses fallback names): ${WARN[*]}"
fi

echo "🚀 Starting Knowledge Mesh agents..."
echo "  Room: $BAND_ROOM_ID"
echo "  Agents: Scraper, Keeper, Reconciler, Registry, Bridge"
echo ""

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
echo "  [dashboard] Starting on http://0.0.0.0:${DASHBOARD_PORT:-8766}..."
uv run python dashboard/server.py &
PIDS+=($!)

sleep 0.5

# Start agents
declare -A AGENTS=(
  ["bridge"]="agents/bridge_agent.py"
  ["scraper"]="agents/scraper_band.py"
  ["keeper"]="agents/keeper_band.py"
  ["reconciler"]="agents/reconciler_band.py"
  ["registry"]="agents/registry_band.py"
)

for name in "${!AGENTS[@]}"; do
  script="${AGENTS[$name]}"
  echo "  [$name] Starting $script..."
  uv run python "$script" &
  PIDS+=($!)
  sleep 1  # stagger startup to avoid rate limits
done

echo ""
echo "✅ All agents launched. Dashboard: http://0.0.0.0:${DASHBOARD_PORT:-8766}"
echo "   Press Ctrl+C to stop all."
echo ""

# Wait for any child to exit
wait
