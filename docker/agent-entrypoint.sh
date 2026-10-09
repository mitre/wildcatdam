#!/bin/bash
# Entrypoint for the Sandcat agent container.
# Waits for the Caldera server to become reachable, downloads the
# Sandcat agent binary, and runs it.
set -e

CALDERA_SERVER="${CALDERA_SERVER:-http://caldera:8888}"
AGENT_GROUP="${AGENT_GROUP:-red}"

echo "[agent] Waiting for Caldera server at ${CALDERA_SERVER} ..."
WAIT=0
until curl -sf "${CALDERA_SERVER}" >/dev/null 2>&1; do
    WAIT=$((WAIT + 1))
    if [ "${WAIT}" -ge 40 ]; then
        echo "[agent] Caldera server not reachable after 40 attempts - giving up"
        exit 1
    fi
    sleep 3
done
echo "[agent] Caldera server is reachable."

echo "[agent] Downloading Sandcat agent ..."
curl -s -X POST \
    -H "platform:linux" \
    -H "file:sandcat.go" \
    -H "server:${CALDERA_SERVER}" \
    -H "group:${AGENT_GROUP}" \
    "${CALDERA_SERVER}/file/download" \
    -o /tmp/sandcat

chmod +x /tmp/sandcat

echo "[agent] Starting Sandcat agent (server=${CALDERA_SERVER}, group=${AGENT_GROUP}) ..."
exec /tmp/sandcat -server "${CALDERA_SERVER}" -group "${AGENT_GROUP}" -v
