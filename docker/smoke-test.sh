#!/bin/bash
# End-to-end smoke test for the Caldera OT + Wildcat Dam (Modbus) integration.
#
# Prerequisites: docker compose -f docker/docker-compose.yml up --build -d
#
# This script:
#   1. Waits for the dam and Caldera to be reachable
#   2. Waits for a Sandcat agent to check in
#   3. Creates an adversary with a Modbus read ability
#   4. Runs an operation against the dam and checks the read returned values
#   5. Reports pass/fail
set -euo pipefail

CALDERA_URL="${CALDERA_URL:-http://localhost:8888}"
PLC_HOST="${PLC_HOST:-localhost}"
PLC_PORT="${PLC_PORT:-5020}"
FACTS_ID="7c9a21ca-3546-47ab-8e9e-b6bd43e884b0"
API_KEY="ADMIN123"  # matches api_key_red in conf/default.yml
AGENT_PAW=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# ── Step 1: Wait for the dam ────────────────────────────────────────
info "Waiting for the dam at ${PLC_HOST}:${PLC_PORT} ..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.create_connection(('${PLC_HOST}',${PLC_PORT}),2); s.close()" 2>/dev/null; then
        pass "Dam is reachable"
        break
    fi
    if [ "$i" -eq 30 ]; then fail "Dam not reachable after 30 attempts"; fi
    sleep 2
done

# ── Step 2: Wait for Caldera ────────────────────────────────────────
info "Waiting for Caldera at ${CALDERA_URL} ..."
for i in $(seq 1 60); do
    if curl -sf "${CALDERA_URL}" >/dev/null 2>&1; then
        pass "Caldera server is reachable"
        break
    fi
    if [ "$i" -eq 60 ]; then fail "Caldera not reachable after 60 attempts"; fi
    sleep 3
done

