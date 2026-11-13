#!/bin/bash
# End-to-end smoke test for the simulator and Caldera for OT.
#
# Usage: bash docker/smoke-test.sh
#
# This script:
#   1. Builds and starts the Docker lab, with the profiles in smoke-test.conf
#   2. Waits for each PLC to come up
#   3. Waits for Caldera to be reachable
#   4. Waits for a Sandcat agent to check in
#   Then, for each protocol in smoke-test.conf:
#   5. Finds the read ability in Caldera
#   6. Creates an adversary that runs it
#   7. Creates a fact source from the shipped one and starts an operation
#   8. Waits for the ability to run
#   9. Checks that the read succeeded and returned values
#
# Everything specific to this simulator is in docker/smoke-test.conf.
# Overrides: CALDERA_URL (default http://localhost:8888) and SIM_HOST, the host
# the PLC ports are published on (default localhost).
set -euo pipefail

DIR="$(dirname "$0")"
. "${DIR}/smoke-test.conf"

PY=python3
"$PY" -c "" 2>/dev/null || PY=python
export PYTHONIOENCODING=utf-8

CALDERA_URL="${CALDERA_URL:-http://localhost:8888}"
SIM_HOST="${SIM_HOST:-localhost}"
API_KEY="ADMIN123"  # matches api_key_red in conf/default.yml
AGENT_PAW=""
SUMMARY=""

COMPOSE=(docker compose -f "${DIR}/docker-compose.yml")
for profile in ${PROFILES:-}; do
    COMPOSE+=(--profile "${profile}")
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# Windows Python ends lines with \r; strip it so Git Bash can compare values.
py() { "$PY" -c "$@" | tr -d '\r'; }
api() { curl -sf -H "KEY:${API_KEY}" "$@"; }

load_target() {
    SERVICE=""
    PORT=""
    ABILITY=""
    SOURCE_FACTS=""
    FACTS=""
    EXPECT=""
    "target_$1"
}

# -- Step 1: Start the lab ---------------------------------------------
info "Building and starting the lab (profiles: ${PROFILES:-none}) ..."
"${COMPOSE[@]}" up --build -d || fail "docker compose up failed"
pass "Lab started"

# -- Step 2: Wait for each PLC -----------------------------------------
for target in ${TARGETS}; do
    load_target "${target}"
    info "Waiting for the ${target} PLC (service ${SERVICE}) ..."
    for i in $(seq 1 30); do
        if "${COMPOSE[@]}" logs "${SERVICE}" 2>/dev/null | grep "listening on" >/dev/null; then
            break
        fi
        if [ "$i" -eq 30 ]; then
            "${COMPOSE[@]}" logs --tail 20 "${SERVICE}" 2>&1 || true
            fail "${SERVICE} did not log 'listening on' after 30 attempts"
        fi
        sleep 2
    done
    if [ -n "${PORT}" ]; then
        PUBLISHED=$("${COMPOSE[@]}" port "${SERVICE}" "${PORT}" 2>/dev/null | tr -d '\r' || true)
        if [ -z "${PUBLISHED}" ]; then
            fail "${SERVICE} port ${PORT} is not published to the host"
        fi
        HOST_PORT="${PUBLISHED##*:}"
        for i in $(seq 1 30); do
            if "$PY" -c "import socket, sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 2).close()" \
                "${SIM_HOST}" "${HOST_PORT}" 2>/dev/null; then
                break
            fi
            if [ "$i" -eq 30 ]; then fail "${SERVICE} not reachable at ${SIM_HOST}:${HOST_PORT} after 30 attempts"; fi
            sleep 2
        done
        pass "${target} PLC is up and reachable at ${SIM_HOST}:${HOST_PORT}"
    else
        pass "${target} PLC is up"
    fi
done

# -- Step 3: Wait for Caldera ------------------------------------------
info "Waiting for Caldera at ${CALDERA_URL} ..."
for i in $(seq 1 60); do
    if curl -sf "${CALDERA_URL}" >/dev/null 2>&1; then
        pass "Caldera server is reachable"
        break
    fi
    if [ "$i" -eq 60 ]; then fail "Caldera not reachable after 60 attempts"; fi
    sleep 3
done

