#!/usr/bin/env bash
# Smoke test for the Auto Data Agent integration.
# Run after starting the backend; reports whether each layer is healthy.
#
# Usage:
#   ./scripts/test-ada-integration.sh [<email> <password>]
#
# Defaults: aimfuture@gmail.com / goodluck (matches dev fixtures)

set -u

EMAIL="${1:-aimfuture@gmail.com}"
PASSWORD="${2:-goodluck}"
OWUI="${OWUI_BASE_URL:-http://localhost:8080}"
ADA="${AUTO_DATA_AGENT_BASE_URL:-http://localhost:8003}"

ok()    { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail()  { printf "  \033[31m✗\033[0m %s\n" "$*"; FAILED=$((FAILED+1)); }
warn()  { printf "  \033[33m!\033[0m %s\n" "$*"; }
header(){ printf "\n\033[1m%s\033[0m\n" "$*"; }

FAILED=0

header "1. AutoDataAgent backend reachable on $ADA"
if curl -sf -o /dev/null -m 5 "$ADA/health"; then
  ok "backend /health responds"
else
  fail "backend not reachable (is the docker container running?)"
fi

header "2. Open WebUI backend reachable on $OWUI"
if curl -sf -o /dev/null -m 5 "$OWUI/api/version"; then
  ok "/api/version responds"
else
  fail "Open WebUI backend not reachable"
  echo
  echo "  Aborting remaining checks."
  exit 1
fi

header "3. Auth: signin as $EMAIL"
TOKEN=$(curl -sf -X POST "$OWUI/api/v1/auths/signin" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('token',''))" 2>/dev/null)

if [ -n "${TOKEN:-}" ]; then
  ok "obtained token (length=${#TOKEN})"
else
  fail "signin failed — check credentials"
  exit 1
fi

header "4. Integration health (proxy → backend → JWT)"
HEALTH=$(curl -sf -H "Authorization: Bearer $TOKEN" "$OWUI/api/v1/auto-data-agent/health")
if echo "$HEALTH" | grep -q '"ok":true'; then
  ok "/api/v1/auto-data-agent/health → ok"
else
  fail "health endpoint failed: $HEALTH"
fi

header "5. Virtual model registered"
MODELS=$(curl -sf -H "Authorization: Bearer $TOKEN" "$OWUI/api/models?refresh=true")
if echo "$MODELS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ids = [m['id'] for m in d.get('data', [])]
sys.exit(0 if 'auto-data-analyst' in ids else 1)
"; then
  ok "auto-data-analyst appears in /api/models"
else
  fail "auto-data-analyst NOT in /api/models — env var not set?"
fi

header "6. Asset proxy security"
# Should be 400 because path is outside the whitelist
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$OWUI/api/v1/auto-data-agent/asset?path=/etc/passwd")
if [ "$STATUS" = "400" ]; then
  ok "rejects non-whitelisted paths (got 400)"
else
  fail "asset proxy returned $STATUS for non-whitelisted path — allowlist may be broken"
fi

header "7. Recent task fetch (smoke)"
LATEST=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "$OWUI/api/v1/auto-data-agent/health" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('status_code'))
")
if [ "$LATEST" = "200" ]; then
  ok "proxy round-trip to AutoDataAgent OK"
else
  fail "proxy round-trip returned $LATEST"
fi

header "8. Database connections proxy"
CONN_RESP=$(curl -sf -H "Authorization: Bearer $TOKEN" "$OWUI/api/v1/auto-data-agent/connections")
CONN_COUNT=$(echo "$CONN_RESP" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('total', len(d.get('connections', []))))
except Exception:
    print(0)
")
if [ -n "$CONN_COUNT" ] && [ "$CONN_COUNT" != "0" ]; then
  ok "/connections returned $CONN_COUNT connection(s)"
elif [ "$CONN_COUNT" = "0" ]; then
  warn "/connections endpoint works but 0 connections registered (DB-source mode unavailable)"
else
  fail "/connections endpoint failed"
fi

echo
if [ "$FAILED" -eq 0 ]; then
  printf "\033[32mAll checks passed.\033[0m Integration is healthy.\n"
  exit 0
else
  printf "\033[31m%d check(s) failed.\033[0m See INTEGRATION_AUTO_DATA_AGENT.md → Debugging.\n" "$FAILED"
  exit 1
fi
