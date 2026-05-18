#!/usr/bin/env bash
# Start OpenWebUI uvicorn with the AutoDataAgent integration env vars.
#
# Usage:
#   ./start-owui.sh             # foreground, logs to stderr
#   ./start-owui.sh --detach    # background, logs to /tmp/owui-backend.log
#   ./start-owui.sh --restart   # stop any running uvicorn first, then start detached
#
# These env vars are required for the `auto-data-analyst` virtual model
# to appear in the model picker (see backend/open_webui/utils/models.py
# around line 389 — the model is only injected when AUTO_DATA_AGENT_API_KEY
# or AUTO_DATA_AGENT_USERNAME is set).
set -euo pipefail

cd "$(dirname "$0")"

# ── AutoDataAgent integration ────────────────────────────────────────────
export AUTO_DATA_AGENT_BASE_URL="${AUTO_DATA_AGENT_BASE_URL:-http://localhost:8003}"
export AUTO_DATA_AGENT_USERNAME="${AUTO_DATA_AGENT_USERNAME:-admin}"
export AUTO_DATA_AGENT_PASSWORD="${AUTO_DATA_AGENT_PASSWORD:-goodluck}"
export AUTO_DATA_AGENT_API_KEY="${AUTO_DATA_AGENT_API_KEY:-da_d6be78e8aef54e028874371e755db12e}"

# Optional upload guardrails (Path A + Path C). Defaults match the code.
export AUTO_DATA_AGENT_MAX_FILES="${AUTO_DATA_AGENT_MAX_FILES:-20}"
export AUTO_DATA_AGENT_MAX_FILE_SIZE_MB="${AUTO_DATA_AGENT_MAX_FILE_SIZE_MB:-100}"
export AUTO_DATA_AGENT_MAX_TOTAL_UPLOAD_MB="${AUTO_DATA_AGENT_MAX_TOTAL_UPLOAD_MB:-300}"

# Tool-server registration so the `minimax-m25-data-analyst` model's
# toolIds:["server:auto-data-agent"] resolves to the actual OpenAPI tool
# endpoint. Without this, OWUI shows no tool options under that model.
# OWUI reads this on startup and persists it; subsequent edits via the
# admin UI take precedence (the persistent config wins over the env var
# on later starts).
if [[ -z "${TOOL_SERVER_CONNECTIONS:-}" ]]; then
    _ada_tool_conn=$(cat <<JSON
[{"url":"${AUTO_DATA_AGENT_BASE_URL}/api/v1/agent-tools","path":"/openapi.json","auth_type":"bearer","key":"${AUTO_DATA_AGENT_API_KEY}","config":{"enable":true},"info":{"id":"auto-data-agent","name":"Auto Data Analyst","description":"Run a data analysis on registered DB sources."}}]
JSON
)
    export TOOL_SERVER_CONNECTIONS="$_ada_tool_conn"
    unset _ada_tool_conn
fi

UVICORN="./.venv/bin/uvicorn"
APP="open_webui.main:app"
ARGS=(--port 8080 --host 0.0.0.0 --forwarded-allow-ips '*' --reload)
LOG="/tmp/owui-backend.log"

case "${1:-}" in
  --restart)
    echo "Stopping any existing uvicorn..."
    pkill -f "uvicorn $APP" 2>/dev/null || true
    sleep 2
    # Fall through to detached start
    set -- --detach
    ;;
esac

case "${1:-}" in
  --detach)
    [[ -f "$LOG" ]] && mv "$LOG" "${LOG}.$(date +%Y%m%d_%H%M%S)" 2>/dev/null || true
    echo "Starting uvicorn detached → $LOG"
    nohup "$UVICORN" "$APP" "${ARGS[@]}" > "$LOG" 2>&1 &
    disown
    PID=$!
    echo "PID=$PID"
    sleep 3
    if kill -0 "$PID" 2>/dev/null; then
        echo "✓ alive — env confirmed:"
        ps eww "$PID" | tr ' ' '\n' | grep '^AUTO_DATA_AGENT_' | sed 's/^/  /'
    else
        echo "✗ uvicorn died — last 20 log lines:"
        tail -20 "$LOG"
        exit 1
    fi
    ;;
  ""|--foreground)
    exec "$UVICORN" "$APP" "${ARGS[@]}"
    ;;
  *)
    echo "Usage: $0 [--detach | --restart | --foreground]"
    exit 2
    ;;
esac
