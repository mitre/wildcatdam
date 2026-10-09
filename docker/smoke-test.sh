#!/bin/bash
# End-to-end smoke test for the Caldera OT + Modbus dam integration.
#
# Prerequisites: docker compose up --build  (all services running)
#
# This script:
#   1. Waits for the sim and Caldera to be healthy
#   2. Waits for a Sandcat agent to check in
#   3. Creates an adversary with a Modbus read ability
#   4. Runs an operation and waits for it to finish
#   5. Reports pass/fail
set -euo pipefail

CALDERA_URL="${CALDERA_URL:-http://localhost:8888}"
SIM_HOST="${SIM_HOST:-localhost}"
SIM_PORT="${SIM_PORT:-5020}"
API_KEY="ADMIN123"  # matches api_key_red in conf/default.yml
AGENT_PAW=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# ── Step 1: Wait for sim ─────────────────────────────────────────────
info "Waiting for Modbus sim at ${SIM_HOST}:${SIM_PORT} ..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.create_connection(('${SIM_HOST}',${SIM_PORT}),2); s.close()" 2>/dev/null; then
        pass "Sim is reachable"
        break
    fi
    if [ "$i" -eq 30 ]; then fail "Sim not reachable after 30 attempts"; fi
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
        pass "Agent registered (paw=${AGENT_PAW})"
        break
    fi
    if [ "$i" -eq 40 ]; then fail "No agent registered after 40 attempts"; fi
    sleep 5
done

# ── Step 4: Find a Modbus read ability ──────────────────────────────
info "Looking for a Modbus read ability ..."
ABILITIES=$(curl -sf -H "KEY:${API_KEY}" "${CALDERA_URL}/api/v2/abilities" 2>/dev/null || echo "[]")
ABILITY_ID=$(echo "${ABILITIES}" | python3 -c "
import sys, json
abilities = json.load(sys.stdin)
for a in abilities:
    name = a.get('name', '').lower()
    if 'modbus' in name and 'read' in name:
        print(a['ability_id'])
        break
else:
    print('')
" 2>/dev/null)

if [ -z "${ABILITY_ID}" ]; then
    info "Modbus read ability not found -- listing available Modbus abilities:"
    echo "${ABILITIES}" | python3 -c "
import sys, json
abilities = json.load(sys.stdin)
for a in abilities:
    if 'modbus' in a.get('name', '').lower() or 'modbus' in a.get('tactic', '').lower():
        print(f\"  {a['ability_id']}  {a['name']}\")
" 2>/dev/null || true
    fail "Could not find a Modbus read ability. Is the modbus plugin loaded?"
fi
pass "Found Modbus read ability (id=${ABILITY_ID})"

# ── Step 5: Create or update adversary profile ──────────────────────
info "Creating adversary profile ..."
ADVERSARY_ID="smoke-test-adversary"
# Try POST first; if the adversary already exists (re-run), PATCH to update it.
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
info "Creating operation ..."
OP_RESP=$(curl -sf -X POST -H "KEY:${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{
        \"name\": \"Modbus Smoke Test\",
        \"adversary\": {\"adversary_id\": \"${ADVERSARY_ID}\"},
        \"group\": \"red\",
        \"auto_close\": true,
        \"source\": {
            \"name\": \"Modbus Smoke Test Source\",
            \"facts\": [
                {\"trait\": \"modbus.server.ip\", \"value\": \"sim\"},
                {\"trait\": \"modbus.server.port\", \"value\": \"5020\"},
                {\"trait\": \"modbus.read_holding.start\", \"value\": \"0\"},
                {\"trait\": \"modbus.read_holding.count\", \"value\": \"7\"}
            ]
        }
    }" \
    "${CALDERA_URL}/api/v2/operations" 2>/dev/null || echo "{}")

OP_ID=$(echo "${OP_RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")

if [ -z "${OP_ID}" ]; then
    fail "Failed to create operation. Response: ${OP_RESP}"
fi
pass "Operation created (id=${OP_ID})"

# ── Step 7: Wait for the operation to finish ────────────────────────
info "Waiting for operation to complete ..."
for i in $(seq 1 60); do
    OP_STATUS=$(curl -sf -H "KEY:${API_KEY}" \
        "${CALDERA_URL}/api/v2/operations/${OP_ID}" 2>/dev/null || echo "{}")
    STATE=$(echo "${OP_STATUS}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','unknown'))" 2>/dev/null || echo "unknown")

    if [ "${STATE}" = "finished" ] || [ "${STATE}" = "cleanup" ]; then
        pass "Operation finished (state=${STATE})"
        break
    fi
    if [ "$i" -eq 60 ]; then
        fail "Operation did not finish after 60 attempts (state=${STATE})"
    fi
    sleep 5
done

# ── Summary ─────────────────────────────────────────────────────────
echo ""
echo "============================================"
pass "Smoke test passed -- end-to-end integration verified"
echo "============================================"
echo ""
echo "  Caldera UI:   ${CALDERA_URL}"
echo "  Sim:          ${SIM_HOST}:${SIM_PORT}"
echo "  Agent paw:    ${AGENT_PAW}"
echo "  Operation ID: ${OP_ID}"
echo ""