# -- Step 4: Wait for an agent -----------------------------------------
info "Waiting for a Sandcat agent to check in ..."
for i in $(seq 1 40); do
    AGENTS=$(api "${CALDERA_URL}/api/v2/agents" 2>/dev/null || echo "[]")
    AGENT_COUNT=$(echo "${AGENTS}" | py "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    if [ "${AGENT_COUNT}" -gt 0 ]; then
        AGENT_PAW=$(echo "${AGENTS}" | py "import sys, json; print(json.load(sys.stdin)[0]['paw'])" 2>/dev/null || echo "")
        if [ -z "${AGENT_PAW}" ]; then fail "Could not read the agent paw"; fi
        pass "Agent registered (paw=${AGENT_PAW})"
        break
    fi
    if [ "$i" -eq 40 ]; then fail "No agent registered after 40 attempts"; fi
    sleep 5
done

SOURCE=$(api "${CALDERA_URL}/api/v2/sources/${FACTS_ID}" 2>/dev/null || echo "")
if [ -z "${SOURCE}" ]; then
    fail "Could not read the ${SIM_NAME} fact source (id=${FACTS_ID}) from Caldera"
fi

for target in ${TARGETS}; do
    load_target "${target}"

    # -- Step 5: Find the ability --------------------------------------
    info "Looking for the '${ABILITY}' ability ..."
    ABILITIES=$(api "${CALDERA_URL}/api/v2/abilities" 2>/dev/null || echo "[]")
    ABILITY_ID=$(echo "${ABILITIES}" | py "
import sys, json
name = sys.argv[1].lower()
print(next((a['ability_id'] for a in json.load(sys.stdin) if a.get('name', '').lower() == name), ''))
" "${ABILITY}" 2>/dev/null || echo "")
    if [ -z "${ABILITY_ID}" ]; then
        info "'${ABILITY}' not found -- listing available ${target} abilities:"
        echo "${ABILITIES}" | py "
import sys, json
for a in json.load(sys.stdin):
    if sys.argv[1] in (a.get('plugin') or '').lower() or sys.argv[1] in a.get('name', '').lower():
        print(f\"  {a['ability_id']}  {a['name']}\")
" "${target}" 2>/dev/null || true
        fail "Could not find the '${ABILITY}' ability. Is the ${target} plugin loaded?"
    fi
    pass "Found '${ABILITY}' (id=${ABILITY_ID})"

    # -- Step 6: Create or update the adversary profile ----------------
    info "Creating adversary profile ..."
    ADVERSARY_ID="smoke-test-${target}"
    ADVERSARY_RESP=$(api -X POST -H "Content-Type: application/json" \
        -d "{
            \"name\": \"Smoke Test ${target}\",
            \"description\": \"Automated smoke test adversary\",
            \"adversary_id\": \"${ADVERSARY_ID}\",
            \"atomic_ordering\": [\"${ABILITY_ID}\"],
            \"objective\": \"default\"
        }" \
        "${CALDERA_URL}/api/v2/adversaries" 2>/dev/null \
      || api -X PATCH -H "Content-Type: application/json" \
        -d "{
            \"name\": \"Smoke Test ${target}\",
            \"atomic_ordering\": [\"${ABILITY_ID}\"]
        }" \
        "${CALDERA_URL}/api/v2/adversaries/${ADVERSARY_ID}" 2>/dev/null \
      || echo "{}")
    if [ -z "$(echo "${ADVERSARY_RESP}" | py "import sys, json; print(json.load(sys.stdin).get('adversary_id', ''))" 2>/dev/null)" ]; then
        fail "Failed to create/update adversary. Response: ${ADVERSARY_RESP}"
    fi
    pass "Adversary profile ready (id=${ADVERSARY_ID})"

    # -- Step 7: Create the fact source and run an operation -----------
    info "Creating fact source ..."
    SOURCE_ID="smoke-test-${target}"
    SOURCE_BODY=$(echo "${SOURCE}" | py "
import sys, json
source_id, name, copied, extra = sys.argv[1:5]
shipped = json.load(sys.stdin).get('facts', [])
facts = []
for trait in copied.split():
    value = next((f['value'] for f in shipped if f['trait'] == trait), None)
    if value is None:
        sys.exit(f'no {trait} fact in the shipped fact source')
    facts.append({'trait': trait, 'value': str(value)})
for pair in extra.split():
    trait, value = pair.split('=', 1)
    facts.append({'trait': trait, 'value': value})
print(json.dumps({'id': source_id, 'name': name, 'facts': facts}))
" "${SOURCE_ID}" "Smoke Test ${target}" "${SOURCE_FACTS}" "${FACTS}") \
        || fail "Could not build the fact source from the ${SIM_NAME} fact source"
    api -X PUT -H "Content-Type: application/json" -d "${SOURCE_BODY}" \
        "${CALDERA_URL}/api/v2/sources/${SOURCE_ID}" >/dev/null 2>&1 || fail "Failed to create the fact source"
    pass "Fact source ready (id=${SOURCE_ID})"

    info "Creating operation ..."
    OP_RESP=$(api -X POST -H "Content-Type: application/json" \
        -d "{
            \"name\": \"Smoke Test ${target}\",
            \"adversary\": {\"adversary_id\": \"${ADVERSARY_ID}\"},
            \"group\": \"red\",
            \"auto_close\": true,
            \"source\": {\"id\": \"${SOURCE_ID}\"}
        }" \
        "${CALDERA_URL}/api/v2/operations" 2>/dev/null || echo "{}")
    OP_ID=$(echo "${OP_RESP}" | py "import sys, json; print(json.load(sys.stdin).get('id', ''))" 2>/dev/null || echo "")
    if [ -z "${OP_ID}" ]; then
        fail "Failed to create operation. Response: ${OP_RESP}"
    fi
    pass "Operation created (id=${OP_ID})"

    # -- Step 8: Wait for the ability to run ---------------------------
    info "Waiting for '${ABILITY}' to run ..."
    for i in $(seq 1 60); do
        OP_STATUS=$(api "${CALDERA_URL}/api/v2/operations/${OP_ID}" 2>/dev/null || echo "{}")
        LINK=$(echo "${OP_STATUS}" | py "
import sys, json
op = json.load(sys.stdin)
done = [l for l in op.get('chain', []) if l.get('finish')]
if done:
    print(done[0]['id'], done[0].get('status', ''))
elif op.get('state') in ('finished', 'cleanup'):
    print('none')
" 2>/dev/null || echo "")
        if [ "${LINK}" = "none" ]; then
            fail "Operation ran no abilities (check the fact source)"
        fi
        if [ -n "${LINK}" ]; then
            read -r LINK_ID LINK_STATUS <<< "${LINK}"
            pass "'${ABILITY}' ran (link=${LINK_ID})"
            break
        fi
        if [ "$i" -eq 60 ]; then
            fail "'${ABILITY}' did not run after 60 attempts (check the fact source)"
        fi
        sleep 5
    done
    api -X PATCH -H "Content-Type: application/json" -d '{"state": "finished"}' \
        "${CALDERA_URL}/api/v2/operations/${OP_ID}" >/dev/null 2>&1 || true

    # -- Step 9: Check the result --------------------------------------
    RESULT=$(api "${CALDERA_URL}/api/v2/operations/${OP_ID}/links/${LINK_ID}/result" 2>/dev/null | py "
import sys, json, base64
result = json.loads(base64.b64decode(json.load(sys.stdin).get('result', '')).decode(errors='replace'))
print(result.get('exit_code', ''))
print(result.get('stdout', '').strip())
" 2>/dev/null || echo "")
    EXIT_CODE=$(printf '%s\n' "${RESULT}" | sed -n '1p')
    OUTPUT=$(printf '%s\n' "${RESULT}" | sed -n '2,$p')
    if [ "${LINK_STATUS}" != "0" ] || [ "${EXIT_CODE}" != "0" ]; then
        fail "'${ABILITY}' failed (status ${LINK_STATUS}, exit ${EXIT_CODE}): ${OUTPUT}"
    fi
    if [ -z "${OUTPUT}" ] || printf '%s\n' "${OUTPUT}" | grep -i "error" >/dev/null; then
        fail "'${ABILITY}' returned an error or no output: ${OUTPUT}"
    fi
    if [ -n "${EXPECT}" ] && ! printf '%s\n' "${OUTPUT}" | grep -E -e "${EXPECT}" >/dev/null; then
        fail "'${ABILITY}' output does not match '${EXPECT}': ${OUTPUT}"
    fi
    pass "'${ABILITY}' returned values"
    SUMMARY="${SUMMARY}  ${target}: service ${SERVICE}, operation ${OP_ID}"$'\n'
done

# -- Summary -----------------------------------------------------------
echo ""
echo "============================================"
pass "Smoke test passed -- end-to-end integration verified"
echo "============================================"
echo ""
echo "  Simulator:  ${SIM_NAME}"
echo "  Caldera UI: ${CALDERA_URL}"
echo "  Agent paw:  ${AGENT_PAW}"
printf '%s' "${SUMMARY}"
echo ""