# ── Step 3: Wait for an agent ───────────────────────────────────────
info "Waiting for a Sandcat agent to check in ..."
for i in $(seq 1 40); do
    AGENTS=$(curl -sf -H "KEY:${API_KEY}" "${CALDERA_URL}/api/v2/agents" 2>/dev/null || echo "[]")
    AGENT_COUNT=$(echo "${AGENTS}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    if [ "${AGENT_COUNT}" -gt 0 ]; then
        AGENT_PAW=$(echo "${AGENTS}" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['paw'])" 2>/dev/null)
        if [ -z "${AGENT_PAW}" ]; then fail "Could not read the agent paw"; fi
        pass "Agent registered (paw=${AGENT_PAW})"
        break
    fi
    if [ "$i" -eq 40 ]; then fail "No agent registered after 40 attempts"; fi
    sleep 5
done

# ── Step 4: Find the Modbus read ability ────────────────────────────
info "Looking for the Modbus Read Holding Registers ability ..."
ABILITIES=$(curl -sf -H "KEY:${API_KEY}" "${CALDERA_URL}/api/v2/abilities" 2>/dev/null || echo "[]")
ABILITY_ID=$(echo "${ABILITIES}" | python3 -c "
import sys, json
for a in json.load(sys.stdin):
    if a.get('name', '').lower() == 'modbus - read holding registers':
        print(a['ability_id'])
        break
else:
    print('')
" 2>/dev/null)
if [ -z "${ABILITY_ID}" ]; then
    fail "Could not find the Modbus Read Holding Registers ability. Is the Modbus plugin loaded?"
fi
pass "Found Modbus Read Holding Registers ability (id=${ABILITY_ID})"

# ── Step 5: Create or update adversary profile ──────────────────────
info "Creating adversary profile ..."
ADVERSARY_ID="smoke-test-adversary"
ADVERSARY_RESP=$(curl -sf -X POST -H "KEY:${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Smoke Test Modbus\",
        \"description\": \"Automated smoke test adversary\",
        \"adversary_id\": \"${ADVERSARY_ID}\",
        \"atomic_ordering\": [\"${ABILITY_ID}\"],
        \"objective\": \"default\"
    }" \
    "${CALDERA_URL}/api/v2/adversaries" 2>/dev/null \
  || curl -sf -X PATCH -H "KEY:${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Smoke Test Modbus\",
        \"atomic_ordering\": [\"${ABILITY_ID}\"]
    }" \
    "${CALDERA_URL}/api/v2/adversaries/${ADVERSARY_ID}" 2>/dev/null \
  || echo "{}")
if [ -z "$(echo "${ADVERSARY_RESP}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('adversary_id',''))" 2>/dev/null)" ]; then
    fail "Failed to create/update adversary. Response: ${ADVERSARY_RESP}"
fi
pass "Adversary profile ready (id=${ADVERSARY_ID})"

# ── Step 6: Create and run an operation ─────────────────────────────
info "Creating fact source ..."
PLC_IP=$(curl -sf -H "KEY:${API_KEY}" "${CALDERA_URL}/api/v2/sources/${FACTS_ID}" 2>/dev/null | python3 -c "
import sys, json
print(next(f['value'] for f in json.load(sys.stdin)['facts'] if f['trait'] == 'modbus.server.ip'))
" 2>/dev/null || echo "")
if [ -z "${PLC_IP}" ]; then
    fail "Could not read modbus.server.ip from the Wildcat Dam fact source"
fi
SOURCE_ID="smoke-test-source"
curl -sf -X PUT -H "KEY:${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
        \"id\": \"${SOURCE_ID}\",
        \"name\": \"Modbus Smoke Test Source\",
        \"facts\": [
            {\"trait\": \"modbus.server.ip\", \"value\": \"${PLC_IP}\"},
            {\"trait\": \"modbus.server.port\", \"value\": \"${PLC_PORT}\"},
            {\"trait\": \"modbus.read_holding.start\", \"value\": \"0\"},
            {\"trait\": \"modbus.read_holding.count\", \"value\": \"7\"}
        ]
    }" \
    "${CALDERA_URL}/api/v2/sources/${SOURCE_ID}" >/dev/null 2>&1 || fail "Failed to create the fact source"
pass "Fact source ready (id=${SOURCE_ID}, plc=${PLC_IP})"

info "Creating operation ..."
OP_RESP=$(curl -sf -X POST -H "KEY:${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Modbus Smoke Test\",
        \"adversary\": {\"adversary_id\": \"${ADVERSARY_ID}\"},
        \"group\": \"red\",
        \"auto_close\": true,
        \"source\": {\"id\": \"${SOURCE_ID}\"}
    }" \
    "${CALDERA_URL}/api/v2/operations" 2>/dev/null || echo "{}")
OP_ID=$(echo "${OP_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [ -z "${OP_ID}" ]; then
    fail "Failed to create operation. Response: ${OP_RESP}"
fi
pass "Operation created (id=${OP_ID})"

# ── Step 7: Wait for the read to run ────────────────────────────────
info "Waiting for the read to run ..."
for i in $(seq 1 60); do
    OP_STATUS=$(curl -sf -H "KEY:${API_KEY}" \
        "${CALDERA_URL}/api/v2/operations/${OP_ID}" 2>/dev/null || echo "{}")
    LINK_ID=$(echo "${OP_STATUS}" | python3 -c "
import sys, json
done = [l for l in json.load(sys.stdin).get('chain', []) if l.get('finish')]
print(done[0]['id'] if done else '')
" 2>/dev/null || echo "")
    if [ -n "${LINK_ID}" ]; then
        pass "Read ran (link=${LINK_ID})"
        break
    fi
    if [ "$i" -eq 60 ]; then
        fail "The read did not run after 60 attempts (check the fact source)"
    fi
    sleep 5
done
curl -sf -X PATCH -H "KEY:${API_KEY}" -H "Content-Type: application/json" \
    -d '{"state": "finished"}' "${CALDERA_URL}/api/v2/operations/${OP_ID}" >/dev/null 2>&1 || true

# ── Step 8: Check the read returned values ──────────────────────────
OUTPUT=$(curl -sf -H "KEY:${API_KEY}" \
    "${CALDERA_URL}/api/v2/operations/${OP_ID}/links/${LINK_ID}/result" 2>/dev/null | python3 -c "
import sys, json, base64
result = json.loads(base64.b64decode(json.load(sys.stdin).get('result', '')).decode(errors='replace'))
print(result.get('exit_code', ''))
print(result.get('stdout', '').strip())
" 2>/dev/null || echo "")
EXIT_CODE=$(echo "${OUTPUT}" | head -1)
VALUES=$(echo "${OUTPUT}" | tail -n +2)
if [ "${EXIT_CODE}" != "0" ] || [ -z "${VALUES}" ] || echo "${VALUES}" | grep -qi "error"; then
    fail "Read Holding Registers failed (exit ${EXIT_CODE}): ${VALUES}"
fi
pass "Read Holding Registers returned values"

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo "============================================"
pass "Smoke test passed -- end-to-end integration verified"
echo "============================================"
echo ""
echo "  Caldera UI:    ${CALDERA_URL}"
echo "  Wildcat Dam:   ${PLC_HOST}:${PLC_PORT} (Modbus)"
echo "  Agent paw:     ${AGENT_PAW}"
echo "  Operation ID:  ${OP_ID}"
echo ""
